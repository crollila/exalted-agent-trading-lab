from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.brokers.order_models import BenchmarkSnapshot, PortfolioSnapshot
from src.config.settings import Settings
from src.db.database import (
    complete_run,
    create_run,
    initialize_database,
    insert_benchmark_snapshot,
    insert_daily_report,
    insert_portfolio_snapshot,
)
from src.execution.order_executor import OrderExecutor
from src.strategies.spy_buy_hold import SpyBuyHoldStrategy
from src.portfolio.portfolio_state import PortfolioState
from src.reporting.benchmark_report import BenchmarkReport
from src.reporting.report_generator import format_report, generate_daily_report
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
    run_id = create_run(
        settings.database_path,
        strategy_id=strategy.strategy_id,
        strategy_name=strategy.name,
        starting_equity=settings.starting_equity,
    )
    proposals = strategy.generate_proposals(portfolio)

    validator = TradeValidator(
        rules=RiskRules(
            min_cash_pct=settings.min_cash_pct,
            max_position_pct=settings.max_position_pct,
            max_daily_turnover_pct=settings.max_daily_turnover_pct,
            max_new_positions_per_day=settings.max_new_positions_per_day,
        )
    )
    executor = OrderExecutor(database_path=settings.database_path, dry_run=True, run_id=run_id)

    try:
        insert_portfolio_snapshot(
            settings.database_path,
            PortfolioSnapshot(
                strategy_id=strategy.strategy_id,
                equity=portfolio.equity,
                cash=portfolio.cash,
                timestamp=portfolio.timestamp,
            ),
            run_id=run_id,
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
        report["run_id"] = run_id

        insert_benchmark_snapshot(
            settings.database_path,
            BenchmarkSnapshot(
                starting_equity=settings.starting_equity,
                current_strategy_equity=portfolio.equity,
                starting_benchmark_price=starting_spy_price,
                current_benchmark_price=current_spy_price,
                timestamp=portfolio.timestamp,
            ),
            run_id=run_id,
        )
        insert_daily_report(
            settings.database_path,
            strategy_id=strategy.strategy_id,
            report_date=date.today().isoformat(),
            report=report,
            run_id=run_id,
        )
        complete_run(settings.database_path, run_id)
    except Exception:
        complete_run(settings.database_path, run_id, status="failed")
        raise

    print(f"Dry run complete. Run ID: {run_id}. Proposals processed: {len(proposals)}. Daily report logged.")


def run_paper_status() -> None:
    settings = Settings.from_env()

    try:
        client = AlpacaClientWrapper(settings=settings)
        account = client.get_account()
        positions = client.get_positions()
        market_open = client.is_market_open()
    except (RuntimeError, ValueError) as exc:
        print(f"Paper status unavailable: {exc}")
        raise SystemExit(1) from exc

    print(f"Account equity: {_read_value(account, 'equity')}")
    print(f"Cash: {_read_value(account, 'cash')}")
    print(f"Buying power: {_read_value(account, 'buying_power')}")
    print(f"Market status: {'open' if market_open else 'closed'}")
    print(f"Positions count: {len(positions)}")


def _read_value(obj: object, name: str) -> object:
    if isinstance(obj, dict):
        return obj.get(name, "unknown")
    return getattr(obj, name, "unknown")


def run_report(run_id: str | None = None) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    result = generate_daily_report(settings.database_path, run_id=run_id)
    if not result.ok or result.report is None:
        print(f"Report unavailable: {result.message}")
        raise SystemExit(1)

    print(format_report(result.report))


def main() -> None:
    parser = argparse.ArgumentParser(description="ExaltedFable Agent Trading Lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize SQLite database")
    subparsers.add_parser("dry-run", help="Run a local dry-run strategy cycle")
    subparsers.add_parser("paper-status", help="Show Alpaca paper account status")
    report_parser = subparsers.add_parser("report", help="Generate a local benchmark report")
    report_parser.add_argument(
        "--run-id",
        help="Generate a report for a specific run ID. Defaults to the latest run.",
    )
    report_parser.add_argument(
        "--latest",
        action="store_true",
        help="Generate a report for the latest run. This is the default.",
    )

    args = parser.parse_args()

    if args.command == "init-db":
        run_init_db()
    elif args.command == "dry-run":
        run_dry_run()
    elif args.command == "paper-status":
        run_paper_status()
    elif args.command == "report":
        run_report(run_id=args.run_id)
    else:
        raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
