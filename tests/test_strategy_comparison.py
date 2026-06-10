import os
import sqlite3
import subprocess
import sys

from src.reporting.strategy_comparison import format_strategy_comparison


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
                   (SELECT COUNT(*) FROM risk_decisions d WHERE d.run_id = r.id AND d.approved = 0) AS rejected
            FROM runs r
            ORDER BY r.started_at ASC, r.id ASC
            '''
        ).fetchall()

    by_strategy = {
        strategy_id: {"run_id": run_id, "proposals": proposals, "orders": orders, "rejected": rejected}
        for strategy_id, run_id, proposals, orders, rejected in rows
    }
    assert by_strategy["cash_only"]["proposals"] == 0
    assert by_strategy["cash_only"]["orders"] == 0
    assert by_strategy["spy_buy_hold"]["proposals"] == 1
    assert by_strategy["spy_buy_hold"]["orders"] == 1
    assert by_strategy["momentum_v1"]["proposals"] == 2
    assert by_strategy["momentum_v1"]["orders"] == 2
    assert all(data["rejected"] == 0 for data in by_strategy.values())


def test_comparison_output_includes_required_metrics():
    output = format_strategy_comparison(
        [
            {
                "strategy_id": "cash_only",
                "run_id": "run-123456",
                "starting_equity": 10000,
                "current_equity": 10000,
                "strategy_return": 0.0,
                "spy_return": 0.0,
                "excess_return": 0.0,
                "max_drawdown": 0.0,
                "trade_count": 0,
                "rejected_trade_count": 0,
            }
        ]
    )

    assert "strategy_id" in output
    assert "run_id" in output
    assert "starting equity" in output
    assert "current equity" in output
    assert "strategy return" in output
    assert "SPY return" in output
    assert "excess return" in output
    assert "max drawdown" in output
    assert "trade count" in output
    assert "rejected trade count" in output


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
