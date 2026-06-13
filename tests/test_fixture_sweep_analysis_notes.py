import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from src.reporting.fixture_sweep_analysis_notes import create_sweep_analysis_note
from src.reporting.strategy_comparison import SCORE_EXPLANATION, SCORE_FORMULA


def test_sweep_analysis_note_generation_from_one_valid_artifact(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    artifact_path = _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json", champion="alpha")

    result = create_sweep_analysis_note(
        output_dir=artifacts_dir,
        notes_dir=notes_dir,
        generated_at=datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc),
    )

    assert result.saved
    assert result.note_path == notes_dir / "sweep_analysis_note_20260613T015152211185Z.md"
    markdown = result.note_path.read_text(encoding="utf-8")
    assert "# Fixture Sweep Analysis Note" in markdown
    assert "Generated timestamp: 2026-06-13T02:00:00+00:00" in markdown
    assert f"Source sweep artifact path: `{artifact_path}`" in markdown


def test_sweep_analysis_note_selects_most_recent_valid_artifact_by_default(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_sweep_artifact(
        artifacts_dir,
        "fixture_sweep_older.json",
        timestamp="2026-06-12T01:51:52.211185+00:00",
        champion="old_winner",
    )
    _write_sweep_artifact(
        artifacts_dir,
        "fixture_sweep_newer.json",
        timestamp="2026-06-13T01:51:52.211185+00:00",
        champion="new_winner",
    )

    result = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    markdown = result.note_path.read_text(encoding="utf-8")
    assert "Overall robust champion: new_winner" in markdown
    assert result.note_path.name == "sweep_analysis_note_20260613T015152211185Z.md"


def test_sweep_analysis_note_skips_malformed_artifacts_safely(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    (artifacts_dir / "fixture_sweep_bad.json").write_text("{bad json", encoding="utf-8")
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_good.json", champion="alpha")

    result = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    assert result.saved
    assert "Skipped malformed artifact count: 1" in result.message
    markdown = result.note_path.read_text(encoding="utf-8")
    assert "Skipped/malformed artifact count: 1" in markdown


def test_sweep_analysis_note_no_valid_artifact_behavior(tmp_path):
    result = create_sweep_analysis_note(output_dir=tmp_path / "missing", notes_dir=tmp_path / "notes")

    assert not result.saved
    assert result.note_path is None
    assert "No valid fixture sweep artifacts found" in result.message
    assert not (tmp_path / "notes").exists()


def test_sweep_analysis_note_creates_notes_directory(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "missing" / "nested" / "notes"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json")

    result = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    assert result.saved
    assert notes_dir.exists()
    assert result.note_path.exists()


def test_sweep_analysis_note_does_not_overwrite_existing_note_without_force(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json")
    first = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)
    first.note_path.write_text("human edits", encoding="utf-8")

    second = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    assert not second.saved
    assert "Use --force to overwrite" in second.message
    assert first.note_path.read_text(encoding="utf-8") == "human edits"


def test_sweep_analysis_note_force_overwrites_existing_note(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json")
    first = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)
    first.note_path.write_text("human edits", encoding="utf-8")

    second = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir, force=True)

    assert second.saved
    assert "# Fixture Sweep Analysis Note" in second.note_path.read_text(encoding="utf-8")


def test_sweep_analysis_note_includes_source_artifact_path(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    artifact_path = _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json")

    result = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)

    markdown = result.note_path.read_text(encoding="utf-8")
    assert f"Source sweep artifact path: `{artifact_path}`" in markdown


def test_sweep_analysis_note_includes_robust_champion(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "Overall robust champion: alpha" in markdown
    assert "Champion wins: 2" in markdown
    assert "Champion average score: 0.0350" in markdown
    assert "Champion average excess return: 5.00%" in markdown
    assert "Champion worst max drawdown: -2.00%" in markdown


def test_sweep_analysis_note_includes_per_fixture_winner_table(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "## Per-Fixture Winners" in markdown
    assert "| Fixture | Winning Strategy | Winning Score |" in markdown
    assert "| multi_day | alpha | 0.0400 |" in markdown
    assert "| bear_trend | alpha | 0.0300 |" in markdown


def test_sweep_analysis_note_includes_strategy_robustness_table(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "## Strategy Robustness" in markdown
    assert "| Strategy ID | Fixture Appearances | Fixture Wins | Win Rate | Average Score | Average Excess Return | Worst Max Drawdown |" in markdown
    assert "| alpha | 2 | 2 | 100.00% | 0.0350 | 5.00% | -2.00% |" in markdown


def test_sweep_analysis_note_includes_human_review_prompts(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "## Human Review Prompts" in markdown
    assert "### Which strategy was most robust?" in markdown
    assert "### Did cash winning indicate strategy weakness?" in markdown
    assert "### Which strategy failed in hostile regimes?" in markdown
    assert "### Which fixture exposed the biggest weakness?" in markdown
    assert "### Is the champion robust enough to promote, or should it be retested?" in markdown
    assert "### What scenario should be added next?" in markdown


def test_sweep_analysis_note_includes_decision_checklist(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert "- [ ] promote" in markdown
    assert "- [ ] modify" in markdown
    assert "- [ ] retest" in markdown
    assert "- [ ] retire" in markdown
    assert "- [ ] no decision yet" in markdown


def test_sweep_analysis_note_includes_safety_disclaimer_and_score_formula(tmp_path):
    markdown = _generated_note_markdown(tmp_path)

    assert SCORE_FORMULA in markdown
    assert SCORE_EXPLANATION in markdown
    assert "local deterministic research only" in markdown
    assert "not live trading" in markdown
    assert "No options" in markdown
    assert "No margin" in markdown
    assert "No shorting" in markdown
    assert "Hermes runtime is disabled" in markdown


def test_sweep_analysis_note_cli_output_includes_saved_note_path(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_good.json", champion="alpha")
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
            "create-sweep-analysis-note",
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

    note_path = notes_dir / "sweep_analysis_note_20260613T015152211185Z.md"
    assert result.returncode == 0
    assert f"Saved fixture sweep analysis note: {note_path}" in result.stdout
    assert note_path.exists()
    assert "Traceback" not in result.stderr


def test_sweep_analysis_note_cli_empty_directory_has_no_stack_trace(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "create-sweep-analysis-note",
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
    assert "No valid fixture sweep artifacts found" in result.stdout
    assert "Traceback" not in result.stderr


def _generated_note_markdown(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    notes_dir = tmp_path / "notes"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json", champion="alpha")
    result = create_sweep_analysis_note(output_dir=artifacts_dir, notes_dir=notes_dir)
    return result.note_path.read_text(encoding="utf-8")


def _write_sweep_artifact(
    output_dir,
    filename,
    timestamp="2026-06-13T01:51:52.211185+00:00",
    champion="alpha",
):
    payload = {
        "sweep_timestamp": timestamp,
        "fixtures_included": ["multi_day", "bear_trend"],
        "score_formula": SCORE_FORMULA,
        "score_explanation": SCORE_EXPLANATION,
        "overall_champion": _aggregate_row(champion, fixture_count=2, wins=2, average_score=0.035),
        "per_fixture_winners": [
            {"fixture_name": "multi_day", "strategy_id": champion, "score": 0.04},
            {"fixture_name": "bear_trend", "strategy_id": champion, "score": 0.03},
        ],
        "strategy_aggregates": [
            _aggregate_row(champion, fixture_count=2, wins=2, average_score=0.035),
            _aggregate_row("runner_up", fixture_count=2, wins=0, average_score=0.015),
        ],
    }
    artifact_path = output_dir / filename
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _aggregate_row(strategy_id, fixture_count, wins, average_score):
    return {
        "strategy_id": strategy_id,
        "fixture_count": fixture_count,
        "wins": wins,
        "average_score": average_score,
        "average_excess_return": 0.05,
        "worst_max_drawdown": -0.02,
    }
