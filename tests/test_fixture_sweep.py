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
