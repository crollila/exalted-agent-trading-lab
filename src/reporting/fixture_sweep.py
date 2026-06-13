from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.reporting.strategy_comparison import SCORE_EXPLANATION, SCORE_FORMULA
from src.reporting.strategy_status import strategy_status_for


@dataclass(frozen=True)
class FixtureWinner:
    fixture_name: str
    strategy_id: str
    score: float


@dataclass(frozen=True)
class StrategyRobustness:
    strategy_id: str
    fixture_count: int
    wins: int
    average_score: float
    average_excess_return: float
    worst_max_drawdown: float


@dataclass(frozen=True)
class FixtureSweepSummary:
    fixtures_included: tuple[str, ...]
    fixture_winners: tuple[FixtureWinner, ...]
    strategy_aggregates: tuple[StrategyRobustness, ...]
    overall_champion: StrategyRobustness
    score_formula: str
    score_explanation: str


@dataclass(frozen=True)
class FixtureSweepArtifactPaths:
    json_path: Path
    csv_path: Path
    markdown_path: Path


def summarize_fixture_sweep(ranked_results_by_fixture: dict[str, list[dict]]) -> FixtureSweepSummary:
    if not ranked_results_by_fixture:
        raise ValueError("fixture sweep requires at least one fixture")

    fixture_winners: list[FixtureWinner] = []
    aggregate_inputs: dict[str, dict[str, list[float] | int]] = {}
    for fixture_name, ranked_results in ranked_results_by_fixture.items():
        if not ranked_results:
            raise ValueError(f"fixture has no ranked results: {fixture_name}")

        winner = _winner_for_fixture(fixture_name, ranked_results)
        fixture_winners.append(winner)
        for row in ranked_results:
            aggregate = aggregate_inputs.setdefault(
                row["strategy_id"],
                {
                    "wins": 0,
                    "scores": [],
                    "excess_returns": [],
                    "max_drawdowns": [],
                },
            )
            if row["rank"] == 1:
                aggregate["wins"] += 1
            aggregate["scores"].append(row["score"])
            aggregate["excess_returns"].append(row["excess_return"])
            aggregate["max_drawdowns"].append(row["max_drawdown"])

    aggregates = tuple(
        sorted(
            (
                StrategyRobustness(
                    strategy_id=strategy_id,
                    fixture_count=len(aggregate["scores"]),
                    wins=aggregate["wins"],
                    average_score=_average(aggregate["scores"]),
                    average_excess_return=_average(aggregate["excess_returns"]),
                    worst_max_drawdown=min(aggregate["max_drawdowns"]),
                )
                for strategy_id, aggregate in aggregate_inputs.items()
            ),
            key=_champion_sort_key,
        )
    )
    return FixtureSweepSummary(
        fixtures_included=tuple(ranked_results_by_fixture),
        fixture_winners=tuple(fixture_winners),
        strategy_aggregates=aggregates,
        overall_champion=aggregates[0],
        score_formula=SCORE_FORMULA,
        score_explanation=SCORE_EXPLANATION,
    )


def format_fixture_sweep(
    summary: FixtureSweepSummary,
    status_by_strategy: dict[str, str] | None = None,
    status_filter_metadata: dict | None = None,
) -> str:
    active_filter_metadata = _normalize_filter_metadata(status_filter_metadata)
    lines = [
        "Fixture Sweep Tournament",
        f"Score formula: {summary.score_formula}",
        f"Score explanation: {summary.score_explanation}",
        "Safety disclaimer: local deterministic research only; not live trading; no broker/order behavior changed.",
        *_filter_text_lines(active_filter_metadata),
        "",
        "Per-fixture winners",
        _text_table(
            headers=("fixture", "winner", "winning score"),
            rows=[
                (winner.fixture_name, winner.strategy_id, _score(winner.score))
                for winner in summary.fixture_winners
            ],
        ),
        "",
        "Strategy robustness",
        _text_table(
            headers=(
                "strategy ID",
                "status",
                "fixtures",
                "wins",
                "average score",
                "average excess return",
                "worst max drawdown",
            ),
            rows=[
                (
                    aggregate.strategy_id,
                    strategy_status_for(aggregate.strategy_id, status_by_strategy),
                    str(aggregate.fixture_count),
                    str(aggregate.wins),
                    _score(aggregate.average_score),
                    _percent(aggregate.average_excess_return),
                    _percent(aggregate.worst_max_drawdown),
                )
                for aggregate in summary.strategy_aggregates
            ],
        ),
        "",
        f"Overall robust champion: {summary.overall_champion.strategy_id}",
        f"Champion fixture wins: {summary.overall_champion.wins}",
        f"Champion average score: {_score(summary.overall_champion.average_score)}",
        f"Champion average excess return: {_percent(summary.overall_champion.average_excess_return)}",
        f"Champion worst max drawdown: {_percent(summary.overall_champion.worst_max_drawdown)}",
    ]
    return "\n".join(lines)


def save_fixture_sweep_artifacts(
    summary: FixtureSweepSummary,
    output_dir: Path | str,
    generated_at: datetime | None = None,
    status_by_strategy: dict[str, str] | None = None,
    status_filter_metadata: dict | None = None,
) -> FixtureSweepArtifactPaths:
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp_text = timestamp.isoformat()
    filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    active_filter_metadata = _normalize_filter_metadata(status_filter_metadata)

    base_name = f"fixture_sweep_{filename_timestamp}"
    json_path = output_path / f"{base_name}.json"
    csv_path = output_path / f"{base_name}.csv"
    markdown_path = output_path / f"{base_name}.md"

    json_path.write_text(
        json.dumps(
            {
                "sweep_timestamp": timestamp_text,
                "fixtures_included": list(summary.fixtures_included),
                "score_formula": summary.score_formula,
                "score_explanation": summary.score_explanation,
                "status_filter": active_filter_metadata,
                "overall_champion": _aggregate_to_dict(summary.overall_champion),
                "strategy_statuses": {
                    aggregate.strategy_id: strategy_status_for(aggregate.strategy_id, status_by_strategy)
                    for aggregate in summary.strategy_aggregates
                },
                "per_fixture_winners": [
                    {
                        "fixture_name": winner.fixture_name,
                        "strategy_id": winner.strategy_id,
                        "score": winner.score,
                    }
                    for winner in summary.fixture_winners
                ],
                "strategy_aggregates": [
                    _aggregate_to_dict(aggregate)
                    for aggregate in summary.strategy_aggregates
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        fieldnames = (
            "row_type",
            "sweep_timestamp",
            "overall_champion",
            "fixture_name",
            "strategy_id",
            "status",
            "score",
            "fixture_count",
            "wins",
            "average_score",
            "average_excess_return",
            "worst_max_drawdown",
            "status_filter_applied",
            "status_filter_exclude_retired",
            "status_filter_included_statuses",
            "status_filter_excluded_strategies",
        )
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for winner in summary.fixture_winners:
            writer.writerow(
                {
                    "row_type": "fixture_winner",
                    "sweep_timestamp": timestamp_text,
                    "overall_champion": summary.overall_champion.strategy_id,
                    "fixture_name": winner.fixture_name,
                    "strategy_id": winner.strategy_id,
                    "status": strategy_status_for(winner.strategy_id, status_by_strategy),
                    "score": winner.score,
                    **_filter_csv_values(active_filter_metadata),
                }
            )
        for aggregate in summary.strategy_aggregates:
            writer.writerow(
                {
                    "row_type": "strategy_aggregate",
                    "sweep_timestamp": timestamp_text,
                    "overall_champion": summary.overall_champion.strategy_id,
                    "status": strategy_status_for(aggregate.strategy_id, status_by_strategy),
                    **_aggregate_to_dict(aggregate),
                    **_filter_csv_values(active_filter_metadata),
                }
            )

    filter_markdown = _filter_markdown_lines(active_filter_metadata)
    markdown_path.write_text(
        "\n".join(
            [
                "# Fixture Sweep Tournament",
                "",
                f"- Sweep timestamp: {timestamp_text}",
                f"- Fixtures included: {', '.join(summary.fixtures_included)}",
                f"- Overall robust champion: `{summary.overall_champion.strategy_id}`",
                f"- Score formula: `{summary.score_formula}`",
                f"- Score explanation: {summary.score_explanation}",
                *filter_markdown,
                "",
                "## Safety Disclaimer",
                "",
                "- This is local deterministic research only.",
                "- This is not live trading.",
                "- No broker/order behavior changed.",
                "",
                "## Per-Fixture Winners",
                "",
                _markdown_table(
                    headers=("Fixture", "Winner", "Winning Score"),
                    rows=[
                        (winner.fixture_name, winner.strategy_id, _score(winner.score))
                        for winner in summary.fixture_winners
                    ],
                ),
                "",
                "## Strategy Robustness",
                "",
                _markdown_table(
                    headers=(
                        "Strategy ID",
                        "Status",
                        "Fixtures",
                        "Wins",
                        "Average Score",
                        "Average Excess Return",
                        "Worst Max Drawdown",
                    ),
                    rows=[
                        (
                            aggregate.strategy_id,
                            strategy_status_for(aggregate.strategy_id, status_by_strategy),
                            str(aggregate.fixture_count),
                            str(aggregate.wins),
                            _score(aggregate.average_score),
                            _percent(aggregate.average_excess_return),
                            _percent(aggregate.worst_max_drawdown),
                        )
                        for aggregate in summary.strategy_aggregates
                    ],
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    return FixtureSweepArtifactPaths(json_path=json_path, csv_path=csv_path, markdown_path=markdown_path)


def _winner_for_fixture(fixture_name: str, ranked_results: list[dict]) -> FixtureWinner:
    winners = [row for row in ranked_results if row["rank"] == 1]
    if len(winners) != 1:
        raise ValueError(f"fixture must have exactly one rank 1 winner: {fixture_name}")
    winner = winners[0]
    return FixtureWinner(
        fixture_name=fixture_name,
        strategy_id=winner["strategy_id"],
        score=winner["score"],
    )


def _champion_sort_key(aggregate: StrategyRobustness) -> tuple:
    return (
        -aggregate.wins,
        -aggregate.average_score,
        -aggregate.average_excess_return,
        abs(aggregate.worst_max_drawdown),
        aggregate.strategy_id,
    )


def _aggregate_to_dict(aggregate: StrategyRobustness) -> dict:
    return {
        "strategy_id": aggregate.strategy_id,
        "fixture_count": aggregate.fixture_count,
        "wins": aggregate.wins,
        "average_score": aggregate.average_score,
        "average_excess_return": aggregate.average_excess_return,
        "worst_max_drawdown": aggregate.worst_max_drawdown,
    }


def _normalize_filter_metadata(status_filter_metadata: dict | None) -> dict:
    if status_filter_metadata is None:
        return {
            "applied": False,
            "exclude_retired": False,
            "included_statuses": [],
            "excluded_strategies": [],
        }
    return {
        "applied": bool(status_filter_metadata.get("applied")),
        "exclude_retired": bool(status_filter_metadata.get("exclude_retired")),
        "included_statuses": list(status_filter_metadata.get("included_statuses") or []),
        "excluded_strategies": list(status_filter_metadata.get("excluded_strategies") or []),
    }


def _excluded_strategy_text(status_filter_metadata: dict) -> str:
    return ", ".join(
        f"{row['strategy_id']} ({row['status']})"
        for row in status_filter_metadata["excluded_strategies"]
    )


def _filter_text_lines(status_filter_metadata: dict) -> list[str]:
    if not status_filter_metadata["applied"]:
        return []

    lines = ["Status filter applied."]
    if status_filter_metadata["exclude_retired"]:
        lines.append("Retired strategies were excluded.")
    if status_filter_metadata["included_statuses"]:
        lines.append(f"Included statuses: {', '.join(status_filter_metadata['included_statuses'])}")
    if status_filter_metadata["excluded_strategies"]:
        lines.append(f"Excluded strategies: {_excluded_strategy_text(status_filter_metadata)}")
    else:
        lines.append("Excluded strategies: none.")
    return lines


def _filter_markdown_lines(status_filter_metadata: dict) -> list[str]:
    lines = [
        f"- Status filter applied: {'yes' if status_filter_metadata['applied'] else 'no'}",
        f"- Exclude retired: {'yes' if status_filter_metadata['exclude_retired'] else 'no'}",
    ]
    if status_filter_metadata["included_statuses"]:
        lines.append(f"- Included statuses: {', '.join(status_filter_metadata['included_statuses'])}")
    if status_filter_metadata["excluded_strategies"]:
        lines.append(f"- Excluded strategies: {_excluded_strategy_text(status_filter_metadata)}")
    return lines


def _filter_csv_values(status_filter_metadata: dict) -> dict:
    return {
        "status_filter_applied": status_filter_metadata["applied"],
        "status_filter_exclude_retired": status_filter_metadata["exclude_retired"],
        "status_filter_included_statuses": ", ".join(status_filter_metadata["included_statuses"]),
        "status_filter_excluded_strategies": _excluded_strategy_text(status_filter_metadata),
    }


def _text_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(str(value)) for value in column) for column in zip(headers, *rows)]
    lines = [
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))


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
