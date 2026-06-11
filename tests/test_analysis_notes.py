import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from src.reporting.analysis_notes import create_strategy_analysis_note
from src.reporting.strategy_comparison import SCORE_FORMULA


def test_analysis_note_generation_from_one_valid_artifact(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    artifact_path = _write_artifact(artifacts_dir, "one.json", winner="alpha")

    result = create_strategy_analysis_note(
        output_dir=artifacts_dir,
        notes_dir=notes_dir,
        generated_at=datetime(2026, 6, 11, 1, 50, tzinfo=timezone.utc),
    )

    assert result.saved
    assert result.note_path == notes_dir / "analysis_note_multi_day_20260611T014633789491Z.md"
    markdown = result.note_path.read_text(encoding="utf-8")
    assert "# Strategy Tournament Analysis Note" in markdown
    assert "Generated timestamp: 2026-06-11T01:50:00+00:00" in markdown
    assert f"Source artifact path: `{artifact_path}`" in markdown


def test_analysis_note_selects_most_recent_valid_artifact_by_default(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_artifact(
        artifacts_dir,
        "older.json",
        timestamp="2026-06-10T01:46:33.789491+00:00",
        fixture_name="flat",
        winner="old_winner",
    )
    _write_artifact(
        artifacts_dir,
        "newer.json",
        timestamp="2026-06-11T01:46:33.789491+00:00",
        fixture_name="multi_day",
        winner="new_winner",
    )

    result = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    markdown = result.note_path.read_text(encoding="utf-8")
    assert "Winner strategy ID: new_winner" in markdown
    assert result.note_path.name == "analysis_note_multi_day_20260611T014633789491Z.md"


def test_analysis_note_skips_malformed_artifacts_safely(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    (artifacts_dir / "bad.json").write_text("{bad json", encoding="utf-8")
    _write_artifact(artifacts_dir, "good.json", winner="alpha")

    result = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    assert result.saved
    assert "Skipped malformed artifact count: 1" in result.message
    markdown = result.note_path.read_text(encoding="utf-8")
    assert "Skipped/malformed artifact count: 1" in markdown


def test_analysis_note_no_valid_artifact_behavior(tmp_path):
    result = create_strategy_analysis_note(output_dir=tmp_path / "missing", notes_dir=tmp_path / "notes")

    assert not result.saved
    assert result.note_path is None
    assert "No valid tournament artifacts found" in result.message
    assert not (tmp_path / "notes").exists()


def test_analysis_note_creates_notes_directory(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "missing" / "nested" / "notes"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json")

    result = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    assert result.saved
    assert notes_dir.exists()
    assert result.note_path.exists()


def test_analysis_note_does_not_overwrite_existing_note_without_force(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json")
    first = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)
    first.note_path.write_text("human edits", encoding="utf-8")

    second = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    assert not second.saved
    assert "Use --force to overwrite" in second.message
    assert first.note_path.read_text(encoding="utf-8") == "human edits"


def test_analysis_note_force_overwrites_existing_note(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json")
    first = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)
    first.note_path.write_text("human edits", encoding="utf-8")

    second = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir, force=True)

    assert second.saved
    assert "# Strategy Tournament Analysis Note" in second.note_path.read_text(encoding="utf-8")


def test_analysis_note_includes_winner_and_ranking_table(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "Winner strategy ID: alpha" in markdown
    assert "Winner score: 0.0400" in markdown
    assert "| Rank | Strategy ID | Score | Strategy Return | SPY Return | Excess Return | Max Drawdown | Trade Count | Rejected Trade Count |" in markdown
    assert "| 1 | alpha | 0.0400 | 6.00% | 3.00% | 4.00% | -1.00% | 2 | 0 |" in markdown


def test_analysis_note_includes_human_review_prompts(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "## Human Review Prompts" in markdown
    assert "### What won?" in markdown
    assert "### Why did it win?" in markdown
    assert "### Was the edge real or fixture-specific?" in markdown
    assert "### What risks showed up?" in markdown
    assert "### What should be tested next?" in markdown
    assert "### Should this strategy be promoted, modified, or retired?" in markdown


def test_analysis_note_includes_decision_checkboxes(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "- [ ] promote" in markdown
    assert "- [ ] modify" in markdown
    assert "- [ ] retest" in markdown
    assert "- [ ] retire" in markdown
    assert "- [ ] no decision yet" in markdown


def test_analysis_note_includes_safety_disclaimer_and_score_formula(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert SCORE_FORMULA in markdown
    assert "local/dry-run research" in markdown
    assert "not live trading" in markdown
    assert "No options" in markdown
    assert "No margin" in markdown
    assert "No shorting" in markdown
    assert "Hermes runtime is disabled" in markdown


def test_analysis_note_cli_output_includes_saved_note_path(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "good.json", winner="alpha")
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "create-analysis-note",
            "--output-dir",
            str(artifacts_dir),
            "--notes-dir",
            str(notes_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    note_path = notes_dir / "analysis_note_multi_day_20260611T014633789491Z.md"
    assert result.returncode == 0
    assert f"Saved strategy analysis note: {note_path}" in result.stdout
    assert note_path.exists()
    assert "Traceback" not in result.stderr


def test_analysis_note_cli_empty_directory_has_no_stack_trace(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "create-analysis-note",
            "--output-dir",
            str(tmp_path / "empty"),
            "--notes-dir",
            str(tmp_path / "notes"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "No valid tournament artifacts found" in result.stdout
    assert "Traceback" not in result.stderr


def _generated_note_markdown(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json", winner="alpha")
    result = create_strategy_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)
    return result.note_path.read_text(encoding="utf-8")


def _write_artifact(
    output_dir,
    filename,
    timestamp="2026-06-11T01:46:33.789491+00:00",
    fixture_name="multi_day",
    winner="alpha",
):
    artifact_path = output_dir / filename
    payload = {
        "experiment_timestamp": timestamp,
        "fixture_name": fixture_name,
        "score_formula": SCORE_FORMULA,
        "score_explanation": "Higher is better.",
        "results": [
            _result_row(rank=1, strategy_id=winner, score=0.04, excess_return=0.04, max_drawdown=-0.01),
            _result_row(rank=2, strategy_id="runner_up", score=0.02, excess_return=0.02, max_drawdown=-0.01),
        ],
    }
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _result_row(rank, strategy_id, score, excess_return, max_drawdown):
    return {
        "rank": rank,
        "strategy_id": strategy_id,
        "run_id": f"{strategy_id}-run",
        "score": score,
        "starting_equity": 10000,
        "current_equity": 10600,
        "strategy_return": 0.06,
        "spy_return": 0.03,
        "excess_return": excess_return,
        "max_drawdown": max_drawdown,
        "trade_count": 2,
        "rejected_trade_count": 0,
        "score_formula": SCORE_FORMULA,
        "score_explanation": "Higher is better.",
    }
