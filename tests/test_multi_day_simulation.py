import os
import subprocess
import sys

import pytest

from src.config.settings import Settings
from src.db.database import initialize_database
from src.execution.local_runner import run_strategy_dry_run
from src.reporting.report_generator import generate_daily_report
from src.simulation.multi_day_fixture import (
    NAMED_SIMULATION_FIXTURES,
    fixture_spy_return,
    get_simulation_fixture,
    multi_day_fixture_spy_return,
)
from src.strategies.cash_only import CashOnlyStrategy
from src.strategies.momentum_v1 import MomentumV1Strategy


def test_multi_day_fixture_produces_non_zero_spy_return():
    assert multi_day_fixture_spy_return() == pytest.approx(0.03)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "bull_trend",
        "bear_trend",
        "sideways_chop",
        "volatile_reversal",
        "spy_outperformance",
        "momentum_crash",
    ],
)
def test_new_named_fixtures_are_registered(fixture_name):
    assert fixture_name in NAMED_SIMULATION_FIXTURES
    assert get_simulation_fixture(fixture_name)


@pytest.mark.parametrize("fixture_name", NAMED_SIMULATION_FIXTURES)
def test_named_fixtures_are_deterministic(fixture_name):
    first = get_simulation_fixture(fixture_name)
    second = get_simulation_fixture(fixture_name)

    assert first == second


@pytest.mark.parametrize(
    "fixture_name",
    [
        "bull_trend",
        "bear_trend",
        "sideways_chop",
        "volatile_reversal",
        "spy_outperformance",
        "momentum_crash",
    ],
)
def test_new_named_fixtures_include_spy_benchmark_movement(fixture_name):
    assert fixture_spy_return(fixture_name) != 0


def test_multi_day_simulation_produces_non_zero_strategy_return(tmp_path):
    database_path = tmp_path / "momentum_multi_day.sqlite3"
    initialize_database(database_path)

    result = run_strategy_dry_run(
        MomentumV1Strategy(),
        _settings(database_path),
        simulation_fixture="multi_day",
    )
    report = generate_daily_report(database_path, run_id=result.run_id).report

    assert report["strategy_id"] == "momentum_v1"
    assert report["strategy_return"] == pytest.approx(0.02184065934)
    assert report["strategy_return"] != 0


def test_multi_day_excess_return_and_drawdown_are_calculated_from_curve(tmp_path):
    database_path = tmp_path / "momentum_metrics.sqlite3"
    initialize_database(database_path)

    result = run_strategy_dry_run(
        MomentumV1Strategy(),
        _settings(database_path),
        simulation_fixture="multi_day",
    )
    report = generate_daily_report(database_path, run_id=result.run_id).report

    assert report["spy_return"] == pytest.approx(0.03)
    assert report["excess_return"] == pytest.approx(report["strategy_return"] - report["spy_return"])
    assert report["max_drawdown"] == pytest.approx(-0.01350061366)


def test_multi_day_cash_only_remains_zero_return_baseline(tmp_path):
    database_path = tmp_path / "cash_multi_day.sqlite3"
    initialize_database(database_path)

    result = run_strategy_dry_run(
        CashOnlyStrategy(),
        _settings(database_path),
        simulation_fixture="multi_day",
    )
    report = generate_daily_report(database_path, run_id=result.run_id).report

    assert report["strategy_return"] == 0.0
    assert report["max_drawdown"] == 0.0
    assert report["spy_return"] == pytest.approx(0.03)
    assert report["excess_return"] == pytest.approx(-0.03)


def test_compare_strategies_default_output_includes_non_zero_fixture_metrics(tmp_path):
    database_path = tmp_path / "comparison_multi_day.sqlite3"
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
    assert "SPY return" in result.stdout
    assert "3.00%" in result.stdout
    assert "0.60%" in result.stdout
    assert "2.18%" in result.stdout


def test_compare_strategies_accepts_explicit_multi_day_fixture_without_credentials(tmp_path):
    database_path = tmp_path / "comparison_multi_day_flag.sqlite3"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies", "--fixture", "multi_day"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "cash_only" in result.stdout
    assert "spy_buy_hold" in result.stdout
    assert "momentum_v1" in result.stdout
    assert "3.00%" in result.stdout


@pytest.mark.parametrize(
    "fixture_name",
    [
        "bull_trend",
        "bear_trend",
        "sideways_chop",
        "volatile_reversal",
        "spy_outperformance",
        "momentum_crash",
    ],
)
def test_compare_strategies_accepts_new_fixtures_without_credentials(tmp_path, fixture_name):
    database_path = tmp_path / f"comparison_{fixture_name}.sqlite3"
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies", "--fixture", fixture_name],
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


def test_momentum_crash_fixture_is_challenging_for_momentum(tmp_path):
    database_path = tmp_path / "momentum_crash.sqlite3"
    initialize_database(database_path)

    result = run_strategy_dry_run(
        MomentumV1Strategy(),
        _settings(database_path),
        simulation_fixture="momentum_crash",
    )
    report = generate_daily_report(database_path, run_id=result.run_id).report

    assert report["strategy_return"] < 0
    assert report["max_drawdown"] < 0


def test_spy_outperformance_fixture_beats_momentum_strategy_return(tmp_path):
    database_path = tmp_path / "spy_outperformance.sqlite3"
    initialize_database(database_path)

    result = run_strategy_dry_run(
        MomentumV1Strategy(),
        _settings(database_path),
        simulation_fixture="spy_outperformance",
    )
    report = generate_daily_report(database_path, run_id=result.run_id).report

    assert report["spy_return"] > report["strategy_return"]
    assert report["excess_return"] < 0


def _settings(database_path):
    return Settings(
        alpaca_api_key=None,
        alpaca_secret_key=None,
        alpaca_paper=None,
        alpaca_base_url="",
        database_path=database_path,
        dry_run=True,
        starting_equity=10000,
        min_cash_pct=0.10,
        max_position_pct=0.20,
        max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )
