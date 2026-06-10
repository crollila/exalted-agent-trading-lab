from __future__ import annotations


def pct_return(start_value: float, end_value: float) -> float:
    if start_value <= 0:
        raise ValueError("start_value must be greater than zero")
    return (end_value - start_value) / start_value


def excess_return(strategy_return: float, benchmark_return: float) -> float:
    return strategy_return - benchmark_return


def benchmark_equity_from_price(
    starting_equity: float,
    starting_benchmark_price: float,
    current_benchmark_price: float,
) -> float:
    if starting_benchmark_price <= 0:
        raise ValueError("starting_benchmark_price must be greater than zero")
    shares = starting_equity / starting_benchmark_price
    return shares * current_benchmark_price


def max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (value - peak) / peak
            worst = min(worst, drawdown)
    return worst
