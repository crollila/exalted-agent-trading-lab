import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from src.reporting.fixture_sweep_leaderboard_export import export_fixture_sweep_leaderboard
from src.reporting.strategy_comparison import SCORE_EXPLANATION, SCORE_FORMULA


def test_fixture_sweep_leaderboard_generates_report_with_one_artifact(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json", champion="alpha")
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"

    result = export_fixture_sweep_leaderboard(
        output_dir=artifacts_dir,
        report_path=report_path,
        generated_at=datetime(2026, 6, 11, 5, 0, tzinfo=timezone.utc),
    )

    assert result.saved
    assert report_path.exists()
    markdown = report_path.read_text(encoding="utf-8")
    assert "# Fixture Sweep Leaderboard" in markdown
    assert "Generated timestamp: 2026-06-11T05:00:00+00:00" in markdown
    assert "Champion strategy ID: `alpha`" in markdown


def test_fixture_sweep_leaderboard_generates_report_with_multiple_artifacts(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_sweep_artifact(
        artifacts_dir,
        "fixture_sweep_one.json",
        timestamp="2026-06-11T05:00:00+00:00",
        champion="alpha",
    )
    _write_sweep_artifact(
        artifacts_dir,
        "fixture_sweep_two.json",
        timestamp="2026-06-11T06:00:00+00:00",
        champion="beta",
    )
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"

    result = export_fixture_sweep_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    markdown = report_path.read_text(encoding="utf-8")
    assert result.saved
    assert "Valid sweeps reviewed: 2" in markdown
    assert "| alpha | unknown | 2 | 2 | 100.00%" in markdown
    assert "| beta | unknown | 2 | 2 | 100.00%" in markdown


def test_fixture_sweep_leaderboard_includes_champion_summary(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Current Robust Champion" in markdown
    assert "Champion strategy ID: `alpha`" in markdown
    assert "Fixture wins: 2" in markdown
    assert "Win rate: 100.00%" in markdown


def test_fixture_sweep_leaderboard_includes_per_fixture_winner_table(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Per-Fixture Winners" in markdown
    assert "| Fixture | Winning Strategy | Winning Score |" in markdown
    assert "| multi_day | alpha | 0.0400 |" in markdown
    assert "| bear_trend | alpha | 0.0300 |" in markdown


def test_fixture_sweep_leaderboard_includes_strategy_robustness_aggregate_table(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Strategy Robustness Aggregates" in markdown
    assert "| Strategy ID | Status | Fixture Appearances | Fixture Wins | Win Rate | Average Score | Average Excess Return | Worst Max Drawdown |" in markdown
    assert "| alpha | unknown | 2 | 2 | 100.00% | 0.0350 | 5.00% | -2.00% |" in markdown


def test_fixture_sweep_leaderboard_includes_score_explanation(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Score Formula" in markdown
    assert SCORE_FORMULA in markdown
    assert SCORE_EXPLANATION in markdown


def test_fixture_sweep_leaderboard_includes_safety_disclaimer(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Safety Disclaimer" in markdown
    assert "local deterministic research only" in markdown
    assert "not live trading" in markdown
    assert "No options" in markdown
    assert "No margin" in markdown
    assert "No shorting" in markdown
    assert "Hermes runtime is disabled" in markdown


def test_fixture_sweep_leaderboard_includes_caveats(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Caveats" in markdown
    assert "not proof of a real trading edge" in markdown
    assert "Cross-fixture robustness is still simulated" in markdown
    assert "guide research, not trading decisions" in markdown


def test_fixture_sweep_leaderboard_creates_output_directory(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json", champion="alpha")
    report_path = tmp_path / "missing" / "nested" / "fixture_sweep_leaderboard.md"

    result = export_fixture_sweep_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    assert result.saved
    assert report_path.exists()


def test_fixture_sweep_leaderboard_no_artifact_behavior(tmp_path):
    artifacts_dir = tmp_path / "empty_artifacts"
    artifacts_dir.mkdir()
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"

    result = export_fixture_sweep_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    assert not result.saved
    assert "No valid fixture sweep artifacts found" in result.message
    assert not report_path.exists()


def test_fixture_sweep_leaderboard_malformed_artifact_skip_behavior(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "fixture_sweep_bad.json").write_text("{bad json", encoding="utf-8")
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_good.json", champion="alpha")
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"

    result = export_fixture_sweep_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    markdown = report_path.read_text(encoding="utf-8")
    assert result.saved
    assert "Skipped/malformed artifact count: 1" in markdown
    assert "fixture_sweep_bad.json" in markdown


def test_fixture_sweep_leaderboard_cli_output_includes_saved_report_path(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_good.json", champion="alpha")
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"
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
            "export-fixture-sweep-leaderboard",
            "--output-dir",
            str(artifacts_dir),
            "--report-path",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert f"Saved fixture sweep leaderboard report: {report_path}" in result.stdout
    assert report_path.exists()
    assert "Traceback" not in result.stderr


def _exported_markdown(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json", champion="alpha")
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"
    export_fixture_sweep_leaderboard(
        output_dir=artifacts_dir,
        report_path=report_path,
        generated_at=datetime(2026, 6, 11, 5, 0, tzinfo=timezone.utc),
    )
    return report_path.read_text(encoding="utf-8")


def _write_sweep_artifact(
    output_dir,
    filename,
    timestamp="2026-06-11T04:00:00+00:00",
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
