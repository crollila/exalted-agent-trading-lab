"""Portfolio Manager / Capital Allocator stage (Phase 7M).

Before a team proposes new trades, it reviews the current portfolio, buying
power, prior theses, attribution outcomes, and SPY-relative performance, then
decides whether to hold, trim, close, rotate, add, hedge, reduce exposure, or do
nothing. This stage makes proposal generation strategy-driven instead of "open
something new every cycle".

Key safety properties:

* This is deterministic. An LLM may *suggest* a tactical intent, but it cannot
  widen caps, unblock low-buying-power buys, or override platform hard risk caps.
* "No trade" / "hold" is a first-class, successful outcome — not a failure.
* Low buying power triggers a review; it never hard-stops the whole cycle. New
  money is blocked unless the team first frees room (trim/close/rotate) or makes
  an explicit margin-exposure request.
* Teams have personalities: ``team_alpha`` is higher-variance (more willing to
  rotate / test ideas, slightly higher cap); ``team_beta`` is conservative
  (fewer trades, more hold/trim, lower cap). Both still obey hard caps.

No secrets are read or stored here.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from src.competition.risk_engine import AccountContext
from src.config.permissions import TradingPermissions


class PortfolioDecisionType(str, Enum):
    HOLD = "hold"
    NO_TRADE = "no_trade"
    TRIM = "trim"
    CLOSE = "close"
    ROTATE = "rotate"
    ADD = "add"
    REDUCE_GROSS_EXPOSURE = "reduce_gross_exposure"
    INCREASE_MARGIN_EXPOSURE_REQUEST = "increase_margin_exposure_request"
    HEDGE = "hedge"


# Decision types that, when chosen, intend to place NEW broker orders this cycle.
NEW_ORDER_DECISIONS = {
    PortfolioDecisionType.ADD,
    PortfolioDecisionType.ROTATE,
    PortfolioDecisionType.HEDGE,
    PortfolioDecisionType.INCREASE_MARGIN_EXPOSURE_REQUEST,
}

# Decision types that free up buying power / reduce exposure.
FREEING_DECISIONS = {
    PortfolioDecisionType.TRIM,
    PortfolioDecisionType.CLOSE,
    PortfolioDecisionType.ROTATE,
    PortfolioDecisionType.REDUCE_GROSS_EXPOSURE,
}

# Tactical (team-chosen) threshold: meaningfully behind SPY.
_UNDERPERFORM_VS_SPY = -0.02


def _read_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() == "true"


def _read_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class PortfolioManagerConfig:
    """Cost-control + behavior config. Hard risk caps live in TradingPermissions."""

    enabled: bool = True
    low_buying_power_review_threshold_pct: float = 0.15
    allow_no_trade_decisions: bool = True
    max_new_proposals_alpha: int = 3
    max_new_proposals_beta: int = 2
    cheap_cycle_gate_enabled: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PortfolioManagerConfig":
        if env is None:
            env = os.environ
        return cls(
            enabled=_read_bool(env, "PORTFOLIO_MANAGER_ENABLED", True),
            low_buying_power_review_threshold_pct=_read_float(
                env, "LOW_BUYING_POWER_REVIEW_THRESHOLD_PCT", 0.15
            ),
            allow_no_trade_decisions=_read_bool(env, "ALLOW_NO_TRADE_DECISIONS", True),
            max_new_proposals_alpha=_read_int(env, "MAX_NEW_PROPOSALS_ALPHA", 3),
            max_new_proposals_beta=_read_int(env, "MAX_NEW_PROPOSALS_BETA", 2),
            cheap_cycle_gate_enabled=_read_bool(env, "CHEAP_CYCLE_GATE_ENABLED", False),
        )


@dataclass
class PortfolioDecision:
    team_id: str
    decision_type: str
    affected_symbols: list[str] = field(default_factory=list)
    rationale: str = ""
    relation_to_spy_performance: str = ""
    relation_to_recent_attribution: str = ""
    buying_power_impact: str = ""
    risk_notes: str = ""
    allowed_to_generate_new_orders: bool = True
    max_new_proposals_this_cycle: int = 0
    proposed_closes_or_trims: list[str] = field(default_factory=list)
    rejected_new_ideas_reason: str | None = None
    low_buying_power: bool = False
    mode: str = ""  # exploration | conservation
    review_questions: dict[str, str] = field(default_factory=dict)
    # Phase 7Z: a no-trade decision must name its CURRENT-data evidence source
    # (never "only historical memory"). One of: current_account_state,
    # current_positions, current_cap_usage, current_market_research_evidence,
    # current_spy_relative_performance, current_risk_condition.
    no_trade_evidence_source: str = ""

    def is_no_trade(self) -> bool:
        """True when this cycle places no new broker orders (hold/no-trade/blocked)."""

        return (not self.allowed_to_generate_new_orders) or self.max_new_proposals_this_cycle <= 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        verb = "NO NEW TRADES" if self.is_no_trade() else f"up to {self.max_new_proposals_this_cycle} new"
        return f"{self.decision_type} ({verb}; mode={self.mode})"


def _coerce_decision_type(raw: Any) -> PortfolioDecisionType | None:
    if isinstance(raw, PortfolioDecisionType):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return PortfolioDecisionType(raw.strip().lower())
    except ValueError:
        return None


def _describe_spy(spy_excess: float | None, team_return: float | None, spy_return: float | None) -> str:
    if spy_excess is None:
        return "SPY-relative performance unknown (no benchmark yet)."
    direction = "beating" if spy_excess > 0 else ("trailing" if spy_excess < 0 else "matching")
    parts = [f"{direction} SPY by {spy_excess:+.4f} excess"]
    if team_return is not None and spy_return is not None:
        parts.append(f"(team {team_return:+.4f} vs SPY {spy_return:+.4f})")
    return " ".join(parts)


def _describe_attribution(outcome: dict[str, Any]) -> str:
    worked = outcome.get("worked_count", 0)
    failed = outcome.get("failed_count", 0)
    pending = outcome.get("pending_count", 0)
    rejections = outcome.get("recent_broker_rejections", []) or []
    text = f"recent outcomes worked={worked} failed={failed} pending={pending}"
    if rejections:
        cats = sorted({r.get("failure_category", "unknown") for r in rejections})
        text += f"; broker rejections: {', '.join(cats)}"
    return text


def _build_review_questions(
    *,
    spy_text: str,
    attribution_text: str,
    candidate_count: int,
    positions_count: int,
) -> dict[str, str]:
    """Compact self-review prompts (cost-controlled — short, deterministic)."""

    return {
        "why_vs_spy": spy_text,
        "drivers": f"{positions_count} open position(s); review which symbols/sectors drove performance.",
        "prior_thesis_outcome": attribution_text,
        "what_changed": "Compare this cycle's context to the previous cycle before adding risk.",
        "better_than_weakest_holding": (
            f"{candidate_count} new idea(s) proposed — only act if better than the weakest current holding."
        ),
        "hold_and_observe": "Holding/observing is a valid outcome when no idea clears the bar.",
    }


def review_portfolio(
    *,
    team_id: str,
    config: PortfolioManagerConfig,
    permissions: TradingPermissions,
    account: AccountContext,
    candidate_count: int,
    spy_excess: float | None = None,
    team_return: float | None = None,
    spy_return: float | None = None,
    attribution_feedback: dict[str, Any] | None = None,
    positions: list[Any] | None = None,
    llm_intent: dict[str, Any] | None = None,
) -> PortfolioDecision:
    """Deterministically decide what the team should do this cycle.

    Returns a ``PortfolioDecision``. An optional ``llm_intent`` (parsed from the
    model's ``portfolio_decision`` block) may *narrow* behavior (pick a tactical
    decision_type, request margin, propose trims, lower the cap) but can never
    widen the cap, unblock low-buying-power buys, or bypass hard risk caps.
    """

    is_alpha = team_id == "team_alpha"
    base_cap = config.max_new_proposals_alpha if is_alpha else config.max_new_proposals_beta
    hard_cap = max(0, permissions.max_daily_orders_per_team - account.orders_today)
    positions = positions or []
    positions_count = len(positions)
    outcome = (attribution_feedback or {}).get("outcome_feedback", {}) or {}
    worked = int(outcome.get("worked_count", 0) or 0)
    failed = int(outcome.get("failed_count", 0) or 0)

    spy_text = _describe_spy(spy_excess, team_return, spy_return)
    attribution_text = _describe_attribution(outcome)
    review_questions = _build_review_questions(
        spy_text=spy_text,
        attribution_text=attribution_text,
        candidate_count=candidate_count,
        positions_count=positions_count,
    )
    default_mode = "exploration" if is_alpha else "conservation"

    # Buying power as a fraction of equity (cash fallback when BP is unknown).
    bp = account.buying_power if account.buying_power is not None else account.cash
    equity = account.equity or 0.0
    bp_ratio = (bp / equity) if equity > 0 else 0.0
    low_bp = bp_ratio < config.low_buying_power_review_threshold_pct
    bp_impact = f"buying_power≈{bp_ratio:.2%} of equity" + (" (LOW — review triggered)" if low_bp else "")

    # Parse the optional LLM tactical intent (advisory only).
    llm_type = _coerce_decision_type((llm_intent or {}).get("decision_type")) if llm_intent else None
    llm_cap_raw = (llm_intent or {}).get("max_new_proposals_this_cycle")
    try:
        llm_cap = int(llm_cap_raw) if llm_cap_raw is not None else None
    except (TypeError, ValueError):
        llm_cap = None
    llm_trims = [str(s) for s in ((llm_intent or {}).get("proposed_closes_or_trims") or []) if str(s).strip()]
    llm_symbols = [str(s).upper() for s in ((llm_intent or {}).get("affected_symbols") or []) if str(s).strip()]
    llm_rationale = str((llm_intent or {}).get("rationale", "")).strip() or None

    def decision(**kwargs: Any) -> PortfolioDecision:
        base = dict(
            team_id=team_id,
            affected_symbols=llm_symbols,
            relation_to_spy_performance=spy_text,
            relation_to_recent_attribution=attribution_text,
            buying_power_impact=bp_impact,
            low_buying_power=low_bp,
            proposed_closes_or_trims=llm_trims,
            review_questions=review_questions,
            mode=default_mode,
        )
        base.update(kwargs)
        return PortfolioDecision(**base)

    # PM disabled: legacy passthrough (still bounded by the hard daily-order cap).
    if not config.enabled:
        cap = min(base_cap, hard_cap, candidate_count if candidate_count else base_cap)
        return decision(
            decision_type=PortfolioDecisionType.ADD.value,
            rationale="Portfolio manager disabled; legacy proposal flow (still hard-capped).",
            allowed_to_generate_new_orders=cap > 0,
            max_new_proposals_this_cycle=cap,
        )

    # --- Low buying power: review, never hard-stop. ---
    if low_bp:
        freeing = (llm_type in FREEING_DECISIONS) or bool(llm_trims)
        margin_request = llm_type == PortfolioDecisionType.INCREASE_MARGIN_EXPOSURE_REQUEST
        if freeing or margin_request:
            dtype = llm_type or (
                PortfolioDecisionType.ROTATE if positions_count else PortfolioDecisionType.REDUCE_GROSS_EXPOSURE
            )
            allow_new = dtype in NEW_ORDER_DECISIONS
            cap = min(base_cap, hard_cap, candidate_count, 2 if is_alpha else 1) if allow_new else 0
            rationale = (
                llm_rationale
                or f"Low buying power; {dtype.value} to free room before any new exposure."
            )
            rejected_reason = None if allow_new else "Low buying power; freeing room this cycle, no new buys."
            return decision(
                decision_type=dtype.value,
                rationale=rationale,
                risk_notes="Margin/exposure stays within platform hard caps regardless of request.",
                allowed_to_generate_new_orders=allow_new and cap > 0,
                max_new_proposals_this_cycle=cap,
                rejected_new_ideas_reason=rejected_reason,
                mode="conservation",
                no_trade_evidence_source=("" if (allow_new and cap > 0) else "current_account_state"),
            )
        dtype = PortfolioDecisionType.REDUCE_GROSS_EXPOSURE if positions_count else PortfolioDecisionType.NO_TRADE
        return decision(
            decision_type=dtype.value,
            rationale=(
                "Low buying power and no trim/close/rotate or margin request; "
                "blocking new-money buys and reviewing the book."
            ),
            risk_notes="New buys blocked deterministically until room is freed or margin is explicitly requested.",
            allowed_to_generate_new_orders=False,
            max_new_proposals_this_cycle=0,
            rejected_new_ideas_reason="Insufficient buying power; free room (trim/close/rotate) or request margin first.",
            mode="conservation",
            no_trade_evidence_source="current_account_state",
        )

    # --- No candidates: holding is a valid successful outcome. ---
    # This no-trade is grounded on a CURRENT fact (zero candidates this cycle),
    # not on historical memory.
    if candidate_count == 0 and config.allow_no_trade_decisions:
        return decision(
            decision_type=PortfolioDecisionType.NO_TRADE.value,
            rationale="No proposal candidates cleared review this cycle; holding and observing.",
            allowed_to_generate_new_orders=False,
            max_new_proposals_this_cycle=0,
            no_trade_evidence_source="current_market_research_evidence",
        )

    # --- Normal conditions: personality-driven, performance-aware. ---
    defensive = spy_excess is not None and spy_excess < _UNDERPERFORM_VS_SPY
    cap = min(base_cap, hard_cap)
    if is_alpha:
        dtype = PortfolioDecisionType.ROTATE if defensive else PortfolioDecisionType.ADD
    else:
        cap = min(cap, config.max_new_proposals_beta)
        if defensive:
            dtype = PortfolioDecisionType.HOLD  # beta preserves capital when behind
        else:
            dtype = PortfolioDecisionType.ADD

    # Recent failure streak tightens the cap further (faster learning for alpha).
    # Phase 7Z: historical losses ALONE must never force max_new=0 on a healthy,
    # candidate-bearing cycle — keep at least one slot when the cap was positive
    # (the deterministic hard caps still bound it from above).
    if failed > worked and (failed + worked) >= 3:
        cap = max(1, cap - 1) if cap > 0 else 0

    # LLM intent may narrow (never widen) the deterministic decision. But a model
    # HOLD / no-trade is only a HARD candidate-generation block when a CURRENT
    # condition supports it (defensive vs SPY, low BP, or no candidates). When no
    # current evidence supports it, the model's no-trade is DOWNGRADED to advisory
    # so stale-memory reasoning alone can never zero a healthy cycle (Phase 7Z).
    llm_forces_no_new = llm_type is not None and llm_type not in NEW_ORDER_DECISIONS
    current_supports_hold = bool(defensive or low_bp or candidate_count == 0)
    downgraded_llm_hold = False
    if llm_type is not None:
        if llm_forces_no_new and not current_supports_hold:
            downgraded_llm_hold = True  # advisory only; keep deterministic dtype
        else:
            dtype = llm_type
    if llm_cap is not None:
        cap = min(cap, max(0, llm_cap))
    cap = min(cap, candidate_count)

    if dtype in NEW_ORDER_DECISIONS:
        allow_new = cap > 0
    else:
        allow_new = False
        cap = 0

    # Evidence source naming for any no-trade outcome (current data only).
    if allow_new:
        evidence = ""
    elif defensive:
        evidence = "current_spy_relative_performance"
    elif positions_count:
        evidence = "current_positions"
    else:
        evidence = "current_cap_usage"

    rationale = llm_rationale or (
        f"{'Rotating into' if dtype == PortfolioDecisionType.ROTATE else 'Adding'} "
        f"the strongest idea(s); capped at {cap}."
        if allow_new
        else f"{dtype.value}: holding existing book; no new exposure justified this cycle."
    )
    risk_note = "Approved sizing is computed by the deterministic risk engine, not the model."
    if downgraded_llm_hold:
        risk_note += (
            " LLM hold/no-trade downgraded to advisory: no current condition supports a hard block."
        )
    return decision(
        decision_type=dtype.value,
        rationale=rationale,
        risk_notes=risk_note,
        allowed_to_generate_new_orders=allow_new,
        max_new_proposals_this_cycle=cap,
        rejected_new_ideas_reason=(None if allow_new else "No idea beat the weakest current holding."),
        mode=("conservation" if (not is_alpha and defensive) else default_mode),
        no_trade_evidence_source=evidence,
    )
