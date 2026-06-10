import csv
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.reporting.strategy_comparison import (
    ARTIFACT_COLUMNS,
    SCORE_FORMULA,
    format_strategy_comparison,
    rank_strategy_reports,
    save_strategy_comparison_artifacts,
    score_strategy_report,
)


def test_compare_strategies_creates_separate_runs_for_all_local_strategies(tmp_path):
    database_path = tmp_path / "comparison.sqlite3"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout
    assert "cash_only" in result.stdout
    assert "spy_buy_hold" in result.stdout
    assert "momentum_v1" in result.stdout

    with sqlite3.connect(database_path) as conn:
        run_rows = conn.execute(
            "SELECT id, strategy_id, status FROM runs ORDER BY started_at ASC, id ASC"
        ).fetchall()
        run_ids = [row[0] for row in run_rows]
        strategy_ids = [row[1] for row in run_rows]

        assert len(run_rows) == 3
        assert len(set(run_ids)) == 3
        assert strategy_ids == ["cash_only", "spy_buy_hold", "momentum_v1"]
        assert all(row[2] == "completed" for row in run_rows)


def test_compare_strategy_reports_are_isolated_by_run(tmp_path):
    database_path = tmp_path / "isolated_comparison.sqlite3"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0

    with sqlite3.connect(database_path) as conn:
        rows = conn.execute(
            '''
            SELECT r.strategy_id, r.id,
                   (SELECT COUNT(*) FROM trade_proposals p WHERE p.run_id = r.id) AS proposals,
                   (SELECT COUNT(*) FROM orders o WHERE o.run_id = r.id) AS orders,
                   (SELECT COUNT(*) FROM risk_decisions d WHERE d.run_id = r.id AND d.approved = 0) AS rejected,
                   (SELECT COUNT(*) FROM portfolio_snapshots ps WHERE ps.run_id = r.id) AS portfolio_snapshots,
                   (SELECT COUNT(*) FROM benchmark_snapshots bs WHERE bs.run_id = r.id) AS benchmark_snapshots
            FROM runs r
            ORDER BY r.started_at ASC, r.id ASC
            '''
        ).fetchall()

    by_strategy = {
        strategy_id: {
            "run_id": run_id,
            "proposals": proposals,
            "orders": orders,
            "rejected": rejected,
            "portfolio_snapshots": portfolio_snapshots,
            "benchmark_snapshots": benchmark_snapshots,
        }
        for (
            strategy_id,
            run_id,
            proposals,
            orders,
            rejected,
            portfolio_snapshots,
            benchmark_snapshots,
        ) in rows
    }
    assert by_strategy["cash_only"]["proposals"] == 0
    assert by_strategy["cash_only"]["orders"] == 0
    assert by_strategy["spy_buy_hold"]["proposals"] == 1
    assert by_strategy["spy_buy_hold"]["orders"] == 1
    assert by_strategy["momentum_v1"]["proposals"] == 2
    assert by_strategy["momentum_v1"]["orders"] == 2
    assert all(data["rejected"] == 0 for data in by_strategy.values())
    assert all(data["portfolio_snapshots"] == 5 for data in by_strategy.values())
    assert all(data["benchmark_snapshots"] == 5 for data in by_strategy.values())


def test_score_calculation_uses_excess_return_drawdown_and_rejected_trades():
    report = _comparison_report(
        strategy_id="scored",
        excess_return=0.05,
        max_drawdown=-0.02,
        rejected_trade_count=2,
    )

    assert score_strategy_report(report) == pytest.approx(0.01)


def test_score_penalizes_larger_drawdowns():
    mild_drawdown = _comparison_report(strategy_id="mild", excess_return=0.05, max_drawdown=-0.01)
    large_drawdown = _comparison_report(strategy_id="large", excess_return=0.05, max_drawdown=-0.04)

    assert score_strategy_report(mild_drawdown) > score_strategy_report(large_drawdown)


def test_score_penalizes_rejected_trades():
    clean = _comparison_report(strategy_id="clean", excess_return=0.05, rejected_trade_count=0)
    rejected = _comparison_report(strategy_id="rejected", excess_return=0.05, rejected_trade_count=2)

    assert score_strategy_report(clean) > score_strategy_report(rejected)


def test_ranking_sorts_best_score_first():
    ranked = rank_strategy_reports(
        [
            _comparison_report(strategy_id="middle", excess_return=0.02),
            _comparison_report(strategy_id="best", excess_return=0.04),
            _comparison_report(strategy_id="worst", excess_return=-0.01),
        ]
    )

    assert [row["strategy_id"] for row in ranked] == ["best", "middle", "worst"]
    assert [row["rank"] for row in ranked] == [1, 2, 3]


def test_ranking_uses_deterministic_tie_breakers():
    ranked = rank_strategy_reports(
        [
            _comparison_report(strategy_id="z_alpha_last", excess_return=0.01, max_drawdown=0.0),
            _comparison_report(strategy_id="a_alpha_first", excess_return=0.01, max_drawdown=0.0),
            _comparison_report(strategy_id="higher_excess", excess_return=0.03, max_drawdown=-0.02),
            _comparison_report(strategy_id="lower_drawdown", excess_return=0.03, max_drawdown=-0.01, rejected_trade_count=1),
            _comparison_report(strategy_id="lower_excess", excess_return=0.02, max_drawdown=-0.01),
        ]
    )

    assert [row["strategy_id"] for row in ranked] == [
        "lower_drawdown",
        "higher_excess",
        "lower_excess",
        "a_alpha_first",
        "z_alpha_last",
    ]


def test_comparison_output_includes_required_metrics():
    output = format_strategy_comparison(
        [
            _comparison_report(strategy_id="cash_only", run_id="run-123456")
        ]
    )

    assert "rank" in output
    assert "strategy ID" in output
    assert "run_id" in output
    assert "score" in output
    assert "starting equity" in output
    assert "current equity" in output
    assert "strategy return" in output
    assert "SPY return" in output
    assert "excess return" in output
    assert "max drawdown" in output
    assert "trade count" in output
    assert "rejected trade count" in output
    assert "Score formula:" in output
    assert SCORE_FORMULA in output


def test_save_strategy_comparison_artifacts_writes_json_with_required_fields(tmp_path):
    output_dir = tmp_path / "missing" / "experiments"
    artifacts = save_strategy_comparison_artifacts(
        reports=_sample_reports(),
        fixture_name="multi_day",
        output_dir=output_dir,
        experiment_timestamp=datetime(2026, 6, 10, 20, 0, tzinfo=timezone.utc),
    )

    assert output_dir.exists()
    payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
    assert payload["experiment_timestamp"] == "2026-06-10T20:00:00+00:00"
    assert payload["fixture_name"] == "multi_day"
    assert len(payload["results"]) == 1
    row = payload["results"][0]
    for field in ARTIFACT_COLUMNS:
        assert field in row
    assert row["rank"] == 1
    assert "score" in row
    assert payload["score_formula"] == SCORE_FORMULA


def test_save_strategy_comparison_artifacts_writes_csv_with_required_columns(tmp_path):
    artifacts = save_strategy_comparison_artifacts(
        reports=_sample_reports(),
        fixture_name="multi_day",
        output_dir=tmp_path / "experiments",
        experiment_timestamp=datetime(2026, 6, 10, 20, 0, tzinfo=timezone.utc),
    )

    with artifacts.csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)

    assert reader.fieldnames == list(ARTIFACT_COLUMNS)
    assert rows[0]["fixture_name"] == "multi_day"
    assert rows[0]["rank"] == "1"
    assert rows[0]["strategy_id"] == "cash_only"
    assert "score" in rows[0]
    assert rows[0]["trade_count"] == "0"


def test_save_strategy_comparison_artifacts_writes_markdown_summary(tmp_path):
    artifacts = save_strategy_comparison_artifacts(
        reports=_sample_reports(),
        fixture_name="flat",
        output_dir=tmp_path / "experiments",
        experiment_timestamp=datetime(2026, 6, 10, 20, 0, tzinfo=timezone.utc),
    )

    summary = artifacts.markdown_path.read_text(encoding="utf-8")
    assert "# Strategy Comparison Experiment" in summary
    assert "Fixture: flat" in summary
    assert "Score formula:" in summary
    assert "rank" in summary
    assert "score" in summary
    assert "Strategy Comparison" in summary
    assert "cash_only" in summary


def test_compare_strategies_save_writes_multi_day_artifacts_without_credentials(tmp_path):
    database_path = tmp_path / "comparison_save_multi_day.sqlite3"
    output_dir = tmp_path / "artifacts" / "multi_day"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "compare-strategies",
            "--fixture",
            "multi_day",
            "--save",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout
    assert "Saved comparison artifacts:" in result.stdout
    assert len(list(output_dir.glob("*.json"))) == 1
    assert len(list(output_dir.glob("*.csv"))) == 1
    assert len(list(output_dir.glob("*.md"))) == 1


def test_compare_strategies_save_writes_flat_artifacts_without_credentials(tmp_path):
    database_path = tmp_path / "comparison_save_flat.sqlite3"
    output_dir = tmp_path / "artifacts" / "flat"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "compare-strategies",
            "--fixture",
            "flat",
            "--save",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Saved comparison artifacts:" in result.stdout
    payload = json.loads(next(output_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["fixture_name"] == "flat"


def test_compare_strategies_includes_hermes_fixtures_when_selected(tmp_path):
    database_path = tmp_path / "comparison_with_hermes.sqlite3"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "compare-strategies",
            "--include-hermes-fixtures",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "hermes_conservative_fixture" in result.stdout
    assert "hermes_aggressive_fixture" in result.stdout

    with sqlite3.connect(database_path) as conn:
        strategy_ids = [
            row[0]
            for row in conn.execute("SELECT strategy_id FROM runs ORDER BY started_at ASC, id ASC").fetchall()
        ]

    assert strategy_ids == [
        "cash_only",
        "spy_buy_hold",
        "momentum_v1",
        "hermes_conservative_fixture",
        "hermes_aggressive_fixture",
    ]


def test_compare_strategies_saved_artifacts_include_hermes_fixtures_when_selected(tmp_path):
    database_path = tmp_path / "comparison_save_with_hermes.sqlite3"
    output_dir = tmp_path / "artifacts" / "hermes"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "compare-strategies",
            "--fixture",
            "multi_day",
            "--include-hermes-fixtures",
            "--save",
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(next(output_dir.glob("*.json")).read_text(encoding="utf-8"))
    result_strategy_ids = {row["strategy_id"] for row in payload["results"]}

    assert "hermes_conservative_fixture" in result_strategy_ids
    assert "hermes_aggressive_fixture" in result_strategy_ids

    with next(output_dir.glob("*.csv")).open(newline="", encoding="utf-8") as csv_file:
        csv_strategy_ids = {row["strategy_id"] for row in csv.DictReader(csv_file)}

    markdown_summary = next(output_dir.glob("*.md")).read_text(encoding="utf-8")

    assert "hermes_conservative_fixture" in csv_strategy_ids
    assert "hermes_aggressive_fixture" in csv_strategy_ids
    assert "hermes_conservative_fixture" in markdown_summary
    assert "hermes_aggressive_fixture" in markdown_summary


def test_compare_strategies_unknown_strategy_fails_cleanly(tmp_path):
    database_path = tmp_path / "unknown_comparison.sqlite3"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies", "--strategies", "not_real"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert "Traceback" not in result.stderr


def _sample_reports():
    return [
        _comparison_report(
            strategy_id="cash_only",
            run_id="run-123456",
            strategy_return=0.0,
            spy_return=0.03,
            excess_return=-0.03,
        )
    ]


def _comparison_report(
    strategy_id="test_strategy",
    run_id="run-123456",
    starting_equity=10000,
    current_equity=10000,
    strategy_return=0.0,
    spy_return=0.0,
    excess_return=0.0,
    max_drawdown=0.0,
    trade_count=0,
    rejected_trade_count=0,
):
    return {
        "strategy_id": strategy_id,
        "run_id": run_id,
        "starting_equity": starting_equity,
        "current_equity": current_equity,
        "strategy_return": strategy_return,
        "spy_return": spy_return,
        "excess_return": excess_return,
        "max_drawdown": max_drawdown,
        "trade_count": trade_count,
        "rejected_trade_count": rejected_trade_count,
    }
