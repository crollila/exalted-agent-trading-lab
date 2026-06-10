import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.brokers.order_models import BenchmarkSnapshot, OrderRequest, PortfolioSnapshot, RiskDecision, TradeAction
from src.db.database import (
    create_run,
    initialize_database,
    insert_benchmark_snapshot,
    insert_order,
    insert_portfolio_snapshot,
    insert_risk_decision,
)
from src.reporting.report_generator import format_report, generate_daily_report


def test_generate_daily_report_calculates_required_metrics(tmp_path):
    database_path = tmp_path / "report.sqlite3"
    initialize_database(database_path)
    run_id = _seed_report_data(database_path)

    result = generate_daily_report(database_path)

    assert result.ok
    report = result.report
    assert report["run_id"] == run_id
    assert report["starting_equity"] == 10000
    assert report["current_equity"] == 10500
    assert report["strategy_return"] == pytest.approx(0.05)
    assert report["spy_return"] == pytest.approx(0.10)
    assert report["excess_return"] == pytest.approx(-0.05)
    assert report["max_drawdown"] == pytest.approx(-0.04545454545)
    assert report["trade_count"] == 2
    assert report["rejected_trade_count"] == 1


def test_generate_daily_report_reports_no_portfolio_data(tmp_path):
    database_path = tmp_path / "empty.sqlite3"
    initialize_database(database_path)
    create_run(database_path, strategy_id="test", strategy_name="Test", starting_equity=10000)

    result = generate_daily_report(database_path)

    assert not result.ok
    assert result.message.startswith("No portfolio snapshots found for run:")


def test_generate_daily_report_reports_missing_benchmark_data(tmp_path):
    database_path = tmp_path / "missing_benchmark.sqlite3"
    initialize_database(database_path)
    run_id = create_run(database_path, strategy_id="test", strategy_name="Test", starting_equity=10000)
    insert_portfolio_snapshot(
        database_path,
        PortfolioSnapshot(
            strategy_id="test",
            equity=10000,
            cash=10000,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        run_id=run_id,
    )

    result = generate_daily_report(database_path)

    assert not result.ok
    assert result.message == f"No benchmark snapshots found for run: {run_id}"


def test_generate_daily_report_reports_missing_run_id(tmp_path):
    database_path = tmp_path / "missing_run.sqlite3"
    initialize_database(database_path)

    result = generate_daily_report(database_path, run_id="missing")

    assert not result.ok
    assert result.message == "Run not found: missing"


def test_latest_run_report_does_not_aggregate_older_runs(tmp_path):
    database_path = tmp_path / "latest.sqlite3"
    initialize_database(database_path)
    _seed_report_data(
        database_path,
        run_id="older-run",
        trade_count=4,
        rejected_trade_count=3,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    latest_run_id = _seed_report_data(
        database_path,
        run_id="latest-run",
        trade_count=1,
        rejected_trade_count=0,
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    result = generate_daily_report(database_path)

    assert result.ok
    assert result.report["run_id"] == latest_run_id
    assert result.report["trade_count"] == 1
    assert result.report["rejected_trade_count"] == 0


def test_explicit_run_id_report_works(tmp_path):
    database_path = tmp_path / "explicit.sqlite3"
    initialize_database(database_path)
    older_run_id = _seed_report_data(
        database_path,
        run_id="older-run",
        trade_count=2,
        rejected_trade_count=1,
        current_equity=10500,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _seed_report_data(
        database_path,
        run_id="latest-run",
        trade_count=1,
        rejected_trade_count=0,
        current_equity=9900,
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    result = generate_daily_report(database_path, run_id=older_run_id)

    assert result.ok
    assert result.report["run_id"] == older_run_id
    assert result.report["current_equity"] == 10500
    assert result.report["trade_count"] == 2
    assert result.report["rejected_trade_count"] == 1


def test_format_report_is_beginner_readable():
    report = {
        "run_id": "run-123",
        "strategy_id": "test",
        "benchmark_symbol": "SPY",
        "starting_equity": 10000,
        "current_equity": 10500,
        "benchmark_equity": 11000,
        "strategy_return": 0.05,
        "spy_return": 0.10,
        "excess_return": -0.05,
        "max_drawdown": -0.045,
        "trade_count": 2,
        "rejected_trade_count": 1,
    }

    output = format_report(report)

    assert "Daily Report" in output
    assert "Run ID: run-123" in output
    assert "Starting equity: $10,000.00" in output
    assert "SPY return: 10.00%" in output
    assert "Rejected trade count: 1" in output


def test_report_cli_works_without_alpaca_credentials(tmp_path):
    database_path = tmp_path / "cli_report.sqlite3"
    initialize_database(database_path)
    run_id = _seed_report_data(database_path)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "report"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Daily Report" in result.stdout
    assert f"Run ID: {run_id}" in result.stdout
    assert "Strategy return: 5.00%" in result.stdout


def test_report_cli_accepts_explicit_run_id_without_alpaca_credentials(tmp_path):
    database_path = tmp_path / "cli_report_run_id.sqlite3"
    initialize_database(database_path)
    run_id = _seed_report_data(database_path)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "report", "--run-id", run_id],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert f"Run ID: {run_id}" in result.stdout


def _seed_report_data(
    database_path,
    run_id="test-run",
    trade_count=2,
    rejected_trade_count=1,
    current_equity=10500,
    started_at=None,
):
    create_run(
        database_path,
        strategy_id="test",
        strategy_name="Test",
        starting_equity=10000,
        run_id=run_id,
        started_at=started_at,
    )
    insert_portfolio_snapshot(
        database_path,
        PortfolioSnapshot(
            strategy_id="test",
            equity=10000,
            cash=10000,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        run_id=run_id,
    )
    insert_portfolio_snapshot(
        database_path,
        PortfolioSnapshot(
            strategy_id="test",
            equity=11000,
            cash=9000,
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        run_id=run_id,
    )
    insert_portfolio_snapshot(
        database_path,
        PortfolioSnapshot(
            strategy_id="test",
            equity=current_equity,
            cash=8500,
            timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
        run_id=run_id,
    )
    insert_benchmark_snapshot(
        database_path,
        BenchmarkSnapshot(
            starting_equity=10000,
            current_strategy_equity=10000,
            starting_benchmark_price=500,
            current_benchmark_price=500,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        run_id=run_id,
    )
    insert_benchmark_snapshot(
        database_path,
        BenchmarkSnapshot(
            starting_equity=10000,
            current_strategy_equity=current_equity,
            starting_benchmark_price=500,
            current_benchmark_price=550,
            timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
        run_id=run_id,
    )
    for index in range(trade_count):
        insert_order(
            database_path,
            OrderRequest(
                proposal_id=f"{run_id}-approved-{index}",
                symbol="SPY",
                action=TradeAction.BUY,
                quantity=1,
                dry_run=True,
                risk_approved=True,
            ),
            submitted=False,
            run_id=run_id,
        )
    for index in range(rejected_trade_count):
        insert_risk_decision(
            database_path,
            RiskDecision(
                proposal_id=f"{run_id}-rejected-{index}",
                approved=False,
                reasons=["Rejected: test."],
                estimated_trade_value=1000,
            ),
            run_id=run_id,
        )
    return run_id
