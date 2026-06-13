import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.main import FIXTURE_SWEEP_FIXTURES
from src.reporting.fixture_sweep import (
    save_fixture_sweep_artifacts,
    summarize_fixture_sweep,
)
from src.reporting.strategy_comparison import SCORE_FORMULA
from src.reporting.strategy_status import set_strategy_status


EXPECTED_SWEEP_FIXTURES = (
    "multi_day",
    "bull_trend",
    "bear_trend",
    "sideways_chop",
    "volatile_reversal",
    "spy_outperformance",
    "momentum_crash",
)


def test_fixture_sweep_runs_all_expected_fixtures():
    assert FIXTURE_SWEEP_FIXTURES == EXPECTED_SWEEP_FIXTURES


def test_fixture_sweep_excludes_flat_by_default():
    assert "flat" not in FIXTURE_SWEEP_FIXTURES


def test_fixture_sweep_calculates_per_fixture_winners():
    summary = summarize_fixture_sweep(
        {
            "multi_day": [
                _row("momentum_v1", rank=1, score=0.03),
                _row("cash_only", rank=2, score=0.0),
            ],
            "bear_trend": [
                _row("cash_only", rank=1, score=0.04),
                _row("momentum_v1", rank=2, score=-0.01),
            ],
        }
    )

    assert [(winner.fixture_name, winner.strategy_id) for winner in summary.fixture_winners] == [
        ("multi_day", "momentum_v1"),
        ("bear_trend", "cash_only"),
    ]


def test_fixture_sweep_calculates_strategy_wins():
    summary = summarize_fixture_sweep(_sample_ranked_results())

    wins_by_strategy = {row.strategy_id: row.wins for row in summary.strategy_aggregates}
    assert wins_by_strategy["momentum_v1"] == 2
    assert wins_by_strategy["cash_only"] == 1


def test_fixture_sweep_calculates_average_score():
    summary = summarize_fixture_sweep(_sample_ranked_results())

    momentum = _aggregate(summary, "momentum_v1")
    assert momentum.average_score == pytest.approx((0.03 + 0.02 - 0.01) / 3)


def test_fixture_sweep_calculates_average_excess_return():
    summary = summarize_fixture_sweep(_sample_ranked_results())

    momentum = _aggregate(summary, "momentum_v1")
    assert momentum.average_excess_return == pytest.approx((0.04 + 0.03 + 0.01) / 3)


def test_fixture_sweep_calculates_worst_drawdown_by_severity():
    summary = summarize_fixture_sweep(_sample_ranked_results())

    momentum = _aggregate(summary, "momentum_v1")
    assert momentum.worst_max_drawdown == pytest.approx(-0.04)


def test_fixture_sweep_uses_deterministic_tie_breakers():
    summary = summarize_fixture_sweep(
        {
            "tie_fixture": [
                _row("beta", rank=1, score=0.01, excess_return=0.02, max_drawdown=-0.01),
                _row("alpha", rank=2, score=0.01, excess_return=0.02, max_drawdown=-0.01),
            ],
            "tie_fixture_two": [
                _row("alpha", rank=1, score=0.01, excess_return=0.02, max_drawdown=-0.01),
                _row("beta", rank=2, score=0.01, excess_return=0.02, max_drawdown=-0.01),
            ],
        }
    )

    assert summary.overall_champion.strategy_id == "alpha"


def test_fixture_sweep_save_writes_json_csv_and_markdown_artifacts(tmp_path):
    summary = summarize_fixture_sweep(_sample_ranked_results())

    artifacts = save_fixture_sweep_artifacts(
        summary=summary,
        output_dir=tmp_path / "artifacts",
        generated_at=datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc),
    )

    assert artifacts.json_path.exists()
    assert artifacts.csv_path.exists()
    assert artifacts.markdown_path.exists()
    payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
    assert payload["sweep_timestamp"] == "2026-06-11T04:00:00+00:00"
    assert payload["fixtures_included"] == ["multi_day", "bull_trend", "bear_trend"]
    assert payload["overall_champion"]["strategy_id"] == summary.overall_champion.strategy_id
    assert payload["score_formula"] == SCORE_FORMULA

    with artifacts.csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert {row["row_type"] for row in rows} == {"fixture_winner", "strategy_aggregate"}

    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    assert "# Fixture Sweep Tournament" in markdown
    assert "Overall robust champion" in markdown


def test_fixture_sweep_cli_output_includes_robust_champion(tmp_path):
    result = _run_fixture_sweep_cli(tmp_path)

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout
    assert "Overall robust champion:" in result.stdout
    assert "Safety disclaimer:" in result.stdout
    for fixture in EXPECTED_SWEEP_FIXTURES:
        assert fixture in result.stdout
    assert "Traceback" not in result.stderr


def test_fixture_sweep_cli_include_hermes_fixtures(tmp_path):
    result = _run_fixture_sweep_cli(tmp_path, "--include-hermes-fixtures")

    assert result.returncode == 0
    assert "hermes_conservative_fixture" in result.stdout
    assert "hermes_aggressive_fixture" in result.stdout
    assert "Traceback" not in result.stderr


def test_fixture_sweep_exclude_retired_is_opt_in(tmp_path):
    registry_path = tmp_path / "notes" / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retired",
        reason="Needs replacement",
        registry_path=registry_path,
    )

    default_result = _run_fixture_sweep_cli(
        tmp_path / "default",
        "--status-registry-path",
        str(registry_path),
    )
    filtered_result = _run_fixture_sweep_cli(
        tmp_path / "filtered",
        "--exclude-retired",
        "--status-registry-path",
        str(registry_path),
    )

    assert default_result.returncode == 0
    assert filtered_result.returncode == 0
    assert "momentum_v1" in default_result.stdout
    assert "Retired strategies excluded." in filtered_result.stdout
    assert "momentum_v1 (retired)" in filtered_result.stdout
    assert "momentum_v1 |" not in filtered_result.stdout


def test_fixture_sweep_status_filter_includes_requested_statuses(tmp_path):
    registry_path = tmp_path / "notes" / "strategy_status.md"
    set_strategy_status(strategy_id="cash_only", status="active", reason="Baseline", registry_path=registry_path)
    set_strategy_status(strategy_id="momentum_v1", status="retest", reason="Needs review", registry_path=registry_path)

    result = _run_fixture_sweep_cli(
        tmp_path,
        "--status",
        "active,promoted,retest",
        "--status-registry-path",
        str(registry_path),
    )

    assert result.returncode == 0
    assert "Included statuses: active, promoted, retest" in result.stdout
    assert "spy_buy_hold (unknown)" in result.stdout
    assert "cash_only   | active" in result.stdout
    assert "momentum_v1 | retest" in result.stdout
    assert "spy_buy_hold |" not in result.stdout


def test_fixture_sweep_status_filter_skips_cleanly_when_no_strategies_match(tmp_path):
    result = _run_fixture_sweep_cli(
        tmp_path,
        "--status",
        "active,promoted,retest",
        "--status-registry-path",
        str(tmp_path / "missing.md"),
    )

    assert result.returncode == 0
    assert "Fixture sweep skipped: status filtering excluded every selected strategy." in result.stdout
    assert "cash_only (unknown)" in result.stdout
    assert "Traceback" not in result.stderr


def test_fixture_sweep_cli_save_writes_artifacts_without_credentials(tmp_path):
    output_dir = tmp_path / "artifacts"
    result = _run_fixture_sweep_cli(tmp_path, "--save", "--output-dir", str(output_dir))

    assert result.returncode == 0
    assert "Saved fixture sweep artifacts:" in result.stdout
    assert len(list(output_dir.glob("fixture_sweep_*.json"))) == 1
    assert len(list(output_dir.glob("fixture_sweep_*.csv"))) == 1
    assert len(list(output_dir.glob("fixture_sweep_*.md"))) == 1
    payload = json.loads(next(output_dir.glob("fixture_sweep_*.json")).read_text(encoding="utf-8"))
    assert payload["fixtures_included"] == list(EXPECTED_SWEEP_FIXTURES)
    assert "overall_champion" in payload


def test_fixture_sweep_saved_artifacts_include_filter_metadata(tmp_path):
    registry_path = tmp_path / "notes" / "strategy_status.md"
    set_strategy_status(strategy_id="momentum_v1", status="retired", reason="Needs replacement", registry_path=registry_path)
    output_dir = tmp_path / "artifacts"
    result = _run_fixture_sweep_cli(
        tmp_path,
        "--exclude-retired",
        "--status-registry-path",
        str(registry_path),
        "--save",
        "--output-dir",
        str(output_dir),
    )

    assert result.returncode == 0
    payload = json.loads(next(output_dir.glob("fixture_sweep_*.json")).read_text(encoding="utf-8"))
    assert payload["status_filter"]["applied"] is True
    assert payload["status_filter"]["exclude_retired"] is True
    assert payload["status_filter"]["excluded_strategies"] == [
        {"strategy_id": "momentum_v1", "status": "retired"}
    ]
    with next(output_dir.glob("fixture_sweep_*.csv")).open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert rows[0]["status_filter_applied"] == "True"
    markdown = next(output_dir.glob("fixture_sweep_*.md")).read_text(encoding="utf-8")
    assert "Status filter applied: yes" in markdown
    assert "Excluded strategies: momentum_v1 (retired)" in markdown


def _sample_ranked_results():
    return {
        "multi_day": [
            _row("momentum_v1", rank=1, score=0.03, excess_return=0.04, max_drawdown=-0.01),
            _row("cash_only", rank=2, score=0.0, excess_return=0.0, max_drawdown=0.0),
        ],
        "bull_trend": [
            _row("momentum_v1", rank=1, score=0.02, excess_return=0.03, max_drawdown=-0.02),
            _row("cash_only", rank=2, score=-0.01, excess_return=-0.01, max_drawdown=0.0),
        ],
        "bear_trend": [
            _row("cash_only", rank=1, score=0.04, excess_return=0.04, max_drawdown=0.0),
            _row("momentum_v1", rank=2, score=-0.01, excess_return=0.01, max_drawdown=-0.04),
        ],
    }


def _row(strategy_id, rank, score, excess_return=0.0, max_drawdown=0.0):
    return {
        "rank": rank,
        "strategy_id": strategy_id,
        "score": score,
        "excess_return": excess_return,
        "max_drawdown": max_drawdown,
    }


def _aggregate(summary, strategy_id):
    return next(row for row in summary.strategy_aggregates if row.strategy_id == strategy_id)


def _run_fixture_sweep_cli(tmp_path, *args):
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(tmp_path / "fixture_sweep.sqlite3")
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "src.main", "fixture-sweep", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
