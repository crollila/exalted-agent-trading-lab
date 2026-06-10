from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class TradeAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class AssetClass(str, Enum):
    STOCK = "stock"
    OPTION = "option"
    CRYPTO = "crypto"


class TradeProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    strategy_id: str
    symbol: str
    action: TradeAction
    asset_class: AssetClass = AssetClass.STOCK
    target_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    quantity: float | None = Field(default=None, gt=0.0)
    estimated_price: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RiskDecision(BaseModel):
    proposal_id: str
    approved: bool
    reasons: list[str]
    approved_quantity: float | None = Field(default=None, ge=0.0)
    estimated_trade_value: float = Field(ge=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrderRequest(BaseModel):
    proposal_id: str
    symbol: str
    action: TradeAction
    quantity: float = Field(gt=0.0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    dry_run: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PortfolioSnapshot(BaseModel):
    equity: float = Field(gt=0.0)
    cash: float = Field(ge=0.0)
    timestamp: datetime
    strategy_id: str | None = None


class BenchmarkSnapshot(BaseModel):
    benchmark_symbol: str = "SPY"
    starting_equity: float = Field(gt=0.0)
    current_strategy_equity: float = Field(ge=0.0)
    starting_benchmark_price: float = Field(gt=0.0)
    current_benchmark_price: float = Field(gt=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
