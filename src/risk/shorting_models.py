from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.brokers.order_models import AssetClass


class ShortAction(str, Enum):
    SHORT = "short"
    SELL_SHORT = "sell_short"


class ShortProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    strategy_id: str
    symbol: str
    asset_class: AssetClass = AssetClass.STOCK
    action: ShortAction
    target_short_weight: float | None = Field(default=None, gt=0.0, le=1.0)
    notional_exposure: float | None = Field(default=None, gt=0.0)
    estimated_price: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    borrow_available_assumption: bool
    borrow_fee_assumption: float | None = Field(default=None, ge=0.0)
    max_loss_exit_price: float | None = Field(default=None, gt=0.0)
    forced_cover_threshold: float | None = Field(default=None, gt=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("strategy_id")
    @classmethod
    def strategy_id_must_not_be_empty(cls, value: str) -> str:
        strategy_id = value.strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        return strategy_id

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

    @model_validator(mode="after")
    def validate_short_proposal(self) -> "ShortProposal":
        if self.asset_class != AssetClass.STOCK:
            raise ValueError("short proposals are stock-only future models")
        if self.target_short_weight is None and self.notional_exposure is None:
            raise ValueError("target_short_weight or notional_exposure is required")
        if self.max_loss_exit_price is not None and self.max_loss_exit_price <= self.estimated_price:
            raise ValueError("max_loss_exit_price must be greater than estimated_price")
        if self.forced_cover_threshold is not None and self.forced_cover_threshold <= self.estimated_price:
            raise ValueError("forced_cover_threshold must be greater than estimated_price")
        return self


class ShortRiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shorting_permission_enabled: bool = False
    max_short_exposure: float = Field(gt=0.0, le=1.0)
    max_gross_exposure: float = Field(gt=0.0)
    max_net_exposure: float = Field(ge=0.0)
    max_loss_per_short_position: float = Field(gt=0.0)
    require_borrow_available_assumption: bool = True
    forced_cover_enabled: bool = True


class ShortRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    approved: bool = False
    reasons: list[str]
    shorting_permission_enabled: bool = False
    estimated_short_exposure: float = Field(ge=0.0)
    forced_cover_required: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

