from __future__ import annotations

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.strategies.base import Strategy


DEFAULT_PRICE_HISTORY: dict[str, list[float]] = {
    "NVDA": [100.0, 105.0, 112.0],
    "MSFT": [100.0, 102.0, 104.0],
    "AAPL": [100.0, 99.0, 98.0],
}


class MomentumV1Strategy(Strategy):
    strategy_id = "momentum_v1"
    name = "Simple Momentum V1"

    def __init__(
        self,
        price_history: dict[str, list[float]] | None = None,
        asset_classes: dict[str, AssetClass] | None = None,
        target_weight: float = 0.10,
        max_positions: int = 3,
    ):
        if target_weight <= 0 or target_weight > 0.20:
            raise ValueError("Momentum target_weight must be greater than 0 and no more than 0.20.")
        if max_positions <= 0:
            raise ValueError("Momentum max_positions must be greater than 0.")

        self.price_history = price_history or DEFAULT_PRICE_HISTORY
        self.asset_classes = asset_classes or {}
        self.target_weight = target_weight
        self.max_positions = max_positions

    def generate_proposals(self, portfolio: PortfolioState) -> list[TradeProposal]:
        proposals: list[TradeProposal] = []
        ranked = sorted(
            self._momentum_scores().items(),
            key=lambda item: (-item[1], item[0]),
        )

        for symbol, momentum in ranked:
            if len(proposals) >= self.max_positions:
                break
            if momentum <= 0:
                continue
            if self.asset_classes.get(symbol, AssetClass.STOCK) != AssetClass.STOCK:
                continue
            if portfolio.has_position(symbol):
                continue

            latest_price = self.price_history[symbol][-1]
            proposals.append(
                TradeProposal(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    action=TradeAction.BUY,
                    asset_class=AssetClass.STOCK,
                    target_weight=self.target_weight,
                    estimated_price=latest_price,
                    thesis=(
                        f"Deterministic momentum candidate: {symbol} returned "
                        f"{momentum:.2%} over the provided close-price window."
                    ),
                    confidence=self._confidence_from_momentum(momentum),
                )
            )

        return proposals

    def _momentum_scores(self) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol, closes in self.price_history.items():
            if len(closes) < 2:
                continue

            first_close = closes[0]
            latest_close = closes[-1]
            if first_close <= 0 or latest_close <= 0:
                continue

            scores[symbol] = (latest_close - first_close) / first_close
        return scores

    def _confidence_from_momentum(self, momentum: float) -> float:
        return min(1.0, max(0.0, 0.50 + momentum))
