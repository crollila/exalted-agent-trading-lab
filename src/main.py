from __future__ import annotations

import argparse
from pathlib import Path

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.config.settings import Settings
from src.db.database import initialize_database
from src.execution.local_runner import run_strategy_dry_run
from src.reporting.report_generator import format_report, generate_daily_report
from src.reporting.strategy_comparison import format_strategy_comparison, save_strategy_comparison_artifacts
from src.strategies.base import Strategy
from src.strategies.cash_only import CashOnlyStrategy
from src.strategies.momentum_v1 import MomentumV1Strategy
from src.strategies.spy_buy_hold import SpyBuyHoldStrategy


KNOWN_STRATEGIES = ("cash_only", "spy_buy_hold", "momentum_v1")
DEFAULT_COMPARISON_STRATEGIES = ("cash_only", "spy_buy_hold", "momentum_v1")
COMPARISON_FIXTURES = ("flat", "multi_day")


def run_init_db() -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    print(f"Initialized database at {settings.database_path}")


def build_strategy(strategy_name: str) -> Strategy:
    if strategy_name == "cash_only":
        return CashOnlyStrategy()
    if strategy_name == "spy_buy_hold":
        return SpyBuyHoldStrategy()
    if strategy_name == "momentum_v1":
        return MomentumV1Strategy()
    raise ValueError(f"Unknown strategy: {strategy_name}")


def run_dry_run(strategy_name: str = "spy_buy_hold") -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    strategy = build_strategy(strategy_name)
    result = run_strategy_dry_run(strategy, settings)

    print(
        f"Dry run complete. Strategy: {result.strategy_id}. "
        f"Run ID: {result.run_id}. Proposals processed: {result.proposal_count}. Daily report logged."
    )


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


def run_compare_strategies(
    strategy_names: tuple[str, ...] = DEFAULT_COMPARISON_STRATEGIES,
    fixture: str = "multi_day",
    save: bool = False,
    output_dir: Path | str = Path("data/experiments"),
) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)

    reports: list[dict] = []
    for strategy_name in strategy_names:
        strategy = build_strategy(strategy_name)
        local_result = run_strategy_dry_run(strategy, settings, simulation_fixture=fixture)
        report_result = generate_daily_report(settings.database_path, run_id=local_result.run_id)
        if not report_result.ok or report_result.report is None:
            print(f"Comparison unavailable for {strategy.strategy_id}: {report_result.message}")
            raise SystemExit(1)
        reports.append(report_result.report)

    print(format_strategy_comparison(reports))
    if save:
        artifacts = save_strategy_comparison_artifacts(
            reports=reports,
            fixture_name=fixture,
            output_dir=output_dir,
        )
        print("Saved comparison artifacts:")
        print(f"JSON: {artifacts.json_path}")
        print(f"CSV: {artifacts.csv_path}")
        print(f"Markdown: {artifacts.markdown_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ExaltedFable Agent Trading Lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize SQLite database")
    dry_run_parser = subparsers.add_parser("dry-run", help="Run a local dry-run strategy cycle")
    dry_run_parser.add_argument(
        "--strategy",
        choices=KNOWN_STRATEGIES,
        default="spy_buy_hold",
        help="Local deterministic strategy to run. Defaults to spy_buy_hold.",
    )
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
    compare_parser = subparsers.add_parser(
        "compare-strategies",
        help="Run local dry-run strategies and print a run-aware comparison",
    )
    compare_parser.add_argument(
        "--strategies",
        nargs="+",
        choices=KNOWN_STRATEGIES,
        default=DEFAULT_COMPARISON_STRATEGIES,
        help="Local strategies to compare. Defaults to cash_only, spy_buy_hold, and momentum_v1.",
    )
    compare_parser.add_argument(
        "--fixture",
        choices=COMPARISON_FIXTURES,
        default="multi_day",
        help="Deterministic local simulation fixture for comparison reports. Defaults to multi_day.",
    )
    compare_parser.add_argument(
        "--save",
        action="store_true",
        help="Save JSON, CSV, and Markdown comparison artifacts to the output directory.",
    )
    compare_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory for saved comparison artifacts. Defaults to data/experiments.",
    )

    args = parser.parse_args()

    if args.command == "init-db":
        run_init_db()
    elif args.command == "dry-run":
        run_dry_run(strategy_name=args.strategy)
    elif args.command == "paper-status":
        run_paper_status()
    elif args.command == "report":
        run_report(run_id=args.run_id)
    elif args.command == "compare-strategies":
        run_compare_strategies(
            strategy_names=tuple(args.strategies),
            fixture=args.fixture,
            save=args.save,
            output_dir=args.output_dir,
        )
    else:
        raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
