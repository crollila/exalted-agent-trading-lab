"""Read-only portfolio cockpit helpers for the Streamlit dashboard.

This module never submits orders. The optional live collection path uses the existing
Alpaca paper-only wrapper and degrades to an unavailable snapshot when credentials or
market data are missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.brokers.team_alpaca_config import load_team_alpaca_paper_config
from src.config.settings import Settings


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float | None
    market_value: float | None
    average_entry: float | None
    unrealized_pl: float | None
    side: str


@dataclass(frozen=True)
class TeamPortfolioSnapshot:
    team_id: str
    available: bool
    message: str
    equity: float | None
    cash: float | None
    buying_power: float | None
    market_open: bool | None
    positions: tuple[PositionSnapshot, ...]
    data_freshness: datetime

    @property
    def positions_count(self) -> int:
        return len(self.positions)


@dataclass(frozen=True)
class PortfolioComparison:
    alpha_equity: float | None
    beta_equity: float | None
    leader: str | None
    difference: float | None
    spy_benchmark_status: str


def _read_value(obj: object, *names: str) -> object | None:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def build_position_snapshot(raw_position: object) -> PositionSnapshot:
    """Normalize a broker position object/dict for display."""

    symbol = str(_read_value(raw_position, "symbol") or "unknown")
    qty = _to_float(_read_value(raw_position, "qty", "quantity"))
    market_value = _to_float(_read_value(raw_position, "market_value", "marketValue"))
    average_entry = _to_float(_read_value(raw_position, "avg_entry_price", "average_entry", "cost_basis"))
    unrealized_pl = _to_float(_read_value(raw_position, "unrealized_pl", "unrealized_pnl"))
    side = str(_read_value(raw_position, "side") or "long").lower()
    if side not in {"long", "short"}:
        side = "long"
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        market_value=market_value,
        average_entry=average_entry,
        unrealized_pl=unrealized_pl,
        side=side,
    )


def build_team_portfolio_snapshot(
    team_id: str,
    *,
    account: object | None,
    positions: Sequence[object] = (),
    market_open: bool | None,
    message: str = "paper account snapshot",
    now: datetime | None = None,
) -> TeamPortfolioSnapshot:
    """Build a display snapshot from already-fetched account/position data."""

    return TeamPortfolioSnapshot(
        team_id=team_id,
        available=account is not None,
        message=message,
        equity=_to_float(_read_value(account, "equity")) if account is not None else None,
        cash=_to_float(_read_value(account, "cash")) if account is not None else None,
        buying_power=_to_float(_read_value(account, "buying_power", "buyingPower")) if account is not None else None,
        market_open=market_open,
        positions=tuple(build_position_snapshot(position) for position in positions),
        data_freshness=now or datetime.now(timezone.utc),
    )


def unavailable_portfolio_snapshot(
    team_id: str,
    message: str,
    *,
    now: datetime | None = None,
) -> TeamPortfolioSnapshot:
    """Return a safe unavailable snapshot without leaking credentials."""

    return TeamPortfolioSnapshot(
        team_id=team_id,
        available=False,
        message=message,
        equity=None,
        cash=None,
        buying_power=None,
        market_open=None,
        positions=(),
        data_freshness=now or datetime.now(timezone.utc),
    )


def collect_team_portfolio_snapshot(
    team_id: str,
    *,
    base_settings: Settings | None = None,
    client_factory: Callable[[Settings], Any] | None = None,
    now: datetime | None = None,
) -> TeamPortfolioSnapshot:
    """Read a team's Alpaca paper account using existing paper-only validation."""

    try:
        team_config = load_team_alpaca_paper_config(team_id)
        team_settings = team_config.to_settings(base_settings)
        wrapper = AlpacaClientWrapper(settings=team_settings, client_factory=client_factory)
        account = wrapper.get_account()
        positions = wrapper.get_positions()
        market_open = wrapper.is_market_open()
    except Exception as exc:
        return unavailable_portfolio_snapshot(team_id, f"{team_id} paper account unavailable: {exc}", now=now)
    return build_team_portfolio_snapshot(
        team_id,
        account=account,
        positions=positions,
        market_open=market_open,
        now=now,
    )


def position_table_rows(snapshot: TeamPortfolioSnapshot) -> list[dict[str, object]]:
    """Rows for a positions table."""

    rows: list[dict[str, object]] = []
    for position in snapshot.positions:
        rows.append(
            {
                "symbol": position.symbol,
                "qty": position.qty,
                "market_value": position.market_value,
                "average_entry": position.average_entry,
                "unrealized_pl": position.unrealized_pl,
                "side": position.side,
            }
        )
    return rows


def allocation_rows(snapshot: TeamPortfolioSnapshot) -> list[dict[str, object]]:
    """Rows for a position-weight chart."""

    total = sum(position.market_value or 0.0 for position in snapshot.positions)
    if total <= 0:
        return []
    return [
        {
            "symbol": position.symbol,
            "market_value": position.market_value or 0.0,
            "weight_pct": ((position.market_value or 0.0) / total) * 100.0,
        }
        for position in snapshot.positions
        if (position.market_value or 0.0) > 0
    ]


def compare_team_portfolios(
    alpha: TeamPortfolioSnapshot,
    beta: TeamPortfolioSnapshot,
    *,
    spy_history_path: Path | str | None = None,
) -> PortfolioComparison:
    """Compare Alpha/Beta equity and report SPY benchmark availability."""

    leader = None
    difference = None
    if alpha.equity is not None and beta.equity is not None:
        difference = alpha.equity - beta.equity
        if difference > 0:
            leader = alpha.team_id
        elif difference < 0:
            leader = beta.team_id
        else:
            leader = "tie"

    spy_status = "SPY benchmark placeholder - no local SPY history wired yet."
    if spy_history_path is not None and Path(spy_history_path).is_file():
        spy_status = f"SPY benchmark data available at {spy_history_path}."
    return PortfolioComparison(
        alpha_equity=alpha.equity,
        beta_equity=beta.equity,
        leader=leader,
        difference=difference,
        spy_benchmark_status=spy_status,
    )


def portfolio_history_message(history_paths: Sequence[Path]) -> str:
    """Return a clear history status for the cockpit."""

    if not history_paths:
        return "No history yet - run a daily report or team cycle to collect data."
    latest = max(history_paths, key=lambda path: path.stat().st_mtime)
    return f"Latest local history/report file: {latest}"
