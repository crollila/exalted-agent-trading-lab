from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.reporting.fixture_sweep_leaderboard_export import (
    FixtureSweepArtifactEntry,
    load_fixture_sweep_artifacts,
)


@dataclass(frozen=True)
class SweepAnalysisNoteResult:
    saved: bool
    note_path: Path | None
    message: str


def create_sweep_analysis_note(
    output_dir: Path | str = Path("data/experiments"),
    notes_dir: Path | str = Path("data/notes"),
    force: bool = False,
    generated_at: datetime | None = None,
) -> SweepAnalysisNoteResult:
    loaded = load_fixture_sweep_artifacts(output_dir)
    notes_path = Path(notes_dir)
    if not loaded.entries:
        message = f"No valid fixture sweep artifacts found in {Path(output_dir)}. No sweep analysis note written."
        if loaded.skipped_artifacts:
            message += f" Skipped malformed artifact count: {len(loaded.skipped_artifacts)}."
        return SweepAnalysisNoteResult(saved=False, note_path=None, message=message)

    latest_entry = loaded.entries[0]
    note_path = notes_path / _sweep_analysis_note_filename(latest_entry)
    if note_path.exists() and not force:
        return SweepAnalysisNoteResult(
            saved=False,
            note_path=note_path,
            message=f"Sweep analysis note already exists: {note_path}. Use --force to overwrite.",
        )

    markdown = format_sweep_analysis_note(
        entry=latest_entry,
        generated_at=generated_at,
        skipped_artifact_count=len(loaded.skipped_artifacts),
    )
    notes_path.mkdir(parents=True, exist_ok=True)
    note_path.write_text(markdown, encoding="utf-8")

    message = f"Saved fixture sweep analysis note: {note_path}"
    if loaded.skipped_artifacts:
        message += f"\nSkipped malformed artifact count: {len(loaded.skipped_artifacts)}"
    return SweepAnalysisNoteResult(saved=True, note_path=note_path, message=message)


def format_sweep_analysis_note(
    entry: FixtureSweepArtifactEntry,
    generated_at: datetime | None = None,
    skipped_artifact_count: int = 0,
) -> str:
    generated_timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    champion = entry.overall_champion

    lines = [
        "# Fixture Sweep Analysis Note",
        "",
        f"Generated timestamp: {generated_timestamp}",
        f"Source sweep artifact path: `{entry.artifact_path}`",
        f"Sweep timestamp: {entry.sweep_timestamp}",
        f"Fixtures included: {', '.join(entry.fixtures_included)}",
        f"Overall robust champion: {champion.strategy_id}",
        f"Champion wins: {champion.wins}",
        f"Champion average score: {_score(champion.average_score)}",
        f"Champion average excess return: {_percent(champion.average_excess_return)}",
        f"Champion worst max drawdown: {_percent(champion.worst_max_drawdown)}",
        f"Skipped/malformed artifact count: {skipped_artifact_count}",
        "",
        "## Per-Fixture Winners",
        "",
        _markdown_table(
            headers=("Fixture", "Winning Strategy", "Winning Score"),
            rows=[
                (winner.fixture_name, winner.strategy_id, _score(winner.score))
                for winner in entry.fixture_winners
            ],
        ),
        "",
        "## Strategy Robustness",
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
                for row in entry.strategy_aggregates
            ],
        ),
        "",
        "## Score Formula",
        "",
        f"`{entry.score_formula}`",
        "",
        entry.score_explanation,
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
        "## Human Review Prompts",
        "",
        "### Which strategy was most robust?",
        "",
        "### Did cash winning indicate strategy weakness?",
        "",
        "### Which strategy failed in hostile regimes?",
        "",
        "### Which fixture exposed the biggest weakness?",
        "",
        "### Is the champion robust enough to promote, or should it be retested?",
        "",
        "### What scenario should be added next?",
        "",
        "## Decision",
        "",
        "- [ ] promote",
        "- [ ] modify",
        "- [ ] retest",
        "- [ ] retire",
        "- [ ] no decision yet",
        "",
    ]
    return "\n".join(lines)


def _sweep_analysis_note_filename(entry: FixtureSweepArtifactEntry) -> str:
    timestamp = entry.sort_timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"sweep_analysis_note_{timestamp}.md"


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
