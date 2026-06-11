from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WINNER_FIELDS = (
    "strategy_id",
    "score",
    "strategy_return",
    "spy_return",
    "excess_return",
    "max_drawdown",
)
RESULT_FIELDS = (
    "rank",
    "strategy_id",
    "score",
    "strategy_return",
    "spy_return",
    "excess_return",
    "max_drawdown",
    "trade_count",
    "rejected_trade_count",
)


@dataclass(frozen=True)
class TournamentStrategyResult:
    rank: int
    strategy_id: str
    score: float
    strategy_return: float
    spy_return: float
    excess_return: float
    max_drawdown: float
    trade_count: int
    rejected_trade_count: int


@dataclass(frozen=True)
class TournamentHistoryEntry:
    experiment_timestamp: str
    sort_timestamp: datetime
    fixture_name: str
    strategy_count: int
    winning_strategy_id: str
    winning_score: float
    winning_strategy_return: float
    winning_spy_return: float
    winning_excess_return: float
    winning_max_drawdown: float
    artifact_path: Path
    strategy_results: list[TournamentStrategyResult]


@dataclass(frozen=True)
class SkippedTournamentArtifact:
    artifact_path: Path
    reason: str


@dataclass(frozen=True)
class TournamentHistoryResult:
    entries: list[TournamentHistoryEntry]
    skipped_artifacts: list[SkippedTournamentArtifact]


def load_tournament_history(output_dir: Path | str) -> TournamentHistoryResult:
    output_path = Path(output_dir)
    if not output_path.exists():
        return TournamentHistoryResult(entries=[], skipped_artifacts=[])

    entries: list[TournamentHistoryEntry] = []
    skipped_artifacts: list[SkippedTournamentArtifact] = []
    for artifact_path in sorted(output_path.glob("*.json")):
        entry, skipped = _load_history_entry(artifact_path)
        if entry is not None:
            entries.append(entry)
        if skipped is not None:
            skipped_artifacts.append(skipped)

    return TournamentHistoryResult(
        entries=sorted(
            entries,
            key=lambda entry: (-entry.sort_timestamp.timestamp(), str(entry.artifact_path)),
        ),
        skipped_artifacts=skipped_artifacts,
    )


def format_tournament_history(result: TournamentHistoryResult, output_dir: Path | str) -> str:
    lines = ["Tournament History", f"Artifact directory: {Path(output_dir)}"]

    if not result.entries:
        lines.append("No valid tournament artifacts found.")
    else:
        headers = (
            "experiment timestamp",
            "fixture",
            "strategies",
            "winner",
            "score",
            "strategy return",
            "SPY return",
            "excess return",
            "max drawdown",
            "artifact path",
        )
        rows = [
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
                str(entry.artifact_path),
            )
            for entry in result.entries
        ]
        widths = [max(len(str(value)) for value in column) for column in zip(headers, *rows)]
        lines.append(_format_row(headers, widths))
        lines.append(_format_row(tuple("-" * width for width in widths), widths))
        lines.extend(_format_row(row, widths) for row in rows)

    if result.skipped_artifacts:
        lines.append("")
        lines.append("Skipped malformed artifacts:")
        for skipped in result.skipped_artifacts:
            lines.append(f"- {skipped.artifact_path}: {skipped.reason}")

    return "\n".join(lines)


def _load_history_entry(artifact_path: Path) -> tuple[TournamentHistoryEntry | None, SkippedTournamentArtifact | None]:
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return _entry_from_payload(payload, artifact_path), None
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, SkippedTournamentArtifact(artifact_path=artifact_path, reason=str(exc))


def _entry_from_payload(payload: Any, artifact_path: Path) -> TournamentHistoryEntry:
    if not isinstance(payload, dict):
        raise ValueError("artifact JSON must be an object")

    experiment_timestamp = _required_str(payload, "experiment_timestamp")
    sort_timestamp = _parse_timestamp(experiment_timestamp)
    fixture_name = _required_str(payload, "fixture_name")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("artifact results must be a non-empty list")

    strategy_results = _strategy_results_from_rows(results)
    winner = _winner_from_results(results)
    return TournamentHistoryEntry(
        experiment_timestamp=experiment_timestamp,
        sort_timestamp=sort_timestamp,
        fixture_name=fixture_name,
        strategy_count=len(results),
        winning_strategy_id=_required_str(winner, "strategy_id"),
        winning_score=_required_number(winner, "score"),
        winning_strategy_return=_required_number(winner, "strategy_return"),
        winning_spy_return=_required_number(winner, "spy_return"),
        winning_excess_return=_required_number(winner, "excess_return"),
        winning_max_drawdown=_required_number(winner, "max_drawdown"),
        artifact_path=artifact_path,
        strategy_results=strategy_results,
    )


def _strategy_results_from_rows(results: list[Any]) -> list[TournamentStrategyResult]:
    strategy_results: list[TournamentStrategyResult] = []
    for index, row in enumerate(results, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"result row {index} must be an object")
        missing_fields = [field for field in RESULT_FIELDS if field not in row]
        if missing_fields:
            raise ValueError(f"result row {index} missing fields: {', '.join(missing_fields)}")

        rank = row.get("rank")
        if not isinstance(rank, int):
            raise ValueError(f"result row {index} has invalid rank")

        strategy_results.append(
            TournamentStrategyResult(
                rank=rank,
                strategy_id=_required_str(row, "strategy_id"),
                score=_required_number(row, "score"),
                strategy_return=_required_number(row, "strategy_return"),
                spy_return=_required_number(row, "spy_return"),
                excess_return=_required_number(row, "excess_return"),
                max_drawdown=_required_number(row, "max_drawdown"),
                trade_count=_required_int(row, "trade_count"),
                rejected_trade_count=_required_int(row, "rejected_trade_count"),
            )
        )
    return strategy_results


def _winner_from_results(results: list[Any]) -> dict:
    ranked_rows = [row for row in results if isinstance(row, dict) and row.get("rank") == 1]
    if len(ranked_rows) != 1:
        raise ValueError("artifact must contain exactly one rank 1 winner")

    winner = ranked_rows[0]
    missing_fields = [field for field in WINNER_FIELDS if field not in winner]
    if missing_fields:
        raise ValueError(f"winner row missing fields: {', '.join(missing_fields)}")
    return winner


def _required_str(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid field: {field}")
    return value


def _required_number(payload: dict, field: str) -> float:
    value = payload.get(field)
    if not isinstance(value, (int, float)):
        raise ValueError(f"missing or invalid numeric field: {field}")
    return float(value)


def _required_int(payload: dict, field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int):
        raise ValueError(f"missing or invalid integer field: {field}")
    return value


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid experiment timestamp: {value}") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _score(value: float) -> str:
    return f"{value:.4f}"
