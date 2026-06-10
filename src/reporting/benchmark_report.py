from __future__ import annotations

from dataclasses import dataclass

from src.portfolio.performance import benchmark_equity_from_price, excess_return, pct_return


@dataclass(frozen=True)
class BenchmarkReport:
    starting_equity: float
    current_strategy_equity: float
    starting_spy_price: float
    current_spy_price: float

    def to_dict(self) -> dict:
        strategy_return = pct_return(self.starting_equity, self.current_strategy_equity)
        benchmark_equity = benchmark_equity_from_price(
            starting_equity=self.starting_equity,
            starting_benchmark_price=self.starting_spy_price,
            current_benchmark_price=self.current_spy_price,
        )
        spy_return = pct_return(self.starting_equity, benchmark_equity)

        return {
            "starting_equity": self.starting_equity,
            "current_strategy_equity": self.current_strategy_equity,
            "benchmark_equity": benchmark_equity,
            "strategy_return": strategy_return,
            "spy_return": spy_return,
            "excess_return_vs_spy": excess_return(strategy_return, spy_return),
        }
