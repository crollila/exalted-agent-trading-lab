"""Pure, testable data aggregation for the Arena Command Center (Phase 7Q).

No Streamlit imports. Every function here is read-only: it reuses the same safe
loaders the CLI uses (scorecards, attribution, cheap-cycle gate, daily SPY
attribution, daily reviews, strategy memory, LLM routing/review status) and never
submits an order, reads a secret value, or bypasses a safety gate. Missing data
degrades to ``None`` / empty rather than raising.

The Arena surfaces the new 7L–7P features:

* 7L attribution outcomes (worked/failed/mixed/pending),
* 7M PortfolioManager decision (decision_type / no_trade / max_new_proposals),
* 7N cheap-cycle gate + daily SPY attribution + daily review artifacts,
* 7O LLM model routing,
* 7P advisory review agents / team debate / strategy memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.agents.llm_review_agents import LLMReviewFlags, review_status
from src.agents.model_routing import routing_status
from src.competition.attribution import performance_feedback
from src.competition.cycle_gate import (
    CheapCycleGateConfig,
    GateDecision,
    evaluate_cheap_cycle_gate,
)
from src.competition.portfolio_manager import PortfolioManagerConfig
from src.competition.scorecard import TeamScorecard, load_latest_scorecard
from src.learning.team_memory import TeamLearningLedger

# Clear, non-deceptive label applied to every demo/sample value. Operator Mode never
# uses this — it shows only real local runtime state.
DEMO_LABEL = "DEMO / SAMPLE DATA — not real"

ARENA_TEAMS: tuple[str, ...] = ("team_alpha", "team_beta")

# Static agent roster per team. Agents never have direct trade permissions.
AGENT_ROSTER: tuple[tuple[str, str], ...] = (
    ("research", "Research Agent"),
    ("risk", "Risk Agent"),
    ("review", "Review Agent"),
    ("portfolio_manager", "Portfolio Manager"),
    ("llm_review", "LLM Review / Debate"),
)


def safe_get(obj: object, *names: str) -> Any:
    """Read the first present attribute/key from an object or dict."""

    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


# ---------------------------------------------------------------------------
# Cheap-cycle gate (local signals only; mirrors main._evaluate_team_cheap_gate)
# ---------------------------------------------------------------------------
def _low_buying_power_from_scorecard(scorecard: TeamScorecard | None, threshold: float) -> bool:
    if scorecard is None or scorecard.buying_power is None:
        return False
    equity = scorecard.starting_equity or 0.0
    if equity <= 0:
        return False
    return (scorecard.buying_power / equity) < threshold


def evaluate_arena_cheap_gate(
    team_id: str,
    *,
    scorecard: TeamScorecard | None = None,
    ledger: TeamLearningLedger | None = None,
    feedback: Mapping[str, Any] | None = None,
    gate_config: CheapCycleGateConfig | None = None,
    pm_config: PortfolioManagerConfig | None = None,
) -> GateDecision:
    """Evaluate the cheap-cycle gate from local signals only (no LLM / broker / net)."""

    gate_config = gate_config or CheapCycleGateConfig.from_env()
    pm_config = pm_config or PortfolioManagerConfig.from_env()
    ledger = ledger if ledger is not None else TeamLearningLedger.load(team_id)
    if scorecard is None:
        scorecard = load_latest_scorecard(team_id)
    if feedback is None:
        try:
            feedback = performance_feedback(team_id)
        except Exception:  # noqa: BLE001 - missing/old attribution must not crash the gate
            feedback = {}
    outcome = feedback.get("outcome_feedback", {}) if isinstance(feedback, dict) else {}

    if scorecard is not None and scorecard.broker_rejected_count:
        broker_rejections = scorecard.broker_rejected_count
    else:
        broker_rejections = len(outcome.get("recent_broker_rejections", []) or [])
    low_bp = _low_buying_power_from_scorecard(scorecard, pm_config.low_buying_power_review_threshold_pct)

    return evaluate_cheap_cycle_gate(
        team_id,
        config=gate_config,
        last_full_cycle_at=(ledger.last_full_cycle_at or None) if ledger else None,
        spy_move_pct=None,
        low_buying_power=low_bp,
        broker_rejections=broker_rejections,
        research_changed=False,
        urgent_review=low_bp,
        mode=ledger.mode if ledger else "",
    )


# ---------------------------------------------------------------------------
# Attribution outcome summary (7L)
# ---------------------------------------------------------------------------
def attribution_outcome_summary(feedback: Mapping[str, Any] | None) -> dict[str, int]:
    """Compact worked/failed/mixed/pending counts from performance feedback."""

    outcome = (feedback or {}).get("outcome_feedback", {}) if isinstance(feedback, dict) else {}
    return {
        "worked": int(outcome.get("worked_count", 0) or 0),
        "failed": int(outcome.get("failed_count", 0) or 0),
        "mixed": int(outcome.get("mixed_count", 0) or 0),
        "pending": int(outcome.get("pending_count", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Per-team Arena snapshot
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TeamArenaSnapshot:
    team_id: str
    is_demo: bool = False
    # Paper account (n/a when unavailable; never invented in Operator Mode).
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    daily_pl: float | None = None
    positions_count: int | None = None
    account_available: bool = False
    account_message: str = ""
    # Competition / scorecard.
    team_return: float | None = None
    spy_return: float | None = None
    excess_return: float | None = None
    rank: int | None = None
    # Latest proposal routing counts (7F).
    execution_eligible_count: int = 0
    simulation_only_count: int = 0
    rejected_count: int = 0
    broker_rejected_count: int = 0
    # Attribution outcomes (7L).
    attribution: dict[str, int] = field(default_factory=lambda: {"worked": 0, "failed": 0, "mixed": 0, "pending": 0})
    # Portfolio Manager decision (7M).
    pm_decision_type: str | None = None
    pm_no_trade: bool = False
    pm_max_new_proposals: int | None = None
    pm_rationale: str = ""
    # Cheap-cycle gate (7N).
    gate_should_run_full_cycle: bool | None = None
    gate_recommended_wait_minutes: int = 0
    gate_recommend_review_only: bool = False
    gate_trigger_flags: tuple[str, ...] = ()
    gate_reason: str = ""
    # Approvals.
    risk_approved: bool = False
    review_approved: bool = False
    # Mode / strategy memory (7P).
    mode: str = ""
    recommended_mode: str = ""
    confidence: float | None = None

    @property
    def beats_spy(self) -> bool | None:
        if self.excess_return is None:
            return None
        return self.excess_return > 0


def _pm_rationale_from_ledger(ledger: TeamLearningLedger | None) -> str:
    if ledger is None:
        return ""
    for change in reversed(ledger.strategy_changes):
        if isinstance(change, str) and change.lower().startswith("portfolio manager"):
            return change
    return ""


def build_team_arena_snapshot(
    team_id: str,
    *,
    portfolio_snapshot: object | None = None,
    scorecard: TeamScorecard | None = None,
    feedback: Mapping[str, Any] | None = None,
    team_status: object | None = None,
    ledger: TeamLearningLedger | None = None,
    gate: GateDecision | None = None,
    is_demo: bool = False,
) -> TeamArenaSnapshot:
    """Aggregate a team's full Arena snapshot from local data. Missing data is safe.

    All inputs are injectable so this is fully unit-testable offline. When an input is
    omitted, the corresponding safe loader runs (and degrades to empty on failure).
    """

    if scorecard is None:
        try:
            scorecard = load_latest_scorecard(team_id)
        except Exception:  # noqa: BLE001
            scorecard = None
    if feedback is None:
        try:
            feedback = performance_feedback(team_id)
        except Exception:  # noqa: BLE001
            feedback = {}
    if ledger is None:
        try:
            ledger = TeamLearningLedger.load(team_id)
        except Exception:  # noqa: BLE001
            ledger = None
    if gate is None:
        try:
            gate = evaluate_arena_cheap_gate(
                team_id, scorecard=scorecard, ledger=ledger, feedback=feedback
            )
        except Exception:  # noqa: BLE001
            gate = None

    # Paper account snapshot (already-fetched; never forces a broker call here).
    equity = cash = buying_power = daily_pl = None
    positions_count = None
    account_available = False
    account_message = "paper account not loaded"
    if portfolio_snapshot is not None:
        account_available = bool(safe_get(portfolio_snapshot, "available"))
        account_message = str(safe_get(portfolio_snapshot, "message") or "")
        equity = safe_get(portfolio_snapshot, "equity")
        cash = safe_get(portfolio_snapshot, "cash")
        buying_power = safe_get(portfolio_snapshot, "buying_power")
        pos = safe_get(portfolio_snapshot, "positions_count")
        positions_count = int(pos) if pos is not None else None

    # Scorecard-derived competition metrics.
    team_return = spy_return = excess_return = None
    rank = None
    exec_eligible = sim_only = rejected = broker_rejected = 0
    pm_decision_type = None
    pm_no_trade = False
    pm_max_new = None
    if scorecard is not None:
        team_return = scorecard.team_return
        spy_return = scorecard.spy_benchmark_return
        excess_return = scorecard.excess_return_vs_spy
        rank = scorecard.current_rank
        exec_eligible = int(getattr(scorecard, "approved_count", 0) or 0)
        sim_only = int(getattr(scorecard, "simulation_only_count", 0) or 0)
        rejected = int(getattr(scorecard, "rejected_count", 0) or 0)
        broker_rejected = int(getattr(scorecard, "broker_rejected_count", 0) or 0)
        pm_decision_type = scorecard.portfolio_decision_type
        pm_no_trade = bool(scorecard.portfolio_no_trade)
        pm_max_new = scorecard.max_new_proposals

    # Prefer live routing counts from the latest proposal file when present.
    risk_approved = review_approved = False
    if team_status is not None:
        ts_exec = safe_get(team_status, "execution_eligible_count")
        ts_sim = safe_get(team_status, "simulation_only_count")
        ts_rej = safe_get(team_status, "rejected_count")
        if any(v is not None for v in (ts_exec, ts_sim, ts_rej)):
            exec_eligible = int(ts_exec or 0)
            sim_only = int(ts_sim or 0)
            rejected = int(ts_rej or 0)
        risk_approved = bool(safe_get(team_status, "risk_approved"))
        review_approved = bool(safe_get(team_status, "review_approved"))

    return TeamArenaSnapshot(
        team_id=team_id,
        is_demo=is_demo,
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        daily_pl=daily_pl,
        positions_count=positions_count,
        account_available=account_available,
        account_message=account_message,
        team_return=team_return,
        spy_return=spy_return,
        excess_return=excess_return,
        rank=rank,
        execution_eligible_count=exec_eligible,
        simulation_only_count=sim_only,
        rejected_count=rejected,
        broker_rejected_count=broker_rejected,
        attribution=attribution_outcome_summary(feedback),
        pm_decision_type=pm_decision_type,
        pm_no_trade=pm_no_trade,
        pm_max_new_proposals=pm_max_new,
        pm_rationale=_pm_rationale_from_ledger(ledger),
        gate_should_run_full_cycle=(gate.should_run_full_cycle if gate else None),
        gate_recommended_wait_minutes=(gate.recommended_wait_minutes if gate else 0),
        gate_recommend_review_only=(gate.recommend_review_only if gate else False),
        gate_trigger_flags=tuple(gate.trigger_flags) if gate else (),
        gate_reason=(gate.reason if gate else ""),
        risk_approved=risk_approved,
        review_approved=review_approved,
        mode=(ledger.mode if ledger else ""),
        recommended_mode="",
        confidence=None,
    )


def build_demo_snapshot(team_id: str) -> TeamArenaSnapshot:
    """A clearly-labeled sample snapshot for Demo Mode (never claims to be real)."""

    alpha = team_id == "team_alpha"
    return TeamArenaSnapshot(
        team_id=team_id,
        is_demo=True,
        equity=1_012_450.0 if alpha else 1_006_110.0,
        cash=410_000.0 if alpha else 540_000.0,
        buying_power=820_000.0 if alpha else 1_080_000.0,
        daily_pl=3_240.0 if alpha else -1_180.0,
        positions_count=4 if alpha else 2,
        account_available=True,
        account_message=DEMO_LABEL,
        team_return=0.01245 if alpha else 0.00611,
        spy_return=0.0082,
        excess_return=0.00425 if alpha else -0.00209,
        rank=1 if alpha else 2,
        execution_eligible_count=2 if alpha else 1,
        simulation_only_count=1,
        rejected_count=0,
        broker_rejected_count=0,
        attribution={"worked": 3 if alpha else 1, "failed": 1, "mixed": 0, "pending": 2},
        pm_decision_type="rotate" if alpha else "hold",
        pm_no_trade=not alpha,
        pm_max_new_proposals=2 if alpha else 0,
        pm_rationale=(
            "Portfolio manager: rotate into the strongest idea(s); capped at 2."
            if alpha
            else "Portfolio manager: hold; no new exposure justified this cycle."
        ),
        gate_should_run_full_cycle=alpha,
        gate_recommended_wait_minutes=0 if alpha else 18,
        gate_recommend_review_only=not alpha,
        gate_trigger_flags=("interval_elapsed",) if alpha else ("interval_not_elapsed",),
        gate_reason=(
            "Minimum interval elapsed; full cycle recommended."
            if alpha
            else "Nothing material changed; staying cheap."
        ),
        risk_approved=True,
        review_approved=alpha,
        mode="exploration" if alpha else "conservation",
        recommended_mode="exploration" if alpha else "conservation",
        confidence=0.62 if alpha else 0.5,
    )


# ---------------------------------------------------------------------------
# Scoreboard leader
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoreboardLeader:
    leader: str | None  # "team_alpha" | "team_beta" | "tie" | None
    headline: str
    lead_metric: float | None = None  # excess-vs-SPY gap (preferred) or equity gap
    lead_basis: str = ""  # "excess vs SPY" | "equity" | ""


def _leader_value(snapshot: TeamArenaSnapshot) -> tuple[float | None, str]:
    if snapshot.excess_return is not None:
        return snapshot.excess_return, "excess vs SPY"
    if snapshot.equity is not None:
        return snapshot.equity, "equity"
    return None, ""


def compute_scoreboard_leader(
    alpha: TeamArenaSnapshot,
    beta: TeamArenaSnapshot,
) -> ScoreboardLeader:
    """Decide which team leads this week, with a clear non-deceptive headline.

    Prefers excess-vs-SPY when available, else equity. Returns ``None`` leader with a
    "No leader yet" headline when neither team has comparable data.
    """

    a_val, a_basis = _leader_value(alpha)
    b_val, b_basis = _leader_value(beta)
    if a_val is None or b_val is None or a_basis != b_basis:
        return ScoreboardLeader(leader=None, headline="No leader yet")

    gap = a_val - b_val
    if abs(gap) < 1e-9:
        return ScoreboardLeader(leader="tie", headline="Dead heat — teams are tied", lead_metric=0.0, lead_basis=a_basis)
    if gap > 0:
        return ScoreboardLeader(
            leader="team_alpha",
            headline="Alpha leads",
            lead_metric=abs(gap),
            lead_basis=a_basis,
        )
    return ScoreboardLeader(
        leader="team_beta",
        headline="Beta leads",
        lead_metric=abs(gap),
        lead_basis=b_basis,
    )


# ---------------------------------------------------------------------------
# LLM routing / review status cards (never include key contents)
# ---------------------------------------------------------------------------
def llm_status_cards(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Combine model routing + advisory review status. Model NAMES + bool only."""

    routing = routing_status(env)
    review = review_status(env)
    return {
        "provider": routing["provider"],
        "api_key_configured": routing["api_key_configured"],
        "strategy_model": routing["strategy_model"],
        "review_model": routing["review_model"],
        "critique_model": routing["critique_model"],
        "summary_model": routing["summary_model"],
        "portfolio_manager_model": routing["portfolio_manager_model"],
        "research_synthesis_model": routing["research_synthesis_model"],
        "stages": review["stages"],
    }


# ---------------------------------------------------------------------------
# Intelligence feed / team brief
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FeedItem:
    team_id: str
    category: str
    text: str


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2%}"


def build_team_intelligence_brief(
    snapshot: TeamArenaSnapshot,
    *,
    daily_review: object | None = None,
    debate: Mapping[str, Any] | None = None,
    max_chars: int = 160,
) -> list[str]:
    """News-style sentences describing what a team is doing, from local state only."""

    from src.ui.arena_components import safe_truncate_text

    label = "Alpha" if snapshot.team_id == "team_alpha" else "Beta"
    prefix = f"[{DEMO_LABEL}] " if snapshot.is_demo else ""
    lines: list[str] = []

    if snapshot.excess_return is not None:
        verb = "beating" if snapshot.excess_return > 0 else ("trailing" if snapshot.excess_return < 0 else "matching")
        lines.append(f"{label} is {verb} SPY by {_fmt_pct(snapshot.excess_return)} this week.")
    if snapshot.pm_decision_type:
        decision = snapshot.pm_decision_type
        cap = snapshot.pm_max_new_proposals
        if snapshot.pm_no_trade:
            lines.append(f"Portfolio Manager decided NO-TRADE/{decision} — holding the book this cycle.")
        else:
            lines.append(f"Portfolio Manager decided {decision} (up to {cap} new idea(s) allowed).")
    attrib = snapshot.attribution
    if any(attrib.values()):
        lines.append(
            f"Attribution: {attrib['worked']} worked, {attrib['failed']} failed, "
            f"{attrib['mixed']} mixed, {attrib['pending']} pending."
        )
    if snapshot.risk_approved or snapshot.review_approved:
        lines.append(
            f"Risk agent {'approved' if snapshot.risk_approved else 'pending'}; "
            f"Review agent {'approved' if snapshot.review_approved else 'pending'}."
        )
    if snapshot.gate_should_run_full_cycle is not None:
        if snapshot.gate_should_run_full_cycle:
            lines.append("Cheap-cycle gate says a full cycle is warranted.")
        else:
            lines.append(
                f"Cheap-cycle gate says stay cheap (~{snapshot.gate_recommended_wait_minutes}m to next cycle)."
            )
    review_text = str(safe_get(daily_review, "why_vs_spy") or "") if daily_review is not None else ""
    if review_text:
        lines.append(f"Daily review says: {review_text}")
    if debate:
        warn = str(debate.get("bear_case") or "")
        if warn:
            lines.append(f"LLM debate (bear case): {warn}")
    # Next likely action.
    if snapshot.gate_should_run_full_cycle is False and snapshot.gate_recommend_review_only:
        lines.append("Next likely action: a cheap review-only pass (no new orders).")
    elif snapshot.gate_should_run_full_cycle:
        lines.append("Next likely action: a full proposal cycle when the bot runs.")

    return [prefix + safe_truncate_text(line, max_chars) for line in lines]


def build_intelligence_feed(
    snapshots: Sequence[TeamArenaSnapshot],
    *,
    daily_reviews: Mapping[str, object] | None = None,
    debates: Mapping[str, Mapping[str, Any]] | None = None,
    limit: int = 10,
    max_chars: int = 140,
) -> list[FeedItem]:
    """Build a compact live-intelligence feed from local team state (5–10 items)."""

    from src.ui.arena_components import safe_truncate_text

    daily_reviews = daily_reviews or {}
    debates = debates or {}
    items: list[FeedItem] = []
    for snapshot in snapshots:
        team = snapshot.team_id
        label = "Alpha" if team == "team_alpha" else "Beta"
        prefix = f"[{DEMO_LABEL}] " if snapshot.is_demo else ""

        if snapshot.pm_decision_type:
            verb = "NO-TRADE" if snapshot.pm_no_trade else snapshot.pm_decision_type
            items.append(FeedItem(team, "Portfolio Manager", prefix + safe_truncate_text(
                f"{label}: PM decided {verb} (cap {snapshot.pm_max_new_proposals}).", max_chars)))
        attrib = snapshot.attribution
        if any(attrib.values()):
            items.append(FeedItem(team, "Attribution", prefix + safe_truncate_text(
                f"{label}: outcomes worked={attrib['worked']} failed={attrib['failed']} "
                f"mixed={attrib['mixed']} pending={attrib['pending']}.", max_chars)))
        if snapshot.broker_rejected_count:
            items.append(FeedItem(team, "Broker", prefix + safe_truncate_text(
                f"{label}: {snapshot.broker_rejected_count} broker rejection(s) recorded.", max_chars)))
        if snapshot.gate_reason:
            items.append(FeedItem(team, "Cheap gate", prefix + safe_truncate_text(
                f"{label}: {snapshot.gate_reason}", max_chars)))
        if snapshot.excess_return is not None:
            items.append(FeedItem(team, "Scoreboard", prefix + safe_truncate_text(
                f"{label}: excess vs SPY {_fmt_pct(snapshot.excess_return)} "
                f"(return {_fmt_pct(snapshot.team_return)}).", max_chars)))
        review = daily_reviews.get(team)
        review_text = str(safe_get(review, "why_vs_spy") or "") if review is not None else ""
        if review_text:
            items.append(FeedItem(team, "Daily review", prefix + safe_truncate_text(
                f"{label}: {review_text}", max_chars)))
        debate = debates.get(team)
        if debate:
            note = str(debate.get("trade_hold_or_observe") or "")
            if note:
                items.append(FeedItem(team, "LLM debate", prefix + safe_truncate_text(
                    f"{label}: debate recommendation — {note}.", max_chars)))

    return items[:limit]


def arena_timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
