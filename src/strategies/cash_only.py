from __future__ import annotations

from src.brokers.order_models import TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.strategies.base import Strategy


class CashOnlyStrategy(Strategy):
    strategy_id = "cash_only"
    name = "Cash Only"

    def generate_proposals(self, portfolio: PortfolioState) -> list[TradeProposal]:
        return []
