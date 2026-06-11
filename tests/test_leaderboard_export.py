import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from src.reporting.leaderboard_export import export_strategy_leaderboard
from src.reporting.strategy_comparison import SCORE_FORMULA


def test_leaderboard_export_generates_report_with_one_artifact(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json", winner="alpha")
    report_path = tmp_path / "reports" / "leaderboard.md"

    result = export_strategy_leaderboard(
        output_dir=artifacts_dir,
        report_path=report_path,
        generated_at=datetime(2026, 6, 10, 21, 30, tzinfo=timezone.utc),
    )

    assert result.saved
    assert report_path.exists()
    markdown = report_path.read_text(encoding="utf-8")
    assert "# Strategy Leaderboard" in markdown
    assert "Generated timestamp: 2026-06-10T21:30:00+00:00" in markdown
    assert "Champion strategy ID: `alpha`" in markdown


def test_leaderboard_export_generates_report_with_multiple_artifacts(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json", winner="alpha")
    _write_artifact(artifacts_dir, "two.json", winner="beta")
    report_path = tmp_path / "reports" / "leaderboard.md"

    result = export_strategy_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    markdown = report_path.read_text(encoding="utf-8")
    assert result.saved
    assert "Valid tournaments reviewed: 2" in markdown
    assert "| alpha |" in markdown
    assert "| beta |" in markdown


def test_leaderboard_export_includes_champion_summary(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Current Champion" in markdown
    assert "Champion strategy ID: `alpha`" in markdown
    assert "Win rate: 100.00%" in markdown


def test_leaderboard_export_includes_score_formula(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Score Formula" in markdown
    assert SCORE_FORMULA in markdown


def test_leaderboard_export_includes_safety_disclaimer(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Safety Disclaimer" in markdown
    assert "dry-run/local research only" in markdown
    assert "not live trading" in markdown
    assert "No options" in markdown
    assert "No margin" in markdown
    assert "No shorting" in markdown
    assert "Hermes runtime is disabled" in markdown


def test_leaderboard_export_includes_strategy_aggregate_table(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Strategy Aggregates" in markdown
    assert "| Strategy ID | Appearances | Wins | Win Rate | Best Score | Average Score | Average Excess Return | Worst Max Drawdown |" in markdown
    assert "| alpha | 1 | 1 | 100.00% | 0.0400 | 0.0400 | 4.00% | -1.00% |" in markdown


def test_leaderboard_export_includes_recent_tournament_table(tmp_path):
    markdown = _exported_markdown(tmp_path)

    assert "## Recent Tournaments" in markdown
    assert "| Timestamp | Fixture | Strategies | Winner | Score | Strategy Return | SPY Return | Excess Return | Max Drawdown |" in markdown
    assert "| 2026-06-10T20:00:00+00:00 | multi_day | 2 | alpha | 0.0400 | 6.00% | 3.00% | 4.00% | -1.00% |" in markdown


def test_leaderboard_export_creates_output_directory(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json", winner="alpha")
    report_path = tmp_path / "missing" / "nested" / "leaderboard.md"

    result = export_strategy_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    assert result.saved
    assert report_path.exists()


def test_leaderboard_export_no_artifact_behavior(tmp_path):
    artifacts_dir = tmp_path / "missing_artifacts"
    report_path = tmp_path / "reports" / "leaderboard.md"

    result = export_strategy_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    assert not result.saved
    assert "No tournament artifacts found" in result.message
    assert not report_path.exists()


def test_leaderboard_export_malformed_artifact_skip_behavior(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "bad.json").write_text("{bad json", encoding="utf-8")
    _write_artifact(artifacts_dir, "good.json", winner="alpha")
    report_path = tmp_path / "reports" / "leaderboard.md"

    result = export_strategy_leaderboard(output_dir=artifacts_dir, report_path=report_path)

    markdown = report_path.read_text(encoding="utf-8")
    assert result.saved
    assert "Skipped/malformed artifact count: 1" in markdown
    assert "bad.json" in markdown


def test_leaderboard_export_cli_output_includes_saved_report_path(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "good.json", winner="alpha")
    report_path = tmp_path / "reports" / "leaderboard.md"
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
            "export-leaderboard",
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
    assert f"Saved strategy leaderboard report: {report_path}" in result.stdout
    assert report_path.exists()
    assert "Traceback" not in result.stderr


def _exported_markdown(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_artifact(artifacts_dir, "one.json", winner="alpha")
    report_path = tmp_path / "reports" / "leaderboard.md"
    export_strategy_leaderboard(
        output_dir=artifacts_dir,
        report_path=report_path,
        generated_at=datetime(2026, 6, 10, 21, 30, tzinfo=timezone.utc),
    )
    return report_path.read_text(encoding="utf-8")


def _write_artifact(
    output_dir,
    filename,
    timestamp="2026-06-10T20:00:00+00:00",
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
