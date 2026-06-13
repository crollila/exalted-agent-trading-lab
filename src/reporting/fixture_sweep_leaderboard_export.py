from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.reporting.fixture_sweep import FixtureWinner, StrategyRobustness


@dataclass(frozen=True)
class FixtureSweepLeaderboardExportResult:
    saved: bool
    report_path: Path
    message: str


@dataclass(frozen=True)
class FixtureSweepArtifactEntry:
    sweep_timestamp: str
    sort_timestamp: datetime
    fixtures_included: tuple[str, ...]
    fixture_winners: tuple[FixtureWinner, ...]
    strategy_aggregates: tuple[StrategyRobustness, ...]
    overall_champion: StrategyRobustness
    score_formula: str
    score_explanation: str
    artifact_path: Path


@dataclass(frozen=True)
class SkippedFixtureSweepArtifact:
    artifact_path: Path
    reason: str


@dataclass(frozen=True)
class FixtureSweepArtifactLoadResult:
    entries: list[FixtureSweepArtifactEntry]
    skipped_artifacts: list[SkippedFixtureSweepArtifact]
    artifact_directory_exists: bool


@dataclass(frozen=True)
class FixtureSweepLeaderboardSummary:
    champion: StrategyRobustness
    valid_sweeps_reviewed: int
    champion_win_rate: float
    fixtures_included: tuple[str, ...]
    score_formula: str
    score_explanation: str
    most_recent_sweep_artifact_path: Path
    skipped_artifact_count: int


def export_fixture_sweep_leaderboard(
    output_dir: Path | str = Path("data/experiments"),
    report_path: Path | str = Path("data/reports/fixture_sweep_leaderboard.md"),
    generated_at: datetime | None = None,
) -> FixtureSweepLeaderboardExportResult:
    active_report_path = Path(report_path)
    loaded = load_fixture_sweep_artifacts(output_dir)
    if not loaded.entries:
        if loaded.artifact_directory_exists:
            message = f"No valid fixture sweep artifacts found in {Path(output_dir)}. No report written."
        else:
            message = f"No fixture sweep artifacts found in {Path(output_dir)}. No report written."
        return FixtureSweepLeaderboardExportResult(saved=False, report_path=active_report_path, message=message)

    markdown = format_fixture_sweep_leaderboard(
        loaded=loaded,
        output_dir=output_dir,
        generated_at=generated_at,
    )
    active_report_path.parent.mkdir(parents=True, exist_ok=True)
    active_report_path.write_text(markdown, encoding="utf-8")
    return FixtureSweepLeaderboardExportResult(
        saved=True,
        report_path=active_report_path,
        message=f"Saved fixture sweep leaderboard report: {active_report_path}",
    )


def load_fixture_sweep_artifacts(output_dir: Path | str) -> FixtureSweepArtifactLoadResult:
    output_path = Path(output_dir)
    if not output_path.exists():
        return FixtureSweepArtifactLoadResult(
            entries=[],
            skipped_artifacts=[],
            artifact_directory_exists=False,
        )

    entries: list[FixtureSweepArtifactEntry] = []
    skipped_artifacts: list[SkippedFixtureSweepArtifact] = []
    for artifact_path in sorted(output_path.glob("fixture_sweep_*.json")):
        entry, skipped = _load_fixture_sweep_entry(artifact_path)
        if entry is not None:
            entries.append(entry)
        if skipped is not None:
            skipped_artifacts.append(skipped)

    return FixtureSweepArtifactLoadResult(
        entries=sorted(
            entries,
            key=lambda entry: (-entry.sort_timestamp.timestamp(), str(entry.artifact_path)),
        ),
        skipped_artifacts=skipped_artifacts,
        artifact_directory_exists=True,
    )


def format_fixture_sweep_leaderboard(
    loaded: FixtureSweepArtifactLoadResult,
    output_dir: Path | str,
    generated_at: datetime | None = None,
) -> str:
    if not loaded.entries:
        raise ValueError("Cannot format fixture sweep leaderboard without valid sweep artifacts.")

    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    summary = _leaderboard_summary(loaded)
    aggregate_rows = _strategy_robustness_rows(loaded.entries)
    fixture_winner_rows = [
        (winner.fixture_name, winner.strategy_id, _score(winner.score))
        for entry in loaded.entries
        for winner in entry.fixture_winners
    ]

    lines = [
        "# Fixture Sweep Leaderboard",
        "",
        f"Generated timestamp: {timestamp}",
        f"Source artifact directory: `{Path(output_dir)}`",
        f"Most recent sweep artifact path: `{summary.most_recent_sweep_artifact_path}`",
        "",
        "## Current Robust Champion",
        "",
        f"- Champion strategy ID: `{summary.champion.strategy_id}`",
        f"- Valid sweeps reviewed: {summary.valid_sweeps_reviewed}",
        f"- Fixture appearances: {summary.champion.fixture_count}",
        f"- Fixture wins: {summary.champion.wins}",
        f"- Win rate: {_percent(summary.champion_win_rate)}",
        f"- Average score: {_score(summary.champion.average_score)}",
        f"- Average excess return: {_percent(summary.champion.average_excess_return)}",
        f"- Worst max drawdown: {_percent(summary.champion.worst_max_drawdown)}",
        f"- Skipped/malformed artifact count: {summary.skipped_artifact_count}",
        "",
        "## Fixtures Included",
        "",
        ", ".join(summary.fixtures_included),
        "",
        "## Score Formula",
        "",
        f"`{summary.score_formula}`",
        "",
        summary.score_explanation,
        "",
        "## Safety Disclaimer",
        "",
        "- This is local deterministic research only.",
        "- This is not live trading.",
        "- No options.",
        "- No margin.",
        "- No shorting.",
        "- Hermes runtime is disabled.",
        "",
        "## Per-Fixture Winners",
        "",
        _markdown_table(
            headers=("Fixture", "Winning Strategy", "Winning Score"),
            rows=fixture_winner_rows,
        ),
        "",
        "## Strategy Robustness Aggregates",
        "",
        _markdown_table(
            headers=(
                "Strategy ID",
                "Fixture Appearances",
                "Fixture Wins",
                "Win Rate",
                "Average Score",
                "Average Excess Return",
                "Worst Max Drawdown",
            ),
            rows=[
                (
                    row.strategy_id,
                    str(row.fixture_count),
                    str(row.wins),
                    _percent(row.wins / row.fixture_count),
                    _score(row.average_score),
                    _percent(row.average_excess_return),
                    _percent(row.worst_max_drawdown),
                )
                for row in aggregate_rows
            ],
        ),
        "",
        "## Caveats",
        "",
        "- Deterministic fixtures are not proof of a real trading edge.",
        "- Cross-fixture robustness is still simulated.",
        "- Results should guide research, not trading decisions.",
        "",
    ]

    if loaded.skipped_artifacts:
        lines.extend(
            [
                "## Skipped Artifacts",
                "",
                f"Skipped/malformed artifact count: {len(loaded.skipped_artifacts)}",
                "",
            ]
        )
        lines.extend(
            f"- `{skipped.artifact_path}`: {skipped.reason}"
            for skipped in loaded.skipped_artifacts
        )
        lines.append("")

    return "\n".join(lines)


def _load_fixture_sweep_entry(
    artifact_path: Path,
) -> tuple[FixtureSweepArtifactEntry | None, SkippedFixtureSweepArtifact | None]:
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return _entry_from_payload(payload, artifact_path), None
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, SkippedFixtureSweepArtifact(artifact_path=artifact_path, reason=str(exc))


def _entry_from_payload(payload: Any, artifact_path: Path) -> FixtureSweepArtifactEntry:
    if not isinstance(payload, dict):
        raise ValueError("fixture sweep artifact JSON must be an object")

    sweep_timestamp = _required_str(payload, "sweep_timestamp")
    fixtures_included = _required_str_tuple(payload, "fixtures_included")
    fixture_winners = _fixture_winners(payload.get("per_fixture_winners"))
    strategy_aggregates = _strategy_aggregates(payload.get("strategy_aggregates"))
    overall_champion = _strategy_robustness(payload.get("overall_champion"), "overall_champion")
    return FixtureSweepArtifactEntry(
        sweep_timestamp=sweep_timestamp,
        sort_timestamp=_parse_timestamp(sweep_timestamp),
        fixtures_included=fixtures_included,
        fixture_winners=fixture_winners,
        strategy_aggregates=strategy_aggregates,
        overall_champion=overall_champion,
        score_formula=_required_str(payload, "score_formula"),
        score_explanation=_required_str(payload, "score_explanation"),
        artifact_path=artifact_path,
    )


def _fixture_winners(value: Any) -> tuple[FixtureWinner, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("per_fixture_winners must be a non-empty list")

    winners: list[FixtureWinner] = []
    for index, row in enumerate(value, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"per_fixture_winners row {index} must be an object")
        winners.append(
            FixtureWinner(
                fixture_name=_required_str(row, "fixture_name"),
                strategy_id=_required_str(row, "strategy_id"),
                score=_required_number(row, "score"),
            )
        )
    return tuple(winners)


def _strategy_aggregates(value: Any) -> tuple[StrategyRobustness, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("strategy_aggregates must be a non-empty list")

    return tuple(
        _strategy_robustness(row, f"strategy_aggregates row {index}")
        for index, row in enumerate(value, start=1)
    )


def _strategy_robustness(value: Any, label: str) -> StrategyRobustness:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")

    return StrategyRobustness(
        strategy_id=_required_str(value, "strategy_id"),
        fixture_count=_required_int(value, "fixture_count"),
        wins=_required_int(value, "wins"),
        average_score=_required_number(value, "average_score"),
        average_excess_return=_required_number(value, "average_excess_return"),
        worst_max_drawdown=_required_number(value, "worst_max_drawdown"),
    )


def _leaderboard_summary(loaded: FixtureSweepArtifactLoadResult) -> FixtureSweepLeaderboardSummary:
    rows = _strategy_robustness_rows(loaded.entries)
    champion = rows[0]
    most_recent_entry = loaded.entries[0]
    return FixtureSweepLeaderboardSummary(
        champion=champion,
        valid_sweeps_reviewed=len(loaded.entries),
        champion_win_rate=champion.wins / champion.fixture_count,
        fixtures_included=_fixture_union(loaded.entries),
        score_formula=most_recent_entry.score_formula,
        score_explanation=most_recent_entry.score_explanation,
        most_recent_sweep_artifact_path=most_recent_entry.artifact_path,
        skipped_artifact_count=len(loaded.skipped_artifacts),
    )


def _strategy_robustness_rows(entries: list[FixtureSweepArtifactEntry]) -> list[StrategyRobustness]:
    aggregates: dict[str, dict[str, float | int]] = {}
    for entry in entries:
        for row in entry.strategy_aggregates:
            aggregate = aggregates.setdefault(
                row.strategy_id,
                {
                    "fixture_count": 0,
                    "wins": 0,
                    "score_total": 0.0,
                    "excess_return_total": 0.0,
                    "worst_max_drawdown": row.worst_max_drawdown,
                },
            )
            aggregate["fixture_count"] += row.fixture_count
            aggregate["wins"] += row.wins
            aggregate["score_total"] += row.average_score * row.fixture_count
            aggregate["excess_return_total"] += row.average_excess_return * row.fixture_count
            aggregate["worst_max_drawdown"] = min(
                aggregate["worst_max_drawdown"],
                row.worst_max_drawdown,
            )

    rows = [
        StrategyRobustness(
            strategy_id=strategy_id,
            fixture_count=aggregate["fixture_count"],
            wins=aggregate["wins"],
            average_score=aggregate["score_total"] / aggregate["fixture_count"],
            average_excess_return=aggregate["excess_return_total"] / aggregate["fixture_count"],
            worst_max_drawdown=aggregate["worst_max_drawdown"],
        )
        for strategy_id, aggregate in aggregates.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -row.wins,
            -row.average_score,
            -row.average_excess_return,
            abs(row.worst_max_drawdown),
            row.strategy_id,
        ),
    )


def _fixture_union(entries: list[FixtureSweepArtifactEntry]) -> tuple[str, ...]:
    fixtures: list[str] = []
    for entry in entries:
        for fixture_name in entry.fixtures_included:
            if fixture_name not in fixtures:
                fixtures.append(fixture_name)
    return tuple(fixtures)


def _required_str(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid field: {field}")
    return value


def _required_str_tuple(payload: dict, field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"missing or invalid list field: {field}")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"missing or invalid list field: {field}")
    return tuple(value)


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
        raise ValueError(f"invalid sweep timestamp: {value}") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _score(value: float) -> str:
    return f"{value:.4f}"
