"""Read-only position review + portfolio-health stage (Phase 7V).

Turns refreshed Alpaca paper positions into a structured, deterministic review:
per-position P&L / weight / thesis status / recommended action, plus portfolio
health checks (negative cash, zero buying power, concentration) and a clear
"should new buys be blocked?" verdict.

This module is strictly READ-ONLY: it never builds a broker client, never calls
``submit_order``, and never mutates state. The deterministic recommendations here
are advisory inputs to the gated execution path  -  the LLM may add color, but the
deterministic rules and the kill switch remain authoritative. It only manages
LONG stock positions; shorts/options are never recommended for new action here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.config.portfolio_limits import PortfolioLimits

# Deterministic action thresholds (conservative; all overridable via the caller).
DEFAULT_STOP_LOSS_PCT = -0.15      # down 15%+ -> exit (thesis likely invalidated)
DEFAULT_WEAKENING_PCT = -0.05      # down 5-15% -> thesis weakening (watch)
DEFAULT_TAKE_PROFIT_PCT = 0.25     # up 25%+ -> consider trimming, esp. if overweight

# Thesis status values.
THESIS_INTACT = "intact"
THESIS_WEAKENING = "weakening"
THESIS_INVALIDATED = "invalidated"
THESIS_UNKNOWN = "unknown"

# Recommended actions (long-only management).
ACTION_HOLD = "hold"
ACTION_TRIM = "trim"
ACTION_EXIT = "exit"
ACTION_WATCH = "watch"


@dataclass
class PositionView:
    symbol: str
    quantity: float
    side: str  # long | short
    avg_entry_price: float | None
    current_price: float | None
    market_value: float | None
    cost_basis: float | None
    unrealized_pl: float | None
    unrealized_pl_pct: float | None
    portfolio_weight: float | None
    days_held: int | None
    original_thesis: str | None
    thesis_source_proposal_id: str | None
    thesis_status: str
    conviction_score: float
    recommended_action: str
    reason: str
    target_price: float | None
    downside_stop: float | None
    time_horizon: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PortfolioHealth:
    equity: float | None
    cash: float | None
    buying_power: float | None
    long_market_value: float
    short_market_value: float
    gross_exposure_pct: float | None
    open_position_count: int
    negative_cash: bool
    zero_buying_power: bool
    low_buying_power: bool
    emergency_buying_power: bool
    concentration_alerts: list[str] = field(default_factory=list)
    critical_problems: list[str] = field(default_factory=list)
    block_new_buys: bool = False
    block_new_buys_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TeamPortfolioReview:
    team_id: str
    generated_at: str
    equity: float | None
    cash: float | None
    buying_power: float | None
    positions: list[PositionView]
    health: PortfolioHealth
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c = {ACTION_HOLD: 0, ACTION_TRIM: 0, ACTION_EXIT: 0, ACTION_WATCH: 0}
        for p in self.positions:
            c[p.recommended_action] = c.get(p.recommended_action, 0) + 1
        return c

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "generated_at": self.generated_at,
            "equity": self.equity,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "counts": self.counts(),
            "positions": [p.as_dict() for p in self.positions],
            "health": self.health.as_dict(),
            "notes": list(self.notes),
        }


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def read_position_fields(raw: Any) -> dict[str, Any]:
    """Read the full set of fields from an Alpaca position object or dict.

    Tolerant of both alpaca-py ``Position`` objects and plain dicts (tests).
    """

    def get(name: str) -> Any:
        if isinstance(raw, Mapping):
            return raw.get(name)
        return getattr(raw, name, None)

    side = get("side")
    side = (getattr(side, "value", side) or "long")
    return {
        "symbol": str(get("symbol") or "").upper(),
        "qty": _f(get("qty")),
        "side": str(side).lower(),
        "avg_entry_price": _f(get("avg_entry_price")),
        "current_price": _f(get("current_price")),
        "market_value": _f(get("market_value")),
        "cost_basis": _f(get("cost_basis")),
        "unrealized_pl": _f(get("unrealized_pl")),
        "unrealized_pl_pct": _f(get("unrealized_plpc")),
    }


def _thesis_for_symbol(symbol: str, attribution_entries: list[Any]) -> tuple[str | None, str | None, str | None]:
    """Return (thesis, source_proposal_id, entry_iso) for the latest submitted long entry."""

    best = None
    for entry in attribution_entries:
        sym = str(getattr(entry, "symbol", "") or "").upper()
        if sym != symbol:
            continue
        asset_type = str(getattr(entry, "asset_type", "") or "")
        if "short" in asset_type or asset_type.startswith("option"):
            continue
        ts = getattr(entry, "timestamp", None)
        if best is None or (ts and ts > best[2]):
            best = (
                getattr(entry, "thesis", None),
                getattr(entry, "proposal_id", None),
                ts or "",
            )
    if best is None:
        return None, None, None
    return best[0], best[1], (best[2] or None)


def _days_held(entry_iso: str | None, now: datetime) -> int | None:
    if not entry_iso:
        return None
    try:
        ts = datetime.fromisoformat(entry_iso)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, (now - ts).days)


def classify_thesis_status(
    has_thesis: bool, unrealized_pl_pct: float | None, *,
    stop_loss_pct: float, weakening_pct: float,
) -> str:
    if unrealized_pl_pct is None:
        return THESIS_UNKNOWN
    if unrealized_pl_pct <= stop_loss_pct:
        return THESIS_INVALIDATED
    if unrealized_pl_pct <= weakening_pct:
        return THESIS_WEAKENING
    if not has_thesis:
        return THESIS_UNKNOWN
    return THESIS_INTACT


def recommend_action(
    *,
    side: str,
    unrealized_pl_pct: float | None,
    portfolio_weight: float | None,
    thesis_status: str,
    limits: PortfolioLimits,
    stop_loss_pct: float,
    take_profit_pct: float,
    free_capital: bool,
) -> tuple[str, str]:
    """Deterministic, conservative long-only recommendation. HOLD is valid."""

    if side != "long":
        return ACTION_WATCH, (
            f"{side} position  -  this manager only acts on long stock; no action taken "
            "(no shorting/options behavior is introduced)."
        )

    weight = portfolio_weight if portfolio_weight is not None else 0.0
    plpc = unrealized_pl_pct

    # Hard risk: deep loss / invalidated thesis -> exit.
    if plpc is not None and plpc <= stop_loss_pct:
        return ACTION_EXIT, (
            f"Down {plpc:.1%} (<= {stop_loss_pct:.0%} stop); thesis treated as invalidated  -  sell-to-close to cap loss."
        )
    if thesis_status == THESIS_INVALIDATED:
        return ACTION_EXIT, "Thesis invalidated by adverse move; sell-to-close to free capital."

    # Concentration / strong gain while overweight -> trim toward target.
    if weight > limits.concentration_alert_pct:
        return ACTION_TRIM, (
            f"Weight {weight:.1%} exceeds concentration alert {limits.concentration_alert_pct:.0%}; "
            f"trim toward max position {limits.max_position_pct:.0%}."
        )
    if plpc is not None and plpc >= take_profit_pct and weight > limits.max_position_pct:
        return ACTION_TRIM, (
            f"Up {plpc:.1%} and overweight ({weight:.1%} > {limits.max_position_pct:.0%}); "
            "trim to lock partial gains and reduce concentration."
        )

    # Capital pressure: free room from the weakest names first (bounded elsewhere).
    if free_capital and plpc is not None and plpc <= weakening_floor(stop_loss_pct):
        return ACTION_TRIM, (
            "Buying power exhausted; this is among the weaker holdings  -  trim to free capital "
            "(bounded by daily trim/exit limits; never a full auto-liquidation)."
        )

    if thesis_status == THESIS_WEAKENING:
        return ACTION_WATCH, "Thesis weakening (modest drawdown); watch for invalidation before acting."

    return ACTION_HOLD, "Thesis intact and within risk/concentration limits; hold and observe."


def weakening_floor(stop_loss_pct: float) -> float:
    """The loss level (above the hard stop) that marks a 'weak' holding for trimming."""

    return stop_loss_pct / 2.0  # e.g. -7.5% when stop is -15%


def _conviction(unrealized_pl_pct: float | None, thesis_status: str) -> float:
    base = 0.5
    if thesis_status == THESIS_INTACT:
        base = 0.65
    elif thesis_status == THESIS_WEAKENING:
        base = 0.4
    elif thesis_status == THESIS_INVALIDATED:
        base = 0.15
    if unrealized_pl_pct is not None:
        base += max(-0.25, min(0.25, unrealized_pl_pct))
    return round(max(0.0, min(1.0, base)), 2)


def build_position_view(
    raw: Any,
    *,
    equity: float | None,
    attribution_entries: list[Any],
    limits: PortfolioLimits,
    now: datetime,
    free_capital: bool,
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
    weakening_pct: float = DEFAULT_WEAKENING_PCT,
    take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
) -> PositionView:
    f = read_position_fields(raw)
    weight = None
    if f["market_value"] is not None and equity and equity > 0:
        weight = abs(f["market_value"]) / equity

    thesis, source_id, entry_iso = _thesis_for_symbol(f["symbol"], attribution_entries)
    thesis_status = classify_thesis_status(
        bool(thesis), f["unrealized_pl_pct"],
        stop_loss_pct=stop_loss_pct, weakening_pct=weakening_pct,
    )
    action, reason = recommend_action(
        side=f["side"],
        unrealized_pl_pct=f["unrealized_pl_pct"],
        portfolio_weight=weight,
        thesis_status=thesis_status,
        limits=limits,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        free_capital=free_capital,
    )
    entry = f["avg_entry_price"]
    target = round(entry * (1 + take_profit_pct), 2) if entry else None
    stop = round(entry * (1 + stop_loss_pct), 2) if entry else None
    return PositionView(
        symbol=f["symbol"],
        quantity=f["qty"] or 0.0,
        side=f["side"],
        avg_entry_price=entry,
        current_price=f["current_price"],
        market_value=f["market_value"],
        cost_basis=f["cost_basis"],
        unrealized_pl=f["unrealized_pl"],
        unrealized_pl_pct=f["unrealized_pl_pct"],
        portfolio_weight=weight,
        days_held=_days_held(entry_iso, now),
        original_thesis=thesis,
        thesis_source_proposal_id=source_id,
        thesis_status=thesis_status,
        conviction_score=_conviction(f["unrealized_pl_pct"], thesis_status),
        recommended_action=action,
        reason=reason,
        target_price=target,
        downside_stop=stop,
        time_horizon="swing (3-15 sessions)",
        confidence=_conviction(f["unrealized_pl_pct"], thesis_status),
    )


def assess_health(
    *,
    equity: float | None,
    cash: float | None,
    buying_power: float | None,
    positions: list[PositionView],
    limits: PortfolioLimits,
) -> PortfolioHealth:
    long_mv = sum((p.market_value or 0.0) for p in positions if p.side == "long")
    short_mv = sum(abs(p.market_value or 0.0) for p in positions if p.side != "long")
    gross = ((long_mv + short_mv) / equity) if equity and equity > 0 else None

    bp = buying_power if buying_power is not None else (cash or 0.0)
    bp_ratio = (bp / equity) if equity and equity > 0 else 0.0
    negative_cash = cash is not None and cash < 0
    zero_bp = bp is not None and bp <= 0
    low_bp = bp_ratio < limits.emergency_buying_power_pct * 3  # advisory low-BP band
    emergency = bp_ratio < limits.emergency_buying_power_pct

    concentration = [
        p.symbol for p in positions
        if p.portfolio_weight is not None and p.portfolio_weight > limits.concentration_alert_pct
    ]

    problems: list[str] = []
    if negative_cash:
        problems.append(f"Negative cash ({cash:,.0f}): account is over-deployed / on margin.")
    if zero_bp:
        problems.append("Zero (or negative) buying power: no room for new-money buys.")
    if gross is not None and gross > limits.max_portfolio_gross_exposure_pct:
        problems.append(
            f"Gross exposure {gross:.0%} exceeds limit {limits.max_portfolio_gross_exposure_pct:.0%}."
        )
    for sym in concentration:
        problems.append(f"Concentration: {sym} exceeds {limits.concentration_alert_pct:.0%} of equity.")
    missing_thesis = [p.symbol for p in positions if not p.original_thesis]
    if missing_thesis:
        problems.append(
            f"Missing/stale local thesis for: {', '.join(sorted(missing_thesis))} (review before adding risk)."
        )

    block = bool(zero_bp or negative_cash or emergency
                 or (gross is not None and gross > limits.max_portfolio_gross_exposure_pct))
    if block:
        reason = (
            "New-money buys BLOCKED until capital is freed (trim/sell-to-close) or buying power "
            "recovers. Bounded reductions are allowed; full auto-liquidation is not."
        )
    else:
        reason = "New buys permitted within position/exposure limits."

    return PortfolioHealth(
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        long_market_value=long_mv,
        short_market_value=short_mv,
        gross_exposure_pct=gross,
        open_position_count=len(positions),
        negative_cash=negative_cash,
        zero_buying_power=zero_bp,
        low_buying_power=low_bp,
        emergency_buying_power=emergency,
        concentration_alerts=concentration,
        critical_problems=problems,
        block_new_buys=block,
        block_new_buys_reason=reason,
    )


def build_team_portfolio_review(
    team_id: str,
    *,
    equity: float | None,
    cash: float | None,
    buying_power: float | None,
    raw_positions: list[Any],
    attribution_entries: list[Any],
    limits: PortfolioLimits,
    now: datetime | None = None,
) -> TeamPortfolioReview:
    """Pure builder: given refreshed account + positions, produce the review."""

    now = now or datetime.now(timezone.utc)
    bp = buying_power if buying_power is not None else (cash or 0.0)
    bp_ratio = (bp / equity) if equity and equity > 0 else 0.0
    free_capital = bp_ratio < limits.emergency_buying_power_pct

    views = [
        build_position_view(
            raw, equity=equity, attribution_entries=attribution_entries,
            limits=limits, now=now, free_capital=free_capital,
        )
        for raw in raw_positions
    ]
    # Sort worst-first so the report leads with what needs attention.
    views.sort(key=lambda p: (p.unrealized_pl_pct if p.unrealized_pl_pct is not None else 0.0))

    health = assess_health(
        equity=equity, cash=cash, buying_power=buying_power,
        positions=views, limits=limits,
    )
    notes = [
        "Paper-only review. Sell-to-close reduces/closes existing long stock only; "
        "no shorting, options, margin, or live trading.",
    ]
    if free_capital and views:
        notes.append(
            "Buying power exhausted: the loop can still recommend and (if approved by "
            "deterministic rules) execute bounded trims/exits to free capital  -  it is not "
            "stuck permanently in 'no new buy' mode."
        )
    return TeamPortfolioReview(
        team_id=team_id,
        generated_at=now.isoformat(),
        equity=equity, cash=cash, buying_power=buying_power,
        positions=views, health=health, notes=notes,
    )


__all__ = [
    "PositionView", "PortfolioHealth", "TeamPortfolioReview",
    "read_position_fields", "classify_thesis_status", "recommend_action",
    "build_position_view", "assess_health", "build_team_portfolio_review",
    "THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED", "THESIS_UNKNOWN",
    "ACTION_HOLD", "ACTION_TRIM", "ACTION_EXIT", "ACTION_WATCH",
    "DEFAULT_STOP_LOSS_PCT", "DEFAULT_WEAKENING_PCT", "DEFAULT_TAKE_PROFIT_PCT",
]
