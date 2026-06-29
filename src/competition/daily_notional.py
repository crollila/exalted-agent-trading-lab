"""Deterministic per-team daily-notional reconciliation + cap enforcement (Phase 7Y).

Reconciles how much *gross paper notional* a team has submitted for the current
America/New_York trading date, then enforces ``MAX_DAILY_NOTIONAL_PER_TEAM``
before every paper order (entries and sell-to-close).

Authority + policy:

* Order usage is reconciled from SUBMITTED paper orders only — never from LLM
  output. Broker order data is the primary source; locally-persisted attribution
  records are a safe fallback when the broker is unavailable.
* Rejected, cancelled, expired, replaced, suspended/failed, and prior-day orders
  do NOT count. Simulation-only proposals never reach a broker, so they never
  count either.
* One consistent policy everywhere: BOTH new entries AND sell-to-close
  submissions count toward the daily notional cap (it is a churn/turnover control
  on gross dollars transacted, not a net-exposure measure).

Pure and deterministic; everything here works without broker credentials. No
secrets are read or logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.competition.market_time import ny_trading_date

# Alpaca order statuses that mean the order did NOT result in a live/accepted
# submission and therefore must not count toward daily notional usage.
EXCLUDED_ORDER_STATUSES = frozenset(
    {"rejected", "canceled", "cancelled", "expired", "replaced", "suspended",
     "failed", "done_for_day_canceled"}
)


@dataclass(frozen=True)
class NotionalReconciliation:
    """Result of reconciling a team's daily notional. No secrets."""

    used: float
    source: str           # broker | local_fallback | unavailable
    status: str           # ok | fallback | unavailable

    def as_dict(self) -> dict[str, Any]:
        return {"used": self.used, "source": self.source, "status": self.status}


def _read(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def order_status(order: Any) -> str:
    raw = _read(order, "status")
    raw = getattr(raw, "value", raw)
    return str(raw or "").strip().lower()


def order_is_submitted(order: Any) -> bool:
    """True when an order counts as a real submitted paper order (not rejected/cancelled)."""

    return order_status(order) not in EXCLUDED_ORDER_STATUSES


def order_notional(order: Any) -> float:
    """Best-effort gross dollar notional for one submitted order (always >= 0).

    Prefers filled value (``filled_qty * filled_avg_price``); falls back to
    ``qty * limit_price``, then to the broker ``notional`` field. Returns 0.0 when
    no price is determinable (never invents a price).
    """

    filled_qty = _to_float(_read(order, "filled_qty")) or 0.0
    filled_avg = _to_float(_read(order, "filled_avg_price"))
    if filled_qty > 0 and filled_avg is not None and filled_avg > 0:
        return abs(filled_qty * filled_avg)

    qty = _to_float(_read(order, "qty")) or 0.0
    limit_price = _to_float(_read(order, "limit_price"))
    if qty > 0 and limit_price is not None and limit_price > 0:
        return abs(qty * limit_price)
    if qty > 0 and filled_avg is not None and filled_avg > 0:
        return abs(qty * filled_avg)

    notional = _to_float(_read(order, "notional"))
    return abs(notional) if notional is not None and notional > 0 else 0.0


def daily_notional_from_orders(orders: list[Any]) -> float:
    """Sum gross notional over submitted (non-excluded) orders. Pure."""

    return sum(order_notional(o) for o in (orders or []) if order_is_submitted(o))


def daily_notional_from_attribution(
    attribution_entries: list[Any], *, now: datetime | None = None
) -> float:
    """Fallback: sum ``quantity * entry_price`` for today's broker-submitted entries.

    Scoped to the current ET trading date; prior-day and non-submitted records are
    excluded. Never uses LLM output as the authority — only persisted broker-
    submitted attribution records.
    """

    today = ny_trading_date(now).isoformat()
    total = 0.0
    for entry in (attribution_entries or []):
        if not _read(entry, "broker_submitted"):
            continue
        ts = str(_read(entry, "timestamp") or "")
        try:
            entry_date = ny_trading_date(datetime.fromisoformat(ts)).isoformat() if ts else None
        except (TypeError, ValueError):
            entry_date = None
        if entry_date != today:
            continue
        qty = _to_float(_read(entry, "quantity")) or 0.0
        price = _to_float(_read(entry, "entry_price")) or 0.0
        if qty > 0 and price > 0:
            total += abs(qty * price)
    return total


# --- notional of a *prospective* order (the thing we are about to submit) -----


def proposal_order_notional(decision: Any, proposal: Any) -> float:
    """Gross notional of an entry order from its deterministic risk decision.

    Uses the risk engine's ``approved_notional`` when present, else
    ``approved_quantity * estimated_price``. Never derived from LLM output.
    """

    approved_notional = _to_float(_read(decision, "approved_notional"))
    if approved_notional is not None and approved_notional > 0:
        return abs(approved_notional)
    qty = _to_float(_read(decision, "approved_quantity")) or 0.0
    price = _to_float(_read(proposal, "estimated_price")) or 0.0
    return abs(qty * price)


def sell_to_close_notional(approved_qty: float, price: float | None) -> float:
    """Gross notional of a sell-to-close order (counts toward the cap)."""

    qty = _to_float(approved_qty) or 0.0
    px = _to_float(price) or 0.0
    return abs(qty * px)


# --- cap enforcement ----------------------------------------------------------


def would_exceed_cap(used: float, next_notional: float, cap: float | None) -> bool:
    """True when submitting ``next_notional`` would push usage over the cap.

    A non-positive or ``None`` cap means "no notional cap configured" (never
    blocks). An unpriced order (``next_notional <= 0``) cannot be shown to exceed
    the cap and is allowed through (other deterministic gates still apply).
    """

    if cap is None or cap <= 0:
        return False
    if next_notional <= 0:
        return False
    return (used + next_notional) > cap + 1e-9


def cap_rejection_reason(used: float, next_notional: float, cap: float | None) -> str:
    return (
        f"Daily notional cap reached: used ${used:,.2f} + next ${next_notional:,.2f} "
        f"= ${used + next_notional:,.2f} > cap ${(cap or 0.0):,.2f} "
        "(MAX_DAILY_NOTIONAL_PER_TEAM; entries + sell-to-close both count)."
    )


__all__ = [
    "EXCLUDED_ORDER_STATUSES", "NotionalReconciliation",
    "order_status", "order_is_submitted", "order_notional",
    "daily_notional_from_orders", "daily_notional_from_attribution",
    "proposal_order_notional", "sell_to_close_notional",
    "would_exceed_cap", "cap_rejection_reason",
]
