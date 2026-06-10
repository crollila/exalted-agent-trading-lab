from __future__ import annotations

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.strategies.base import Strategy


class SpyBuyHoldStrategy(Strategy):
    strategy_id = "spy_buy_hold"
    name = "SPY Buy and Hold"

    def __init__(self, target_weight: float = 0.20, estimated_price: float = 500.0):
        self.target_weight = target_weight
        self.estimated_price = estimated_price

    def generate_proposals(self, portfolio: PortfolioState) -> list[TradeProposal]:
        if portfolio.has_position("SPY"):
            return []

        return [
            TradeProposal(
                strategy_id=self.strategy_id,
                symbol="SPY",
                action=TradeAction.BUY,
                asset_class=AssetClass.STOCK,
                target_weight=self.target_weight,
                estimated_price=self.estimated_price,
                thesis="Baseline strategy: buy SPY as benchmark exposure when not already invested.",
                confidence=1.0,
            )
        ]
