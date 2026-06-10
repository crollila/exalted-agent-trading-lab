from __future__ import annotations

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.strategies.base import Strategy


class MomentumV1Strategy(Strategy):
    strategy_id = "momentum_v1"
    name = "Simple Momentum V1"

    def __init__(self, symbols: list[str] | None = None, target_weight: float = 0.10):
        self.symbols = symbols or ["NVDA", "MSFT", "AAPL"]
        self.target_weight = target_weight

    def generate_proposals(self, portfolio: PortfolioState) -> list[TradeProposal]:
        # Placeholder only.
        # Phase 4 should replace this with real deterministic momentum logic.
        proposals: list[TradeProposal] = []

        for symbol in self.symbols:
            if portfolio.has_position(symbol):
                continue

            proposals.append(
                TradeProposal(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    action=TradeAction.BUY,
                    asset_class=AssetClass.STOCK,
                    target_weight=self.target_weight,
                    estimated_price=100.0,
                    thesis="Placeholder momentum candidate. Replace with real momentum ranking in Phase 4.",
                    confidence=0.50,
                )
            )

        return proposals
