import os
import sqlite3
import subprocess
import sys

from src.db.database import initialize_database


def test_dry_run_creates_run_id_and_links_records(tmp_path):
    database_path = tmp_path / "dry_run.sqlite3"
    initialize_database(database_path)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "dry-run"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Run ID:" in result.stdout

    with sqlite3.connect(database_path) as conn:
        run_row = conn.execute("SELECT id, status FROM runs").fetchone()
        run_id = run_row[0]

        assert run_row[1] == "completed"
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_snapshots WHERE run_id = ?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM benchmark_snapshots WHERE run_id = ?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM trade_proposals WHERE run_id = ?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM risk_decisions WHERE run_id = ?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE run_id = ?", (run_id,)).fetchone()[0] == 1


def test_dry_run_accepts_momentum_strategy_and_links_records(tmp_path):
    database_path = tmp_path / "momentum_dry_run.sqlite3"
    initialize_database(database_path)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "dry-run", "--strategy", "momentum_v1"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy: momentum_v1" in result.stdout

    with sqlite3.connect(database_path) as conn:
        run_row = conn.execute("SELECT id, strategy_id, status FROM runs").fetchone()
        run_id = run_row[0]

        assert run_row[1:] == ("momentum_v1", "completed")
        assert conn.execute("SELECT COUNT(*) FROM trade_proposals WHERE run_id = ?", (run_id,)).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM risk_decisions WHERE run_id = ?", (run_id,)).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE run_id = ?", (run_id,)).fetchone()[0] == 2


def test_dry_run_unknown_strategy_fails_cleanly(tmp_path):
    database_path = tmp_path / "unknown_strategy.sqlite3"
    initialize_database(database_path)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "dry-run", "--strategy", "not_real"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert "Traceback" not in result.stderr
