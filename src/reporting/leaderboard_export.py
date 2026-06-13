from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.reporting.strategy_status import DEFAULT_STRATEGY_STATUS_PATH, load_latest_strategy_statuses, strategy_status_for
from src.reporting.strategy_comparison import SCORE_FORMULA
from src.reporting.tournament_champion import TournamentChampionResult, load_tournament_champion
from src.reporting.tournament_history import TournamentHistoryEntry


@dataclass(frozen=True)
class LeaderboardExportResult:
    saved: bool
    report_path: Path
    message: str


@dataclass(frozen=True)
class StrategyLeaderboardRow:
    strategy_id: str
    appearances: int
    wins: int
    win_rate: float
    best_score: float
    average_score: float
    average_excess_return: float
    worst_max_drawdown: float


def export_strategy_leaderboard(
    output_dir: Path | str = Path("data/experiments"),
    report_path: Path | str = Path("data/reports/strategy_leaderboard.md"),
    generated_at: datetime | None = None,
    status_registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
) -> LeaderboardExportResult:
    active_report_path = Path(report_path)
    champion_result = load_tournament_champion(output_dir)
    if champion_result.summary is None:
        if champion_result.artifact_directory_exists:
            message = f"No valid tournament artifacts found in {Path(output_dir)}. No report written."
        else:
            message = f"No tournament artifacts found in {Path(output_dir)}. No report written."
        return LeaderboardExportResult(saved=False, report_path=active_report_path, message=message)

    markdown = format_strategy_leaderboard(
        champion_result=champion_result,
        output_dir=output_dir,
        generated_at=generated_at,
        status_by_strategy=load_latest_strategy_statuses(status_registry_path),
    )
    active_report_path.parent.mkdir(parents=True, exist_ok=True)
    active_report_path.write_text(markdown, encoding="utf-8")
    return LeaderboardExportResult(
        saved=True,
        report_path=active_report_path,
        message=f"Saved strategy leaderboard report: {active_report_path}",
    )


def format_strategy_leaderboard(
    champion_result: TournamentChampionResult,
    output_dir: Path | str,
    generated_at: datetime | None = None,
    status_by_strategy: dict[str, str] | None = None,
) -> str:
    if champion_result.summary is None:
        raise ValueError("Cannot format leaderboard without valid tournament artifacts.")

    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    summary = champion_result.summary
    aggregate_rows = _strategy_leaderboard_rows(champion_result.history.entries)
    recent_entries = champion_result.history.entries[:10]

    lines = [
        "# Strategy Leaderboard",
        "",
        f"Generated timestamp: {timestamp}",
        f"Artifact source directory: `{Path(output_dir)}`",
        "",
        "## Current Champion",
        "",
        f"- Champion strategy ID: `{summary.champion_strategy_id}`",
        f"- Champion strategy status: `{strategy_status_for(summary.champion_strategy_id, status_by_strategy)}`",
        f"- Valid tournaments reviewed: {summary.valid_tournaments_reviewed}",
        f"- Wins: {summary.champion_wins}",
        f"- Win rate: {_percent(summary.champion_win_rate)}",
        f"- Best score: {_score(summary.champion_best_score)}",
        f"- Average score: {_score(summary.champion_average_score)}",
        f"- Average excess return: {_percent(summary.champion_average_excess_return)}",
        f"- Worst max drawdown: {_percent(summary.champion_worst_max_drawdown)}",
        f"- Most recent win timestamp: {summary.most_recent_win_timestamp or 'none'}",
        f"- Fixtures appeared: {', '.join(summary.fixtures_appeared)}",
        f"- Skipped/malformed artifact count: {summary.skipped_artifact_count}",
        "",
        "## Score Formula",
        "",
        f"`{SCORE_FORMULA}`",
        "",
        "## Safety Disclaimer",
        "",
        "- This is dry-run/local research only.",
        "- This is not live trading.",
        "- No options.",
        "- No margin.",
        "- No shorting.",
        "- Hermes runtime is disabled.",
        "",
        "## Recent Tournaments",
        "",
        _markdown_table(
            headers=(
                "Timestamp",
                "Fixture",
                "Strategies",
                "Winner",
                "Score",
                "Strategy Return",
                "SPY Return",
                "Excess Return",
                "Max Drawdown",
            ),
            rows=[
                (
                    entry.experiment_timestamp,
                    entry.fixture_name,
                    str(entry.strategy_count),
                    entry.winning_strategy_id,
                    _score(entry.winning_score),
                    _percent(entry.winning_strategy_return),
                    _percent(entry.winning_spy_return),
                    _percent(entry.winning_excess_return),
                    _percent(entry.winning_max_drawdown),
                )
                for entry in recent_entries
            ],
        ),
        "",
        "## Strategy Aggregates",
        "",
        _markdown_table(
            headers=(
                "Strategy ID",
                "Status",
                "Appearances",
                "Wins",
                "Win Rate",
                "Best Score",
                "Average Score",
                "Average Excess Return",
                "Worst Max Drawdown",
            ),
            rows=[
                (
                    row.strategy_id,
                    strategy_status_for(row.strategy_id, status_by_strategy),
                    str(row.appearances),
                    str(row.wins),
                    _percent(row.win_rate),
                    _score(row.best_score),
                    _score(row.average_score),
                    _percent(row.average_excess_return),
                    _percent(row.worst_max_drawdown),
                )
                for row in aggregate_rows
            ],
        ),
        "",
        "## Fixture Caveats",
        "",
        (
            "Deterministic fixtures are useful for repeatable local comparisons, but they are not proof of a "
            "real trading edge. Live markets include changing liquidity, slippage, fees, news, regime shifts, "
            "and execution risks that these fixtures do not model."
        ),
        "",
    ]

    if champion_result.history.skipped_artifacts:
        lines.extend(
            [
                "## Skipped Artifacts",
                "",
                f"Skipped/malformed artifact count: {len(champion_result.history.skipped_artifacts)}",
                "",
            ]
        )
        lines.extend(
            f"- `{skipped.artifact_path}`: {skipped.reason}"
            for skipped in champion_result.history.skipped_artifacts
        )
        lines.append("")

    return "\n".join(lines)


def _strategy_leaderboard_rows(entries: list[TournamentHistoryEntry]) -> list[StrategyLeaderboardRow]:
    aggregates: dict[str, dict[str, list[float] | int]] = {}
    for entry in entries:
        for result in entry.strategy_results:
            aggregate = aggregates.setdefault(
                result.strategy_id,
                {
                    "wins": 0,
                    "scores": [],
                    "excess_returns": [],
                    "max_drawdowns": [],
                },
            )
            if result.rank == 1:
                aggregate["wins"] += 1
            aggregate["scores"].append(result.score)
            aggregate["excess_returns"].append(result.excess_return)
            aggregate["max_drawdowns"].append(result.max_drawdown)

    rows = [
        StrategyLeaderboardRow(
            strategy_id=strategy_id,
            appearances=len(aggregate["scores"]),
            wins=aggregate["wins"],
            win_rate=aggregate["wins"] / len(aggregate["scores"]),
            best_score=max(aggregate["scores"]),
            average_score=_average(aggregate["scores"]),
            average_excess_return=_average(aggregate["excess_returns"]),
            worst_max_drawdown=min(aggregate["max_drawdowns"]),
        )
        for strategy_id, aggregate in aggregates.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -row.wins,
            -row.average_score,
            -row.best_score,
            -row.average_excess_return,
            abs(row.worst_max_drawdown),
            row.strategy_id,
        ),
    )


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _score(value: float) -> str:
    return f"{value:.4f}"
