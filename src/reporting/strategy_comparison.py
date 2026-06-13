from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_COMPARISON_FIELDS = (
    "rank",
    "strategy_id",
    "run_id",
    "score",
    "starting_equity",
    "current_equity",
    "strategy_return",
    "spy_return",
    "excess_return",
    "max_drawdown",
    "trade_count",
    "rejected_trade_count",
    "score_formula",
    "score_explanation",
)
ARTIFACT_COLUMNS = ("experiment_timestamp", "fixture_name", *REQUIRED_COMPARISON_FIELDS)
FILTER_ARTIFACT_COLUMNS = (
    *ARTIFACT_COLUMNS,
    "status_filter_applied",
    "status_filter_exclude_retired",
    "status_filter_included_statuses",
    "status_filter_excluded_strategies",
)
REJECTED_TRADE_SCORE_PENALTY = 0.01
SCORE_FORMULA = "score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)"
SCORE_EXPLANATION = (
    "Higher is better. Excess return helps the score, while larger drawdowns and rejected trades lower it. "
    "Trade count is shown for context but does not add points."
)


@dataclass(frozen=True)
class ComparisonArtifactPaths:
    json_path: Path
    csv_path: Path
    markdown_path: Path


def format_strategy_comparison(reports: list[dict]) -> str:
    ranked_reports = rank_strategy_reports(reports)
    headers = (
        "rank",
        "strategy ID",
        "run_id",
        "score",
        "starting equity",
        "current equity",
        "strategy return",
        "SPY return",
        "excess return",
        "max drawdown",
        "trade count",
        "rejected trade count",
    )
    rows = [
        (
            str(report["rank"]),
            report["strategy_id"],
            report["run_id"],
            _score(report["score"]),
            _money(report["starting_equity"]),
            _money(report["current_equity"]),
            _percent(report["strategy_return"]),
            _percent(report["spy_return"]),
            _percent(report["excess_return"]),
            _percent(report["max_drawdown"]),
            str(report["trade_count"]),
            str(report["rejected_trade_count"]),
        )
        for report in ranked_reports
    ]

    widths = [
        max(len(str(value)) for value in column)
        for column in zip(headers, *rows)
    ]
    lines = [
        "Strategy Comparison",
        f"Score formula: {SCORE_FORMULA}",
        f"Score explanation: {SCORE_EXPLANATION}",
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def save_strategy_comparison_artifacts(
    reports: list[dict],
    fixture_name: str,
    output_dir: Path | str,
    experiment_timestamp: datetime | None = None,
    status_filter_metadata: dict | None = None,
) -> ComparisonArtifactPaths:
    timestamp = experiment_timestamp or datetime.now(timezone.utc)
    timestamp_text = timestamp.astimezone(timezone.utc).isoformat()
    filename_timestamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_fixture_name = _safe_filename_part(fixture_name)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    ranked_reports = rank_strategy_reports(reports)
    active_filter_metadata = _normalize_filter_metadata(status_filter_metadata)
    include_filter_metadata = status_filter_metadata is not None

    rows = []
    for report in ranked_reports:
        row = {
            "experiment_timestamp": timestamp_text,
            "fixture_name": fixture_name,
            **{field: report[field] for field in REQUIRED_COMPARISON_FIELDS},
        }
        if include_filter_metadata:
            row.update(
                {
                    "status_filter_applied": active_filter_metadata["applied"],
                    "status_filter_exclude_retired": active_filter_metadata["exclude_retired"],
                    "status_filter_included_statuses": ", ".join(active_filter_metadata["included_statuses"]),
                    "status_filter_excluded_strategies": _excluded_strategy_text(active_filter_metadata),
                }
            )
        rows.append(row)

    base_name = f"strategy_comparison_{safe_fixture_name}_{filename_timestamp}"
    json_path = output_path / f"{base_name}.json"
    csv_path = output_path / f"{base_name}.csv"
    markdown_path = output_path / f"{base_name}.md"

    payload = {
        "experiment_timestamp": timestamp_text,
        "fixture_name": fixture_name,
        "score_formula": SCORE_FORMULA,
        "score_explanation": SCORE_EXPLANATION,
        "results": rows,
    }
    if include_filter_metadata:
        payload["status_filter"] = active_filter_metadata

    json_path.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=FILTER_ARTIFACT_COLUMNS if include_filter_metadata else ARTIFACT_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(rows)

    filter_markdown = _filter_markdown_lines(active_filter_metadata) if include_filter_metadata else []
    markdown_path.write_text(
        "\n".join(
            [
                "# Strategy Comparison Experiment",
                "",
                f"- Experiment timestamp: {timestamp_text}",
                f"- Fixture: {fixture_name}",
                f"- Score formula: `{SCORE_FORMULA}`",
                f"- Score explanation: {SCORE_EXPLANATION}",
                *filter_markdown,
                "",
                "```text",
                format_strategy_comparison(ranked_reports),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return ComparisonArtifactPaths(
        json_path=json_path,
        csv_path=csv_path,
        markdown_path=markdown_path,
    )


def rank_strategy_reports(reports: list[dict]) -> list[dict]:
    scored_reports = [
        {
            **report,
            "score": score_strategy_report(report),
            "score_formula": SCORE_FORMULA,
            "score_explanation": SCORE_EXPLANATION,
        }
        for report in reports
    ]
    ranked_reports = sorted(
        scored_reports,
        key=lambda report: (
            -report["score"],
            -report["excess_return"],
            abs(report["max_drawdown"]),
            report["rejected_trade_count"],
            report["strategy_id"],
        ),
    )
    return [
        {
            **report,
            "rank": index,
        }
        for index, report in enumerate(ranked_reports, start=1)
    ]


def score_strategy_report(report: dict) -> float:
    max_drawdown_penalty = abs(report["max_drawdown"])
    rejected_trade_penalty = report["rejected_trade_count"] * REJECTED_TRADE_SCORE_PENALTY
    return round(report["excess_return"] - max_drawdown_penalty - rejected_trade_penalty, 10)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))


def _safe_filename_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


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
    excluded = status_filter_metadata["excluded_strategies"]
    return ", ".join(
        f"{row['strategy_id']} ({row['status']})"
        for row in excluded
    )


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


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _score(value: float) -> str:
    return f"{value:.4f}"
