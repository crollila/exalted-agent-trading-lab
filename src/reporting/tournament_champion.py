from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.reporting.strategy_status import strategy_status_for
from src.reporting.tournament_history import (
    TournamentHistoryEntry,
    TournamentHistoryResult,
    load_tournament_history,
)


@dataclass(frozen=True)
class TournamentChampionSummary:
    champion_strategy_id: str
    valid_tournaments_reviewed: int
    champion_wins: int
    champion_win_rate: float
    champion_best_score: float
    champion_average_score: float
    champion_average_excess_return: float
    champion_worst_max_drawdown: float
    most_recent_win_timestamp: str | None
    fixtures_appeared: tuple[str, ...]
    skipped_artifact_count: int


@dataclass(frozen=True)
class TournamentChampionResult:
    summary: TournamentChampionSummary | None
    history: TournamentHistoryResult
    artifact_directory_exists: bool


@dataclass
class _StrategyAggregate:
    strategy_id: str
    wins: int
    scores: list[float]
    excess_returns: list[float]
    max_drawdowns: list[float]
    fixtures: set[str]
    win_timestamps: list[datetime]
    win_timestamp_texts: list[str]


def load_tournament_champion(output_dir: Path | str) -> TournamentChampionResult:
    output_path = Path(output_dir)
    history = load_tournament_history(output_path)
    if not history.entries:
        return TournamentChampionResult(
            summary=None,
            history=history,
            artifact_directory_exists=output_path.exists(),
        )

    aggregates = _aggregate_strategies(history.entries)
    champion = _select_champion(aggregates.values())
    return TournamentChampionResult(
        summary=_summary_from_aggregate(champion, history),
        history=history,
        artifact_directory_exists=output_path.exists(),
    )


def format_tournament_champion(
    result: TournamentChampionResult,
    output_dir: Path | str,
    status_by_strategy: dict[str, str] | None = None,
) -> str:
    lines = ["Tournament Champion", f"Artifact directory: {Path(output_dir)}"]

    if result.summary is None:
        if not result.artifact_directory_exists:
            lines.append("No tournament artifacts found.")
        else:
            lines.append("No valid tournament artifacts found.")
    else:
        summary = result.summary
        lines.extend(
            [
                f"Champion strategy ID: {summary.champion_strategy_id}",
                f"Champion strategy status: {strategy_status_for(summary.champion_strategy_id, status_by_strategy)}",
                f"Valid tournaments reviewed: {summary.valid_tournaments_reviewed}",
                f"Champion wins: {summary.champion_wins}",
                f"Champion win rate: {_percent(summary.champion_win_rate)}",
                f"Champion best score: {_score(summary.champion_best_score)}",
                f"Champion average score: {_score(summary.champion_average_score)}",
                f"Champion average excess return: {_percent(summary.champion_average_excess_return)}",
                f"Champion worst max drawdown: {_percent(summary.champion_worst_max_drawdown)}",
                f"Most recent win timestamp: {summary.most_recent_win_timestamp or 'none'}",
                f"Fixtures where champion appeared: {', '.join(summary.fixtures_appeared)}",
                f"Skipped/malformed artifact count: {summary.skipped_artifact_count}",
            ]
        )

    if result.history.skipped_artifacts:
        lines.append("")
        lines.append("Skipped malformed artifacts:")
        for skipped in result.history.skipped_artifacts:
            lines.append(f"- {skipped.artifact_path}: {skipped.reason}")

    return "\n".join(lines)


def _aggregate_strategies(entries: list[TournamentHistoryEntry]) -> dict[str, _StrategyAggregate]:
    aggregates: dict[str, _StrategyAggregate] = {}
    for entry in entries:
        for result in entry.strategy_results:
            aggregate = aggregates.setdefault(
                result.strategy_id,
                _StrategyAggregate(
                    strategy_id=result.strategy_id,
                    wins=0,
                    scores=[],
                    excess_returns=[],
                    max_drawdowns=[],
                    fixtures=set(),
                    win_timestamps=[],
                    win_timestamp_texts=[],
                ),
            )
            aggregate.scores.append(result.score)
            aggregate.excess_returns.append(result.excess_return)
            aggregate.max_drawdowns.append(result.max_drawdown)
            aggregate.fixtures.add(entry.fixture_name)
            if result.rank == 1:
                aggregate.wins += 1
                aggregate.win_timestamps.append(entry.sort_timestamp)
                aggregate.win_timestamp_texts.append(entry.experiment_timestamp)
    return aggregates


def _select_champion(aggregates) -> _StrategyAggregate:
    return sorted(
        aggregates,
        key=lambda aggregate: (
            -aggregate.wins,
            -_average(aggregate.scores),
            -max(aggregate.scores),
            -_average(aggregate.excess_returns),
            abs(min(aggregate.max_drawdowns)),
            aggregate.strategy_id,
        ),
    )[0]


def _summary_from_aggregate(
    champion: _StrategyAggregate,
    history: TournamentHistoryResult,
) -> TournamentChampionSummary:
    recent_win_timestamp = _most_recent_win_timestamp(champion)
    return TournamentChampionSummary(
        champion_strategy_id=champion.strategy_id,
        valid_tournaments_reviewed=len(history.entries),
        champion_wins=champion.wins,
        champion_win_rate=champion.wins / len(history.entries),
        champion_best_score=max(champion.scores),
        champion_average_score=_average(champion.scores),
        champion_average_excess_return=_average(champion.excess_returns),
        champion_worst_max_drawdown=min(champion.max_drawdowns),
        most_recent_win_timestamp=recent_win_timestamp,
        fixtures_appeared=tuple(sorted(champion.fixtures)),
        skipped_artifact_count=len(history.skipped_artifacts),
    )


def _most_recent_win_timestamp(champion: _StrategyAggregate) -> str | None:
    if not champion.win_timestamps:
        return None

    latest_index = max(range(len(champion.win_timestamps)), key=lambda index: champion.win_timestamps[index])
    return champion.win_timestamp_texts[latest_index]


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _score(value: float) -> str:
    return f"{value:.4f}"
