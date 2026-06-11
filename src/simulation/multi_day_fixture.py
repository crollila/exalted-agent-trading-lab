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


MULTI_DAY_FIXTURE_NAME = "multi_day"
SCENARIO_FIXTURE_NAMES = (
    "bull_trend",
    "bear_trend",
    "sideways_chop",
    "volatile_reversal",
    "spy_outperformance",
    "momentum_crash",
)


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


SCENARIO_FIXTURES: dict[str, tuple[FixtureDay, ...]] = {
    MULTI_DAY_FIXTURE_NAME: MULTI_DAY_FIXTURE,
    "bull_trend": (
        FixtureDay(
            timestamp=datetime(2026, 2, 2, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 2, 3, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 506.0, "NVDA": 118.0, "MSFT": 106.0, "AAPL": 99.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 2, 4, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 512.0, "NVDA": 124.0, "MSFT": 109.0, "AAPL": 101.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 2, 5, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 520.0, "NVDA": 132.0, "MSFT": 112.0, "AAPL": 103.0},
        ),
    ),
    "bear_trend": (
        FixtureDay(
            timestamp=datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 3, 3, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 492.0, "NVDA": 106.0, "MSFT": 101.0, "AAPL": 96.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 3, 4, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 485.0, "NVDA": 101.0, "MSFT": 98.0, "AAPL": 94.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 3, 5, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 478.0, "NVDA": 96.0, "MSFT": 95.0, "AAPL": 92.0},
        ),
    ),
    "sideways_chop": (
        FixtureDay(
            timestamp=datetime(2026, 4, 2, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 4, 3, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 504.0, "NVDA": 116.0, "MSFT": 102.0, "AAPL": 99.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 4, 6, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 497.0, "NVDA": 108.0, "MSFT": 105.0, "AAPL": 97.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 4, 7, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 502.0, "NVDA": 113.0, "MSFT": 103.0, "AAPL": 98.0},
        ),
    ),
    "volatile_reversal": (
        FixtureDay(
            timestamp=datetime(2026, 5, 4, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 5, 5, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 485.0, "NVDA": 96.0, "MSFT": 94.0, "AAPL": 92.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 5, 6, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 506.0, "NVDA": 121.0, "MSFT": 108.0, "AAPL": 100.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 5, 7, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 514.0, "NVDA": 128.0, "MSFT": 111.0, "AAPL": 102.0},
        ),
    ),
    "spy_outperformance": (
        FixtureDay(
            timestamp=datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 6, 2, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 512.0, "NVDA": 113.0, "MSFT": 105.0, "AAPL": 99.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 6, 3, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 524.0, "NVDA": 114.0, "MSFT": 105.5, "AAPL": 99.5},
        ),
        FixtureDay(
            timestamp=datetime(2026, 6, 4, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 535.0, "NVDA": 115.0, "MSFT": 106.0, "AAPL": 100.0},
        ),
    ),
    "momentum_crash": (
        FixtureDay(
            timestamp=datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 7, 2, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 505.0, "NVDA": 118.0, "MSFT": 106.0, "AAPL": 99.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 7, 6, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 498.0, "NVDA": 90.0, "MSFT": 92.0, "AAPL": 97.0},
        ),
        FixtureDay(
            timestamp=datetime(2026, 7, 7, 21, 0, tzinfo=timezone.utc),
            close_prices={"SPY": 502.0, "NVDA": 84.0, "MSFT": 89.0, "AAPL": 96.0},
        ),
    ),
}

NAMED_SIMULATION_FIXTURES = tuple(SCENARIO_FIXTURES)


def multi_day_fixture_spy_return() -> float:
    return fixture_spy_return(MULTI_DAY_FIXTURE_NAME)


def fixture_spy_return(fixture_name: str) -> float:
    fixture = get_simulation_fixture(fixture_name)
    first_price = fixture[0].close_prices["SPY"]
    latest_price = fixture[-1].close_prices["SPY"]
    return (latest_price - first_price) / first_price


def get_simulation_fixture(fixture_name: str) -> tuple[FixtureDay, ...]:
    try:
        return SCENARIO_FIXTURES[fixture_name]
    except KeyError as exc:
        raise ValueError(f"Unknown simulation fixture: {fixture_name}") from exc


def insert_multi_day_simulation_snapshots(
    database_path: Path | str,
    run_id: str,
    strategy_id: str,
    starting_equity: float,
    approved_trades: list[ApprovedSimulationTrade],
    fixture_name: str = MULTI_DAY_FIXTURE_NAME,
) -> None:
    fixture = get_simulation_fixture(fixture_name)
    cash, positions = _apply_approved_trades(starting_equity, approved_trades)
    starting_spy_price = fixture[0].close_prices["SPY"]

    for day in fixture:
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
