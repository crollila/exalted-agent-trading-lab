from __future__ import annotations

from pydantic import BaseModel, Field

from src.brokers.order_models import AssetClass, TradeAction


class HermesTradeProposal(BaseModel):
    symbol: str
    action: TradeAction
    asset_class: AssetClass = AssetClass.STOCK
    target_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)


class HermesProposalBatch(BaseModel):
    strategy_id: str
    proposals: list[HermesTradeProposal]
    portfolio_notes: str
