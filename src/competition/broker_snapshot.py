"""Fresh broker-state grounding for the competition cycle (Phase 7Z).

One immutable snapshot of a team's *current* paper account + positions, fetched
ONCE per team per market-open full cycle and threaded — unchanged — into:

* the Portfolio Manager evaluation,
* the proposal / candidate-generation context,
* routing / execution context,
* the cycle audit.

The point is to stop stale historical memory (old XYZ/SPY holdings, prior short
exposure, an old low-buying-power lesson) from masquerading as live portfolio
state. If the broker read fails, the snapshot is marked ``account_state_unavailable``
and callers must NOT pretend positions are zero or cash is available.

Read-only: this module only calls ``get_account`` / ``get_positions``. It never
submits an order, never sizes a trade, and never prints secrets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from src.competition.position_review import read_position_fields
from src.competition.risk_engine import AccountContext

# Snapshot read statuses (stable strings used by tests, audit, and diagnostics).
STATUS_OK = "ok"
STATUS_ACCOUNT_STATE_UNAVAILABLE = "account_state_unavailable"

# Source labels.
SOURCE_LIVE = "live_team_paper_account"
SOURCE_UNAVAILABLE = "account_state_unavailable"


@dataclass(frozen=True)
class BrokerSnapshot:
    """Immutable current-cycle broker facts for one team. No secrets.

    ``account_read_ok`` is the single source of truth for "did we actually read
    the live account this cycle". When it is False, equity/cash/buying_power are
    ``None`` and ``status`` is ``account_state_unavailable`` — callers must treat
    the account as *unknown*, never as flat/funded.
    """

    team_id: str
    source: str
    snapshot_time: str
    account_read_ok: bool
    status: str
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    position_count: int | None = None
    long_position_count: int | None = None
    short_position_count: int | None = None
    held_symbols: tuple[str, ...] = ()
    short_symbols: tuple[str, ...] = ()
    as_of: date | None = None
    classification: str | None = None  # broker auth classification when unavailable
    # Reconciled usage carried alongside the snapshot so the AccountContext built
    # from it keeps the daily caps engaged (never re-fetched downstream).
    orders_today: int = 0
    daily_notional_today: float = 0.0
    positions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_available(self) -> bool:
        return self.account_read_ok and self.status == STATUS_OK

    @property
    def is_flat(self) -> bool:
        """True only when we KNOW (live read) the account holds no positions."""

        return self.is_available and (self.position_count or 0) == 0

    @property
    def has_short_exposure(self) -> bool:
        return self.is_available and (self.short_position_count or 0) > 0

    def buying_power_ratio(self) -> float | None:
        if not self.is_available or not self.equity or self.equity <= 0:
            return None
        bp = self.buying_power if self.buying_power is not None else self.cash
        if bp is None:
            return None
        return bp / self.equity

    def to_account_context(self, *, starting_equity_fallback: float | None = None) -> AccountContext:
        """Build the deterministic AccountContext used by risk math.

        When the live read failed we fall back to a deterministic context (so the
        cycle can still run review-only paths) but the snapshot ``status`` already
        records that the account was unavailable — the classifier downstream marks
        the cycle ``account_state_unavailable`` rather than inventing a flat book.
        """

        if self.is_available:
            return AccountContext(
                equity=float(self.equity or 0.0),
                cash=float(self.cash or 0.0),
                buying_power=(float(self.buying_power) if self.buying_power is not None else None),
                orders_today=self.orders_today,
                daily_notional_today=self.daily_notional_today,
                as_of=self.as_of,
            )
        equity = float(starting_equity_fallback or 0.0)
        return AccountContext(
            equity=equity,
            cash=equity,
            buying_power=(equity * 2.0 if equity else None),
            orders_today=self.orders_today,
            daily_notional_today=self.daily_notional_today,
            as_of=self.as_of,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "source": self.source,
            "snapshot_time": self.snapshot_time,
            "account_read_ok": self.account_read_ok,
            "status": self.status,
            "equity": self.equity,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "position_count": self.position_count,
            "long_position_count": self.long_position_count,
            "short_position_count": self.short_position_count,
            "held_symbols": list(self.held_symbols),
            "short_symbols": list(self.short_symbols),
            "classification": self.classification,
            "orders_today": self.orders_today,
            "daily_notional_today": self.daily_notional_today,
        }


def _coerce_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def summarize_positions(raw_positions: list[Any]) -> dict[str, Any]:
    """Deterministically summarize raw broker positions. No broker calls."""

    held: list[str] = []
    shorts: list[str] = []
    normalized: list[dict[str, Any]] = []
    for raw in raw_positions or []:
        fields = read_position_fields(raw)
        symbol = fields.get("symbol") or ""
        if not symbol:
            continue
        normalized.append(fields)
        held.append(symbol)
        if fields.get("side") == "short":
            shorts.append(symbol)
    return {
        "position_count": len(normalized),
        "long_position_count": len(normalized) - len(shorts),
        "short_position_count": len(shorts),
        "held_symbols": tuple(dict.fromkeys(held)),
        "short_symbols": tuple(dict.fromkeys(shorts)),
        "positions": tuple(normalized),
    }


def build_snapshot_from_parts(
    team_id: str,
    *,
    account: Any | None,
    raw_positions: list[Any] | None,
    account_read_ok: bool,
    source: str = SOURCE_LIVE,
    classification: str | None = None,
    orders_today: int = 0,
    daily_notional_today: float = 0.0,
    as_of: date | None = None,
    now: datetime | None = None,
) -> BrokerSnapshot:
    """Assemble a :class:`BrokerSnapshot` from already-fetched parts (pure).

    ``account`` is the team account object/dict (``equity``/``cash``/
    ``buying_power``). When ``account_read_ok`` is False the snapshot is marked
    ``account_state_unavailable`` and exposes no account numbers — positions are
    treated as *unknown* (``None``), never zero.
    """

    now = now or datetime.now(timezone.utc)
    timestamp = now.isoformat()

    if not account_read_ok or account is None:
        return BrokerSnapshot(
            team_id=team_id,
            source=SOURCE_UNAVAILABLE,
            snapshot_time=timestamp,
            account_read_ok=False,
            status=STATUS_ACCOUNT_STATE_UNAVAILABLE,
            classification=classification,
            orders_today=orders_today,
            daily_notional_today=daily_notional_today,
            as_of=as_of,
        )

    def _read(name: str) -> Any:
        if isinstance(account, dict):
            return account.get(name)
        return getattr(account, name, None)

    equity = _coerce_float(_read("equity"))
    cash = _coerce_float(_read("cash"))
    buying_power = _coerce_float(_read("buying_power"))
    pos = summarize_positions(list(raw_positions or []))

    return BrokerSnapshot(
        team_id=team_id,
        source=source,
        snapshot_time=timestamp,
        account_read_ok=True,
        status=STATUS_OK,
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        position_count=pos["position_count"],
        long_position_count=pos["long_position_count"],
        short_position_count=pos["short_position_count"],
        held_symbols=pos["held_symbols"],
        short_symbols=pos["short_symbols"],
        classification=classification,
        orders_today=orders_today,
        daily_notional_today=daily_notional_today,
        as_of=as_of,
        positions=pos["positions"],
    )


def unavailable_snapshot(
    team_id: str,
    *,
    classification: str | None = None,
    orders_today: int = 0,
    daily_notional_today: float = 0.0,
    as_of: date | None = None,
    now: datetime | None = None,
) -> BrokerSnapshot:
    """A snapshot for a team whose live paper account could not be read."""

    return build_snapshot_from_parts(
        team_id,
        account=None,
        raw_positions=None,
        account_read_ok=False,
        classification=classification,
        orders_today=orders_today,
        daily_notional_today=daily_notional_today,
        as_of=as_of,
        now=now,
    )


__all__ = [
    "STATUS_OK",
    "STATUS_ACCOUNT_STATE_UNAVAILABLE",
    "SOURCE_LIVE",
    "SOURCE_UNAVAILABLE",
    "BrokerSnapshot",
    "summarize_positions",
    "build_snapshot_from_parts",
    "unavailable_snapshot",
]
