from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.reporting.strategy_comparison import SCORE_FORMULA
from src.reporting.tournament_history import TournamentHistoryEntry, load_tournament_history


@dataclass(frozen=True)
class AnalysisNoteResult:
    saved: bool
    note_path: Path | None
    message: str


def create_strategy_analysis_note(
    output_dir: Path | str = Path("data/experiments"),
    notes_dir: Path | str = Path("data/notes"),
    force: bool = False,
    generated_at: datetime | None = None,
) -> AnalysisNoteResult:
    history = load_tournament_history(output_dir)
    notes_path = Path(notes_dir)
    if not history.entries:
        message = f"No valid tournament artifacts found in {Path(output_dir)}. No analysis note written."
        if history.skipped_artifacts:
            message += f" Skipped malformed artifact count: {len(history.skipped_artifacts)}."
        return AnalysisNoteResult(saved=False, note_path=None, message=message)

    latest_entry = history.entries[0]
    note_path = notes_path / _analysis_note_filename(latest_entry)
    if note_path.exists() and not force:
        return AnalysisNoteResult(
            saved=False,
            note_path=note_path,
            message=f"Analysis note already exists: {note_path}. Use --force to overwrite.",
        )

    markdown = format_strategy_analysis_note(
        entry=latest_entry,
        generated_at=generated_at,
        skipped_artifact_count=len(history.skipped_artifacts),
    )
    notes_path.mkdir(parents=True, exist_ok=True)
    note_path.write_text(markdown, encoding="utf-8")

    message = f"Saved strategy analysis note: {note_path}"
    if history.skipped_artifacts:
        message += f"\nSkipped malformed artifact count: {len(history.skipped_artifacts)}"
    return AnalysisNoteResult(saved=True, note_path=note_path, message=message)


def format_strategy_analysis_note(
    entry: TournamentHistoryEntry,
    generated_at: datetime | None = None,
    skipped_artifact_count: int = 0,
) -> str:
    generated_timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    winner = next(result for result in entry.strategy_results if result.rank == 1)

    lines = [
        "# Strategy Tournament Analysis Note",
        "",
        f"Generated timestamp: {generated_timestamp}",
        f"Source artifact path: `{entry.artifact_path}`",
        f"Tournament timestamp: {entry.experiment_timestamp}",
        f"Fixture name: {entry.fixture_name}",
        f"Winner strategy ID: {winner.strategy_id}",
        f"Winner score: {_score(winner.score)}",
        f"Skipped/malformed artifact count: {skipped_artifact_count}",
        "",
        "## Strategy Ranking",
        "",
        _markdown_table(
            headers=(
                "Rank",
                "Strategy ID",
                "Score",
                "Strategy Return",
                "SPY Return",
                "Excess Return",
                "Max Drawdown",
                "Trade Count",
                "Rejected Trade Count",
            ),
            rows=[
                (
                    str(result.rank),
                    result.strategy_id,
                    _score(result.score),
                    _percent(result.strategy_return),
                    _percent(result.spy_return),
                    _percent(result.excess_return),
                    _percent(result.max_drawdown),
                    str(result.trade_count),
                    str(result.rejected_trade_count),
                )
                for result in sorted(entry.strategy_results, key=lambda result: result.rank)
            ],
        ),
        "",
        "## Score Formula",
        "",
        f"`{SCORE_FORMULA}`",
        "",
        "## Safety Disclaimer",
        "",
        "- This is local/dry-run research.",
        "- This is not live trading.",
        "- No options.",
        "- No margin.",
        "- No shorting.",
        "- Hermes runtime is disabled.",
        "",
        "## Human Review Prompts",
        "",
        "### What won?",
        "",
        "### Why did it win?",
        "",
        "### Was the edge real or fixture-specific?",
        "",
        "### What risks showed up?",
        "",
        "### What should be tested next?",
        "",
        "### Should this strategy be promoted, modified, or retired?",
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


def _analysis_note_filename(entry: TournamentHistoryEntry) -> str:
    timestamp = _filename_timestamp(entry.sort_timestamp)
    fixture = _safe_filename_part(entry.fixture_name)
    return f"analysis_note_{fixture}_{timestamp}.md"


def _filename_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_filename_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)


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
