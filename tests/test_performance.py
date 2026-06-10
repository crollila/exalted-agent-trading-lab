import pytest

from src.portfolio.performance import benchmark_equity_from_price, excess_return, max_drawdown, pct_return
from src.reporting.benchmark_report import BenchmarkReport


def test_pct_return():
    assert pct_return(100, 110) == 0.10


def test_excess_return():
    assert excess_return(0.12, 0.05) == pytest.approx(0.07)


def test_benchmark_equity_from_price():
    assert benchmark_equity_from_price(10000, 500, 550) == 11000


def test_max_drawdown():
    curve = [100, 120, 90, 110]
    assert round(max_drawdown(curve), 4) == -0.25


def test_benchmark_report_calculates_spy_comparison():
    report = BenchmarkReport(
        starting_equity=10000,
        current_strategy_equity=10500,
        starting_spy_price=500,
        current_spy_price=550,
    ).to_dict()

    assert report["current_strategy_equity"] == 10500
    assert report["benchmark_equity"] == 11000
    assert report["strategy_return"] == pytest.approx(0.05)
    assert report["spy_return"] == pytest.approx(0.10)
    assert report["excess_return_vs_spy"] == pytest.approx(-0.05)
