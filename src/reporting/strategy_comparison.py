from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_COMPARISON_FIELDS = (
    "strategy_id",
    "run_id",
    "starting_equity",
    "current_equity",
    "strategy_return",
    "spy_return",
    "excess_return",
    "max_drawdown",
    "trade_count",
    "rejected_trade_count",
)
ARTIFACT_COLUMNS = ("experiment_timestamp", "fixture_name", *REQUIRED_COMPARISON_FIELDS)


@dataclass(frozen=True)
class ComparisonArtifactPaths:
    json_path: Path
    csv_path: Path
    markdown_path: Path


def format_strategy_comparison(reports: list[dict]) -> str:
    headers = (
        "strategy_id",
        "run_id",
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
            report["strategy_id"],
            report["run_id"],
            _money(report["starting_equity"]),
            _money(report["current_equity"]),
            _percent(report["strategy_return"]),
            _percent(report["spy_return"]),
            _percent(report["excess_return"]),
            _percent(report["max_drawdown"]),
            str(report["trade_count"]),
            str(report["rejected_trade_count"]),
        )
        for report in reports
    ]

    widths = [
        max(len(str(value)) for value in column)
        for column in zip(headers, *rows)
    ]
    lines = [
        "Strategy Comparison",
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
) -> ComparisonArtifactPaths:
    timestamp = experiment_timestamp or datetime.now(timezone.utc)
    timestamp_text = timestamp.astimezone(timezone.utc).isoformat()
    filename_timestamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_fixture_name = _safe_filename_part(fixture_name)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "experiment_timestamp": timestamp_text,
            "fixture_name": fixture_name,
            **{field: report[field] for field in REQUIRED_COMPARISON_FIELDS},
        }
        for report in reports
    ]

    base_name = f"strategy_comparison_{safe_fixture_name}_{filename_timestamp}"
    json_path = output_path / f"{base_name}.json"
    csv_path = output_path / f"{base_name}.csv"
    markdown_path = output_path / f"{base_name}.md"

    json_path.write_text(
        json.dumps(
            {
                "experiment_timestamp": timestamp_text,
                "fixture_name": fixture_name,
                "results": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=ARTIFACT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    markdown_path.write_text(
        "\n".join(
            [
                "# Strategy Comparison Experiment",
                "",
                f"- Experiment timestamp: {timestamp_text}",
                f"- Fixture: {fixture_name}",
                "",
                "```text",
                format_strategy_comparison(reports),
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


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))


def _safe_filename_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"
