from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    market_value: float
    average_entry_price: float | None = None


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    positions: dict[str, Position]
    timestamp: datetime

    def position_value(self, symbol: str) -> float:
        position = self.positions.get(symbol)
        return 0.0 if position is None else position.market_value

    def position_weight(self, symbol: str) -> float:
        if self.equity <= 0:
            return 0.0
        return self.position_value(symbol) / self.equity

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions and self.positions[symbol].quantity > 0
