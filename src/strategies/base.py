from __future__ import annotations

from abc import ABC, abstractmethod

from src.brokers.order_models import TradeProposal
from src.portfolio.portfolio_state import PortfolioState


class Strategy(ABC):
    strategy_id: str
    name: str

    @abstractmethod
    def generate_proposals(self, portfolio: PortfolioState) -> list[TradeProposal]:
        raise NotImplementedError
