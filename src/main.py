from __future__ import annotations

import argparse
from pathlib import Path

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.config.settings import Settings
from src.db.database import initialize_database
from src.execution.local_runner import SIMULATION_FIXTURES, run_strategy_dry_run
from src.reporting.analysis_notes import create_strategy_analysis_note
from src.reporting.fixture_sweep import (
    format_fixture_sweep,
    save_fixture_sweep_artifacts,
    summarize_fixture_sweep,
)
from src.reporting.fixture_sweep_analysis_notes import create_sweep_analysis_note
from src.reporting.fixture_sweep_leaderboard_export import export_fixture_sweep_leaderboard
from src.reporting.leaderboard_export import export_strategy_leaderboard
from src.reporting.report_generator import format_report, generate_daily_report
from src.reporting.research_decisions import (
    ALLOWED_RESEARCH_DECISIONS,
    DEFAULT_DECISION_LEDGER_PATH,
    read_research_decision_ledger,
    record_research_decision,
)
from src.reporting.strategy_status import (
    ALLOWED_STRATEGY_STATUSES,
    DEFAULT_STRATEGY_STATUS_PATH,
    load_latest_strategy_statuses,
    read_strategy_status_registry,
    set_strategy_status,
)
from src.reporting.strategy_comparison import (
    format_strategy_comparison,
    rank_strategy_reports,
    save_strategy_comparison_artifacts,
)
from src.reporting.tournament_champion import format_tournament_champion, load_tournament_champion
from src.reporting.tournament_history import format_tournament_history, load_tournament_history
from src.strategies.base import Strategy
from src.strategies.cash_only import CashOnlyStrategy
from src.strategies.hermes_fixtures import (
    HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID,
    HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
    HermesAggressiveFixtureStrategy,
    HermesConservativeFixtureStrategy,
)
from src.strategies.momentum_v1 import MomentumV1Strategy
from src.strategies.spy_buy_hold import SpyBuyHoldStrategy


HERMES_FIXTURE_STRATEGIES = (
    HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
    HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID,
)
KNOWN_STRATEGIES = ("cash_only", "spy_buy_hold", "momentum_v1", *HERMES_FIXTURE_STRATEGIES)
DEFAULT_COMPARISON_STRATEGIES = ("cash_only", "spy_buy_hold", "momentum_v1")
COMPARISON_FIXTURES = SIMULATION_FIXTURES
FIXTURE_SWEEP_FIXTURES = tuple(fixture for fixture in COMPARISON_FIXTURES if fixture != "flat")


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
    if strategy_name == HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID:
        return HermesConservativeFixtureStrategy()
    if strategy_name == HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID:
        return HermesAggressiveFixtureStrategy()
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
    include_hermes_fixtures: bool = False,
) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    selected_strategy_names = _comparison_strategy_names(
        strategy_names=strategy_names,
        include_hermes_fixtures=include_hermes_fixtures,
    )

    reports: list[dict] = []
    for strategy_name in selected_strategy_names:
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


def run_fixture_sweep(
    strategy_names: tuple[str, ...] = DEFAULT_COMPARISON_STRATEGIES,
    include_hermes_fixtures: bool = False,
    save: bool = False,
    output_dir: Path | str = Path("data/experiments"),
) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    selected_strategy_names = _comparison_strategy_names(
        strategy_names=strategy_names,
        include_hermes_fixtures=include_hermes_fixtures,
    )

    ranked_results_by_fixture: dict[str, list[dict]] = {}
    for fixture in FIXTURE_SWEEP_FIXTURES:
        reports: list[dict] = []
        for strategy_name in selected_strategy_names:
            strategy = build_strategy(strategy_name)
            local_result = run_strategy_dry_run(strategy, settings, simulation_fixture=fixture)
            report_result = generate_daily_report(settings.database_path, run_id=local_result.run_id)
            if not report_result.ok or report_result.report is None:
                print(f"Fixture sweep unavailable for {fixture}/{strategy.strategy_id}: {report_result.message}")
                raise SystemExit(1)
            reports.append(report_result.report)
        ranked_results_by_fixture[fixture] = rank_strategy_reports(reports)

    summary = summarize_fixture_sweep(ranked_results_by_fixture)
    status_by_strategy = load_latest_strategy_statuses()
    print(format_fixture_sweep(summary, status_by_strategy=status_by_strategy))
    if save:
        artifacts = save_fixture_sweep_artifacts(
            summary=summary,
            output_dir=output_dir,
            status_by_strategy=status_by_strategy,
        )
        print("Saved fixture sweep artifacts:")
        print(f"JSON: {artifacts.json_path}")
        print(f"CSV: {artifacts.csv_path}")
        print(f"Markdown: {artifacts.markdown_path}")


def run_tournament_history(output_dir: Path | str = Path("data/experiments")) -> None:
    history = load_tournament_history(output_dir)
    print(format_tournament_history(history, output_dir=output_dir))


def run_tournament_champion(output_dir: Path | str = Path("data/experiments")) -> None:
    champion = load_tournament_champion(output_dir)
    print(
        format_tournament_champion(
            champion,
            output_dir=output_dir,
            status_by_strategy=load_latest_strategy_statuses(),
        )
    )


def run_export_leaderboard(
    output_dir: Path | str = Path("data/experiments"),
    report_path: Path | str = Path("data/reports/strategy_leaderboard.md"),
) -> None:
    result = export_strategy_leaderboard(output_dir=output_dir, report_path=report_path)
    print(result.message)


def run_export_fixture_sweep_leaderboard(
    output_dir: Path | str = Path("data/experiments"),
    report_path: Path | str = Path("data/reports/fixture_sweep_leaderboard.md"),
) -> None:
    result = export_fixture_sweep_leaderboard(output_dir=output_dir, report_path=report_path)
    print(result.message)


def run_create_analysis_note(
    output_dir: Path | str = Path("data/experiments"),
    notes_dir: Path | str = Path("data/notes"),
    force: bool = False,
) -> None:
    result = create_strategy_analysis_note(output_dir=output_dir, notes_dir=notes_dir, force=force)
    print(result.message)


def run_create_sweep_analysis_note(
    output_dir: Path | str = Path("data/experiments"),
    notes_dir: Path | str = Path("data/notes"),
    force: bool = False,
) -> None:
    result = create_sweep_analysis_note(output_dir=output_dir, notes_dir=notes_dir, force=force)
    print(result.message)


def run_record_research_decision(
    strategy_id: str,
    decision: str,
    reason: str,
    ledger_path: Path | str = DEFAULT_DECISION_LEDGER_PATH,
    source_note: Path | str | None = None,
    next_action: str | None = None,
) -> None:
    try:
        result = record_research_decision(
            strategy_id=strategy_id,
            decision=decision,
            reason=reason,
            ledger_path=ledger_path,
            source_note=source_note,
            next_action=next_action,
        )
    except ValueError as exc:
        print(f"Research decision unavailable: {exc}")
        raise SystemExit(1) from exc

    print(result.message)


def run_research_decisions(ledger_path: Path | str = DEFAULT_DECISION_LEDGER_PATH) -> None:
    result = read_research_decision_ledger(ledger_path=ledger_path)
    print(result.message)


def run_set_strategy_status(
    strategy_id: str,
    status: str,
    reason: str,
    registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
    source_note: Path | str | None = None,
    next_action: str | None = None,
) -> None:
    try:
        result = set_strategy_status(
            strategy_id=strategy_id,
            status=status,
            reason=reason,
            registry_path=registry_path,
            source_note=source_note,
            next_action=next_action,
        )
    except ValueError as exc:
        print(f"Strategy status unavailable: {exc}")
        raise SystemExit(1) from exc

    print(result.message)


def run_strategy_status(registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH) -> None:
    result = read_strategy_status_registry(registry_path=registry_path)
    print(result.message)


def _comparison_strategy_names(
    strategy_names: tuple[str, ...],
    include_hermes_fixtures: bool,
) -> tuple[str, ...]:
    if not include_hermes_fixtures:
        return strategy_names

    selected = list(strategy_names)
    for strategy_name in HERMES_FIXTURE_STRATEGIES:
        if strategy_name not in selected:
            selected.append(strategy_name)
    return tuple(selected)


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
        "--include-hermes-fixtures",
        action="store_true",
        help="Include parser-only local Hermes JSON fixture strategies in the comparison.",
    )
    compare_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory for saved comparison artifacts. Defaults to data/experiments.",
    )
    fixture_sweep_parser = subparsers.add_parser(
        "fixture-sweep",
        help="Run local strategy comparison across deterministic non-flat fixtures",
    )
    fixture_sweep_parser.add_argument(
        "--include-hermes-fixtures",
        action="store_true",
        help="Include parser-only local Hermes JSON fixture strategies in the sweep.",
    )
    fixture_sweep_parser.add_argument(
        "--save",
        action="store_true",
        help="Save JSON, CSV, and Markdown fixture sweep artifacts to the output directory.",
    )
    fixture_sweep_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory for saved fixture sweep artifacts. Defaults to data/experiments.",
    )
    history_parser = subparsers.add_parser(
        "tournament-history",
        help="Review saved local compare-strategies JSON artifacts",
    )
    history_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    champion_parser = subparsers.add_parser(
        "tournament-champion",
        help="Summarize the current champion strategy across saved tournaments",
    )
    champion_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    leaderboard_parser = subparsers.add_parser(
        "export-leaderboard",
        help="Export a Markdown strategy leaderboard from saved ranked tournaments",
    )
    leaderboard_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    leaderboard_parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/strategy_leaderboard.md"),
        help="Markdown report path. Defaults to data/reports/strategy_leaderboard.md.",
    )
    fixture_sweep_leaderboard_parser = subparsers.add_parser(
        "export-fixture-sweep-leaderboard",
        help="Export a Markdown fixture sweep robustness leaderboard",
    )
    fixture_sweep_leaderboard_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved fixture sweep JSON artifacts. Defaults to data/experiments.",
    )
    fixture_sweep_leaderboard_parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/fixture_sweep_leaderboard.md"),
        help="Markdown report path. Defaults to data/reports/fixture_sweep_leaderboard.md.",
    )
    analysis_note_parser = subparsers.add_parser(
        "create-analysis-note",
        help="Create a Markdown human review note from the latest saved ranked tournament",
    )
    analysis_note_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    analysis_note_parser.add_argument(
        "--notes-dir",
        type=Path,
        default=Path("data/notes"),
        help="Directory for analysis note Markdown files. Defaults to data/notes.",
    )
    analysis_note_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the analysis note if the deterministic filename already exists.",
    )
    sweep_analysis_note_parser = subparsers.add_parser(
        "create-sweep-analysis-note",
        help="Create a Markdown human review note from the latest saved fixture sweep",
    )
    sweep_analysis_note_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved fixture sweep JSON artifacts. Defaults to data/experiments.",
    )
    sweep_analysis_note_parser.add_argument(
        "--notes-dir",
        type=Path,
        default=Path("data/notes"),
        help="Directory for sweep analysis note Markdown files. Defaults to data/notes.",
    )
    sweep_analysis_note_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the sweep analysis note if the deterministic filename already exists.",
    )
    decision_parser = subparsers.add_parser(
        "record-research-decision",
        help="Append a local strategy research decision to the Markdown ledger",
    )
    decision_parser.add_argument("--strategy-id", required=True, help="Strategy ID the decision applies to.")
    decision_parser.add_argument(
        "--decision",
        choices=ALLOWED_RESEARCH_DECISIONS,
        required=True,
        help="Research decision for the strategy.",
    )
    decision_parser.add_argument("--reason", required=True, help="Human-readable reason for the decision.")
    decision_parser.add_argument(
        "--source-note",
        type=Path,
        help="Optional source analysis note path.",
    )
    decision_parser.add_argument("--next-action", help="Optional follow-up action to test next.")
    decision_parser.add_argument(
        "--ledger-path",
        type=Path,
        default=DEFAULT_DECISION_LEDGER_PATH,
        help="Markdown decision ledger path. Defaults to data/notes/research_decisions.md.",
    )
    read_decisions_parser = subparsers.add_parser(
        "research-decisions",
        help="Print the local strategy research decision ledger",
    )
    read_decisions_parser.add_argument(
        "--ledger-path",
        type=Path,
        default=DEFAULT_DECISION_LEDGER_PATH,
        help="Markdown decision ledger path. Defaults to data/notes/research_decisions.md.",
    )
    status_parser = subparsers.add_parser(
        "set-strategy-status",
        help="Append a local research status for a strategy",
    )
    status_parser.add_argument("--strategy-id", required=True, help="Strategy ID the status applies to.")
    status_parser.add_argument(
        "--status",
        choices=ALLOWED_STRATEGY_STATUSES,
        required=True,
        help="Research status for the strategy.",
    )
    status_parser.add_argument("--reason", required=True, help="Human-readable reason for the status.")
    status_parser.add_argument(
        "--source-note",
        type=Path,
        help="Optional source analysis note path.",
    )
    status_parser.add_argument("--next-action", help="Optional follow-up action to test next.")
    status_parser.add_argument(
        "--registry-path",
        type=Path,
        default=DEFAULT_STRATEGY_STATUS_PATH,
        help="Markdown strategy status registry path. Defaults to data/notes/strategy_status.md.",
    )
    read_status_parser = subparsers.add_parser(
        "strategy-status",
        help="Print the local strategy status registry",
    )
    read_status_parser.add_argument(
        "--registry-path",
        type=Path,
        default=DEFAULT_STRATEGY_STATUS_PATH,
        help="Markdown strategy status registry path. Defaults to data/notes/strategy_status.md.",
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
            include_hermes_fixtures=args.include_hermes_fixtures,
        )
    elif args.command == "fixture-sweep":
        run_fixture_sweep(
            include_hermes_fixtures=args.include_hermes_fixtures,
            save=args.save,
            output_dir=args.output_dir,
        )
    elif args.command == "tournament-history":
        run_tournament_history(output_dir=args.output_dir)
    elif args.command == "tournament-champion":
        run_tournament_champion(output_dir=args.output_dir)
    elif args.command == "export-leaderboard":
        run_export_leaderboard(output_dir=args.output_dir, report_path=args.report_path)
    elif args.command == "export-fixture-sweep-leaderboard":
        run_export_fixture_sweep_leaderboard(output_dir=args.output_dir, report_path=args.report_path)
    elif args.command == "create-analysis-note":
        run_create_analysis_note(output_dir=args.output_dir, notes_dir=args.notes_dir, force=args.force)
    elif args.command == "create-sweep-analysis-note":
        run_create_sweep_analysis_note(output_dir=args.output_dir, notes_dir=args.notes_dir, force=args.force)
    elif args.command == "record-research-decision":
        run_record_research_decision(
            strategy_id=args.strategy_id,
            decision=args.decision,
            reason=args.reason,
            ledger_path=args.ledger_path,
            source_note=args.source_note,
            next_action=args.next_action,
        )
    elif args.command == "research-decisions":
        run_research_decisions(ledger_path=args.ledger_path)
    elif args.command == "set-strategy-status":
        run_set_strategy_status(
            strategy_id=args.strategy_id,
            status=args.status,
            reason=args.reason,
            registry_path=args.registry_path,
            source_note=args.source_note,
            next_action=args.next_action,
        )
    elif args.command == "strategy-status":
        run_strategy_status(registry_path=args.registry_path)
    else:
        raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
