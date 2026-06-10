from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from src.brokers.order_models import BenchmarkSnapshot, PortfolioSnapshot
from src.config.settings import Settings
from src.db.database import (
    initialize_database,
    insert_benchmark_snapshot,
    insert_daily_report,
    insert_portfolio_snapshot,
)
from src.execution.order_executor import OrderExecutor
from src.strategies.spy_buy_hold import SpyBuyHoldStrategy
from src.portfolio.portfolio_state import PortfolioState
from src.reporting.benchmark_report import BenchmarkReport
from src.risk.risk_rules import RiskRules
from src.risk.trade_validator import TradeValidator


def run_init_db() -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    print(f"Initialized database at {settings.database_path}")


def run_dry_run() -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)

    portfolio = PortfolioState(
        equity=settings.starting_equity,
        cash=settings.starting_equity,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )

    strategy = SpyBuyHoldStrategy()
    proposals = strategy.generate_proposals(portfolio)

    validator = TradeValidator(
        rules=RiskRules(
            min_cash_pct=settings.min_cash_pct,
            max_position_pct=settings.max_position_pct,
            max_daily_turnover_pct=settings.max_daily_turnover_pct,
            max_new_positions_per_day=settings.max_new_positions_per_day,
        )
    )
    executor = OrderExecutor(database_path=settings.database_path, dry_run=True)

    insert_portfolio_snapshot(
        settings.database_path,
        PortfolioSnapshot(
            strategy_id=strategy.strategy_id,
            equity=portfolio.equity,
            cash=portfolio.cash,
            timestamp=portfolio.timestamp,
        ),
    )

    for proposal in proposals:
        decision = validator.validate(proposal=proposal, portfolio=portfolio)
        executor.handle_decision(proposal=proposal, decision=decision)

    starting_spy_price = 500.0
    current_spy_price = 500.0
    report = BenchmarkReport(
        starting_equity=settings.starting_equity,
        current_strategy_equity=portfolio.equity,
        starting_spy_price=starting_spy_price,
        current_spy_price=current_spy_price,
    ).to_dict()

    insert_benchmark_snapshot(
        settings.database_path,
        BenchmarkSnapshot(
            starting_equity=settings.starting_equity,
            current_strategy_equity=portfolio.equity,
            starting_benchmark_price=starting_spy_price,
            current_benchmark_price=current_spy_price,
            timestamp=portfolio.timestamp,
        ),
    )
    insert_daily_report(
        settings.database_path,
        strategy_id=strategy.strategy_id,
        report_date=date.today().isoformat(),
        report=report,
    )

    print(f"Dry run complete. Proposals processed: {len(proposals)}. Daily report logged.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ExaltedFable Agent Trading Lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize SQLite database")
    subparsers.add_parser("dry-run", help="Run a local dry-run strategy cycle")

    args = parser.parse_args()

    if args.command == "init-db":
        run_init_db()
    elif args.command == "dry-run":
        run_dry_run()
    else:
        raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
