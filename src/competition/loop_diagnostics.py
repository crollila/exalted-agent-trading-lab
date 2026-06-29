"""Non-trading diagnostic for the cheap competition loop (Phase 7U).

Answers "why did the autonomous paper loop stop placing orders?" using only
read-only, local, and already-fetched signals. This module is deterministic and
pure: callers gather the live facts (account snapshot, market clock, today's
order count, latest scorecard, ledger, kill-switch state) and pass them in; the
module classifies the per-team blocker and renders a human-readable report.

It NEVER generates proposals, calls an LLM, or submits orders. The deterministic
risk engine and kill switch remain authoritative; nothing here can trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# --- Final per-team diagnosis values (stable strings used by tests + ops). ---
READY = "READY"
MARKET_CLOSED = "MARKET_CLOSED"
CONFIG_DISABLED = "CONFIG_DISABLED"
CAP_REACHED = "CAP_REACHED"
NO_EXECUTABLE_PROPOSALS = "NO_EXECUTABLE_PROPOSALS"
AGENT_GATE_FAILED = "AGENT_GATE_FAILED"
PYTHON_RISK_REJECTED = "PYTHON_RISK_REJECTED"
BROKER_ERROR = "BROKER_ERROR"
LOOP_NOT_RUNNING = "LOOP_NOT_RUNNING"
UNKNOWN = "UNKNOWN"

ALL_DIAGNOSES = (
    READY,
    MARKET_CLOSED,
    CONFIG_DISABLED,
    CAP_REACHED,
    NO_EXECUTABLE_PROPOSALS,
    AGENT_GATE_FAILED,
    PYTHON_RISK_REJECTED,
    BROKER_ERROR,
    LOOP_NOT_RUNNING,
    UNKNOWN,
)


@dataclass(frozen=True)
class TeamLoopFacts:
    """Read-only facts gathered for one team. No secrets, no proposals."""

    team_id: str
    # Time.
    local_iso: str
    ny_iso: str
    # Market clock (None fields when undeterminable).
    market_is_open: bool | None
    clock_next_open: str | None = None
    clock_next_close: str | None = None
    clock_note: str | None = None
    # Config / safety.
    kill_switch_engaged: bool = False
    dry_run: bool = False
    trading_mode: str = "paper"
    stocks_enabled: bool = True
    strict_market_hours_only: bool = True
    market_hours_only: bool = True
    review_only_during_market_hours: bool = True
    sleep_seconds: int = 900
    cheap_gate_enabled: bool = False
    min_full_cycle_interval_minutes: int = 30
    proposal_source: str = "llm"
    # Account (None when the team's paper account is unreachable).
    account_ok: bool = False
    account_classification: str = "unknown"
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    open_positions: int | None = None
    low_buying_power: bool = False
    low_bp_threshold_pct: float = 0.15
    # Usage / caps.
    orders_today: int | None = None
    max_daily_orders_per_team: int = 3
    daily_notional_note: str = "not tracked on the week-loop path"
    # Cheap-gate decision (advisory; deterministic).
    gate_should_run_full_cycle: bool = True
    gate_recommend_review_only: bool = False
    gate_reason: str = ""
    # Latest cycle outcome (from the most recent scorecard).
    latest_scorecard_path: str | None = None
    latest_cycle_at: str | None = None
    proposals_count: int | None = None
    approved_count: int | None = None
    rejected_count: int | None = None
    simulation_only_count: int | None = None
    orders_submitted: int | None = None
    broker_rejected_count: int | None = None
    portfolio_decision_type: str | None = None
    portfolio_no_trade: bool | None = None
    no_trade_reason: str | None = None
    # Loop liveness (audit heartbeat).
    last_audit_iso: str | None = None
    audit_age_seconds: float | None = None
    loop_heartbeat_stale: bool = False


@dataclass
class TeamDiagnosis:
    team_id: str
    diagnosis: str
    headline: str
    last_cycle_diagnosis: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "diagnosis": self.diagnosis,
            "headline": self.headline,
            "last_cycle_diagnosis": self.last_cycle_diagnosis,
            "notes": list(self.notes),
        }


def classify_last_cycle(facts: TeamLoopFacts) -> str:
    """Classify what the most recent recorded cycle did (best-effort)."""

    if facts.proposals_count is None:
        return LOOP_NOT_RUNNING
    if facts.broker_rejected_count:
        return BROKER_ERROR
    if facts.orders_submitted:
        return READY
    # No orders submitted — figure out why.
    if facts.proposals_count == 0:
        return NO_EXECUTABLE_PROPOSALS
    if facts.approved_count == 0 and facts.rejected_count:
        return PYTHON_RISK_REJECTED
    if facts.approved_count == 0:
        return NO_EXECUTABLE_PROPOSALS
    return UNKNOWN


def classify_diagnosis(facts: TeamLoopFacts) -> TeamDiagnosis:
    """Compute the headline blocker for a team using precedence rules.

    Precedence favors *persistent* blockers (config, broker, exhausted buying
    power, caps) over the transient MARKET_CLOSED, so an operator sees the real
    reason the loop will not trade even once the market reopens.
    """

    notes: list[str] = []
    last_cycle = classify_last_cycle(facts)

    # Nothing has ever run and there's no heartbeat -> the loop likely isn't up.
    never_ran = facts.proposals_count is None and facts.last_audit_iso is None
    if never_ran:
        return TeamDiagnosis(
            team_id=facts.team_id,
            diagnosis=LOOP_NOT_RUNNING,
            headline="No scorecard and no iteration audit found — the loop has not produced a cycle.",
            last_cycle_diagnosis=last_cycle,
            notes=["Start the loop (see README) or run a single --once iteration to verify."],
        )

    if facts.loop_heartbeat_stale:
        notes.append(
            f"Iteration audit heartbeat is stale (last at {facts.last_audit_iso}); "
            "the loop process may not be running."
        )

    # 1) Config / safety hard-off.
    if facts.kill_switch_engaged:
        return TeamDiagnosis(facts.team_id, CONFIG_DISABLED,
                             "Kill switch is ENGAGED — execution is globally disabled.", last_cycle, notes)
    if facts.trading_mode != "paper":
        return TeamDiagnosis(facts.team_id, CONFIG_DISABLED,
                             f"TRADING_MODE={facts.trading_mode!r} (not 'paper') — execution surfaces are off.",
                             last_cycle, notes)
    if facts.dry_run:
        return TeamDiagnosis(facts.team_id, CONFIG_DISABLED,
                             "DRY_RUN=true — the loop runs cycles but never submits paper orders.",
                             last_cycle, notes)
    if not facts.stocks_enabled:
        return TeamDiagnosis(facts.team_id, CONFIG_DISABLED,
                             "ENABLE_PAPER_STOCKS is off — no execution-eligible stock_long route.",
                             last_cycle, notes)

    # 2) Broker unreachable for this team.
    if not facts.account_ok:
        return TeamDiagnosis(facts.team_id, BROKER_ERROR,
                             f"Team paper account unreachable ({facts.account_classification}).",
                             last_cycle, notes)

    # 3) Daily order cap reached for the current ET trading date.
    if (
        facts.orders_today is not None
        and facts.orders_today >= facts.max_daily_orders_per_team
    ):
        return TeamDiagnosis(
            facts.team_id, CAP_REACHED,
            f"Daily order cap reached: {facts.orders_today}/{facts.max_daily_orders_per_team} "
            "orders today (ET). New orders resume next trading day.",
            last_cycle, notes,
        )

    # 4) Buying power exhausted -> deterministic risk/PM blocks all new orders.
    if facts.low_buying_power:
        bp_txt = "unknown" if facts.buying_power is None else f"${facts.buying_power:,.0f}"
        return TeamDiagnosis(
            facts.team_id, PYTHON_RISK_REJECTED,
            f"Low buying power ({bp_txt}, < {facts.low_bp_threshold_pct:.0%} of equity): the "
            "deterministic portfolio/risk gate blocks new-money buys until room is freed.",
            last_cycle, notes,
        )

    # 5) Market closed (transient, but the honest current reason when nothing else blocks).
    if facts.market_is_open is False and facts.market_hours_only:
        extra = " Strict quiet mode is active." if facts.strict_market_hours_only else ""
        return TeamDiagnosis(
            facts.team_id, MARKET_CLOSED,
            f"Market is closed; full cycles are suppressed until the next open"
            + (f" ({facts.clock_next_open})" if facts.clock_next_open else "")
            + f".{extra}",
            last_cycle, notes,
        )

    # 6) Last cycle produced nothing executable even though the path was clear.
    if last_cycle == NO_EXECUTABLE_PROPOSALS:
        return TeamDiagnosis(
            facts.team_id, NO_EXECUTABLE_PROPOSALS,
            "Last cycle generated no execution-eligible proposals (model held / proposed nothing, "
            "or all routed to simulation). This can be a HEALTHY no-trade.",
            last_cycle, notes,
        )
    if last_cycle == PYTHON_RISK_REJECTED:
        return TeamDiagnosis(
            facts.team_id, PYTHON_RISK_REJECTED,
            "Last cycle's proposals were rejected by the deterministic risk engine.",
            last_cycle, notes,
        )
    if last_cycle == BROKER_ERROR:
        return TeamDiagnosis(
            facts.team_id, BROKER_ERROR,
            "Last cycle had broker rejections; inspect attribution for failure categories.",
            last_cycle, notes,
        )

    # 7) Cheap gate is holding back a full cycle (cost control, not a fault).
    if facts.cheap_gate_enabled and not facts.gate_should_run_full_cycle:
        return TeamDiagnosis(
            facts.team_id, AGENT_GATE_FAILED,
            f"Cheap cycle gate is holding back a full cycle: {facts.gate_reason}",
            last_cycle, notes,
        )

    # 8) Everything clear and the market is open (or unknown) -> ready to trade.
    if facts.market_is_open is not False:
        return TeamDiagnosis(
            facts.team_id, READY,
            "Config, account, caps, and buying power all clear; the next eligible iteration "
            "can run a full cycle and submit if a proposal clears deterministic risk.",
            last_cycle, notes,
        )

    return TeamDiagnosis(facts.team_id, UNKNOWN,
                         "No single blocker identified; inspect the audit log.", last_cycle, notes)


def _fmt(value: Any, money: bool = False) -> str:
    if value is None:
        return "n/a"
    if money and isinstance(value, (int, float)):
        return f"${value:,.2f}"
    return str(value)


def format_team_report(facts: TeamLoopFacts, diagnosis: TeamDiagnosis) -> str:
    """Render the full per-team diagnostic block. No secrets are included."""

    lines: list[str] = []
    lines.append(f"================ {facts.team_id} ================")
    lines.append(f"Local time:        {facts.local_iso}")
    lines.append(f"America/New_York:  {facts.ny_iso}")

    if facts.market_is_open is None:
        clock_state = f"unknown ({facts.clock_note or 'clock unavailable'})"
    else:
        clock_state = "OPEN" if facts.market_is_open else "CLOSED"
    lines.append(f"Market clock:      {clock_state}")
    lines.append(f"  next_open:       {_fmt(facts.clock_next_open)}")
    lines.append(f"  next_close:      {_fmt(facts.clock_next_close)}")

    lines.append("Loop / market-hours config:")
    lines.append(f"  market_hours_only={facts.market_hours_only} "
                 f"strict_market_hours_only={facts.strict_market_hours_only} "
                 f"review_only_during_market_hours={facts.review_only_during_market_hours}")
    lines.append(f"  sleep_seconds={facts.sleep_seconds} "
                 f"cheap_gate_enabled={facts.cheap_gate_enabled} "
                 f"min_full_cycle_interval_minutes={facts.min_full_cycle_interval_minutes}")
    lines.append(f"  proposal_source={facts.proposal_source}")

    lines.append("Autonomy / execution:")
    lines.append(f"  kill_switch_engaged={facts.kill_switch_engaged} dry_run={facts.dry_run} "
                 f"trading_mode={facts.trading_mode} stocks_enabled={facts.stocks_enabled}")

    lines.append("Paper account (read-only):")
    lines.append(f"  auth_ok={facts.account_ok} ({facts.account_classification})")
    lines.append(f"  equity={_fmt(facts.equity, money=True)} cash={_fmt(facts.cash, money=True)} "
                 f"buying_power={_fmt(facts.buying_power, money=True)}")
    lines.append(f"  open_positions={_fmt(facts.open_positions)} "
                 f"low_buying_power={facts.low_buying_power} (threshold {facts.low_bp_threshold_pct:.0%})")

    lines.append("Daily usage vs caps (ET trading date):")
    lines.append(f"  orders_today={_fmt(facts.orders_today)} / max_daily_orders_per_team={facts.max_daily_orders_per_team}")
    lines.append(f"  daily_notional: {facts.daily_notional_note}")

    lines.append("Cheap cycle gate (deterministic; no LLM):")
    lines.append(f"  should_run_full_cycle={facts.gate_should_run_full_cycle} "
                 f"recommend_review_only={facts.gate_recommend_review_only}")
    lines.append(f"  reason={facts.gate_reason or '(gate disabled)'}")

    lines.append("Loop liveness:")
    lines.append(f"  last_iteration_audit={_fmt(facts.last_audit_iso)} stale={facts.loop_heartbeat_stale}")

    lines.append("Latest recorded cycle:")
    lines.append(f"  scorecard={_fmt(facts.latest_scorecard_path)}")
    lines.append(f"  at={_fmt(facts.latest_cycle_at)}")
    lines.append(f"  proposals={_fmt(facts.proposals_count)} approved={_fmt(facts.approved_count)} "
                 f"simulation_only={_fmt(facts.simulation_only_count)} rejected={_fmt(facts.rejected_count)}")
    lines.append(f"  orders_submitted={_fmt(facts.orders_submitted)} "
                 f"broker_rejected={_fmt(facts.broker_rejected_count)}")
    lines.append(f"  portfolio_decision={_fmt(facts.portfolio_decision_type)} "
                 f"no_trade={_fmt(facts.portfolio_no_trade)}")
    lines.append(f"  no_trade_reason={_fmt(facts.no_trade_reason)}")
    lines.append("  (risk/review token contents are not persisted; routing counts above reflect the "
                 "deterministic Python risk result.)")
    lines.append(f"  last_cycle_diagnosis={diagnosis.last_cycle_diagnosis}")

    lines.append("")
    lines.append(f">>> DIAGNOSIS [{facts.team_id}]: {diagnosis.diagnosis}")
    lines.append(f"    {diagnosis.headline}")
    for note in diagnosis.notes:
        lines.append(f"    note: {note}")
    return "\n".join(lines)


__all__ = [
    "READY", "MARKET_CLOSED", "CONFIG_DISABLED", "CAP_REACHED",
    "NO_EXECUTABLE_PROPOSALS", "AGENT_GATE_FAILED", "PYTHON_RISK_REJECTED",
    "BROKER_ERROR", "LOOP_NOT_RUNNING", "UNKNOWN", "ALL_DIAGNOSES",
    "TeamLoopFacts", "TeamDiagnosis",
    "classify_last_cycle", "classify_diagnosis", "format_team_report",
]
