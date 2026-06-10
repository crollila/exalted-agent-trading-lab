from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.brokers.order_models import AssetClass, TradeAction


class HermesTradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    action: TradeAction
    asset_class: AssetClass = AssetClass.STOCK
    target_weight: float = Field(gt=0.0, le=1.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("symbol")
    @classmethod
    def symbol_must_not_be_empty(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be empty")
        return symbol

    @field_validator("thesis")
    @classmethod
    def thesis_must_not_be_empty(cls, value: str) -> str:
        thesis = value.strip()
        if not thesis:
            raise ValueError("thesis must not be empty")
        return thesis


class HermesProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    proposals: list[HermesTradeProposal]
    portfolio_notes: str

    @field_validator("strategy_id")
    @classmethod
    def strategy_id_must_not_be_empty(cls, value: str) -> str:
        strategy_id = value.strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        return strategy_id
