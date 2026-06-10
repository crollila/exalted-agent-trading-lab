from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.brokers.order_models import BenchmarkSnapshot, PortfolioSnapshot, TradeAction
from src.db.database import insert_benchmark_snapshot, insert_portfolio_snapshot


@dataclass(frozen=True)
class ApprovedSimulationTrade:
    symbol: str
    action: TradeAction
    quantity: float
    estimated_price: float


@dataclass(frozen=True)
class FixtureDay:
    timestamp: datetime
    close_prices: dict[str, float]


MULTI_DAY_FIXTURE: tuple[FixtureDay, ...] = (
    FixtureDay(
        timestamp=datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc),
        close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
    ),
    FixtureDay(
        timestamp=datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc),
        close_prices={"SPY": 505.0, "NVDA": 118.0, "MSFT": 106.0, "AAPL": 99.0},
    ),
    FixtureDay(
        timestamp=datetime(2026, 1, 6, 21, 0, tzinfo=timezone.utc),
        close_prices={"SPY": 495.0, "NVDA": 106.0, "MSFT": 103.0, "AAPL": 97.0},
    ),
    FixtureDay(
        timestamp=datetime(2026, 1, 7, 21, 0, tzinfo=timezone.utc),
        close_prices={"SPY": 510.0, "NVDA": 124.0, "MSFT": 108.0, "AAPL": 96.0},
    ),
    FixtureDay(
        timestamp=datetime(2026, 1, 8, 21, 0, tzinfo=timezone.utc),
        close_prices={"SPY": 515.0, "NVDA": 130.0, "MSFT": 110.0, "AAPL": 100.0},
    ),
)


def multi_day_fixture_spy_return() -> float:
    first_price = MULTI_DAY_FIXTURE[0].close_prices["SPY"]
    latest_price = MULTI_DAY_FIXTURE[-1].close_prices["SPY"]
    return (latest_price - first_price) / first_price


def insert_multi_day_simulation_snapshots(
    database_path: Path | str,
    run_id: str,
    strategy_id: str,
    starting_equity: float,
    approved_trades: list[ApprovedSimulationTrade],
) -> None:
    cash, positions = _apply_approved_trades(starting_equity, approved_trades)
    starting_spy_price = MULTI_DAY_FIXTURE[0].close_prices["SPY"]

    for day in MULTI_DAY_FIXTURE:
        equity = _equity_for_day(cash=cash, positions=positions, close_prices=day.close_prices)
        insert_portfolio_snapshot(
            database_path,
            PortfolioSnapshot(
                strategy_id=strategy_id,
                equity=equity,
                cash=cash,
                timestamp=day.timestamp,
            ),
            run_id=run_id,
        )
        insert_benchmark_snapshot(
            database_path,
            BenchmarkSnapshot(
                starting_equity=starting_equity,
                current_strategy_equity=equity,
                starting_benchmark_price=starting_spy_price,
                current_benchmark_price=day.close_prices["SPY"],
                timestamp=day.timestamp,
            ),
            run_id=run_id,
        )


def _apply_approved_trades(
    starting_equity: float,
    approved_trades: list[ApprovedSimulationTrade],
) -> tuple[float, dict[str, float]]:
    cash = starting_equity
    positions: dict[str, float] = {}

    for trade in approved_trades:
        trade_value = trade.quantity * trade.estimated_price
        if trade.action == TradeAction.BUY:
            cash -= trade_value
            positions[trade.symbol] = positions.get(trade.symbol, 0.0) + trade.quantity
        elif trade.action == TradeAction.SELL:
            cash += trade_value
            positions[trade.symbol] = positions.get(trade.symbol, 0.0) - trade.quantity

    return cash, {symbol: quantity for symbol, quantity in positions.items() if quantity > 0}


def _equity_for_day(cash: float, positions: dict[str, float], close_prices: dict[str, float]) -> float:
    missing_symbols = sorted(symbol for symbol in positions if symbol not in close_prices)
    if missing_symbols:
        raise ValueError(f"Multi-day fixture is missing close prices for: {', '.join(missing_symbols)}")

    return cash + sum(quantity * close_prices[symbol] for symbol, quantity in positions.items())
