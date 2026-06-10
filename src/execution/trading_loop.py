from __future__ import annotations

from dataclasses import dataclass

from src.execution.order_executor import OrderExecutor
from src.portfolio.portfolio_state import PortfolioState
from src.risk.trade_validator import TradeValidator
from src.strategies.base import Strategy


@dataclass
class TradingLoop:
    strategy: Strategy
    validator: TradeValidator
    executor: OrderExecutor

    def run_once(self, portfolio: PortfolioState) -> int:
        proposals = self.strategy.generate_proposals(portfolio)

        for proposal in proposals:
            decision = self.validator.validate(proposal=proposal, portfolio=portfolio)
            self.executor.handle_decision(proposal=proposal, decision=decision)

        return len(proposals)
