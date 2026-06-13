from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class OptionAction(str, Enum):
    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"


class OptionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlying_symbol: str
    option_type: OptionType
    expiration: date
    strike: float = Field(gt=0.0)
    open_interest: int | None = Field(default=None, ge=0)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    @field_validator("underlying_symbol")
    @classmethod
    def underlying_symbol_must_not_be_empty(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("underlying_symbol must not be empty")
        return symbol

    @field_validator("expiration")
    @classmethod
    def expiration_must_not_be_zero_dte(cls, value: date) -> date:
        if value <= date.today():
            raise ValueError("expiration must be after today; 0DTE options are disabled")
        return value


class OptionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    strategy_id: str
    contract: OptionContract
    action: OptionAction
    contracts: int = Field(gt=0)
    premium: float = Field(gt=0.0)
    estimated_total_premium: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    liquidity_open_interest_assumption: str
    assignment_exercise_risk_note: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("strategy_id")
    @classmethod
    def strategy_id_must_not_be_empty(cls, value: str) -> str:
        strategy_id = value.strip()
        if not strategy_id:
            raise ValueError("strategy_id must not be empty")
        return strategy_id

    @field_validator("thesis")
    @classmethod
    def thesis_must_not_be_empty(cls, value: str) -> str:
        thesis = value.strip()
        if not thesis:
            raise ValueError("thesis must not be empty")
        return thesis

    @field_validator("liquidity_open_interest_assumption")
    @classmethod
    def liquidity_assumption_must_not_be_empty(cls, value: str) -> str:
        assumption = value.strip()
        if not assumption:
            raise ValueError("liquidity_open_interest_assumption must not be empty")
        return assumption

    @field_validator("assignment_exercise_risk_note")
    @classmethod
    def assignment_risk_note_must_not_be_empty(cls, value: str) -> str:
        note = value.strip()
        if not note:
            raise ValueError("assignment_exercise_risk_note must not be empty")
        return note


class OptionRiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options_permission_enabled: bool = False
    max_premium_at_risk: float = Field(default=1000.0, gt=0.0)
    max_contracts_per_trade: int = Field(default=1, gt=0)
    max_portfolio_option_exposure: float = Field(default=0.05, gt=0.0, le=1.0)
    no_zero_dte: bool = True
    allow_naked_short_options: bool = False
    live_options_enabled: bool = False
    broker_option_execution_enabled: bool = False


class OptionRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    approved: bool = False
    reasons: list[str]
    estimated_premium_at_risk: float = Field(ge=0.0)
    contracts: int = Field(ge=0)
    broker_option_execution_enabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def check_option_risk(
    proposal: OptionProposal,
    limits: OptionRiskLimits | None = None,
    *,
    current_option_exposure: float = 0.0,
    portfolio_equity: float | None = None,
) -> OptionRiskDecision:
    limits = limits or OptionRiskLimits()
    reasons: list[str] = []

    if not limits.options_permission_enabled:
        reasons.append("Rejected: options permission is disabled.")
    if proposal.contracts > limits.max_contracts_per_trade:
        reasons.append("Rejected: option contract count exceeds max contracts per trade.")
    if proposal.estimated_total_premium > limits.max_premium_at_risk:
        reasons.append("Rejected: option premium at risk exceeds max premium at risk.")
    if limits.no_zero_dte and proposal.contract.expiration <= date.today():
        reasons.append("Rejected: 0DTE options are disabled.")
    if limits.allow_naked_short_options:
        reasons.append("Rejected: naked short options must remain disabled.")
    if limits.live_options_enabled:
        reasons.append("Rejected: live options must remain disabled.")
    if limits.broker_option_execution_enabled:
        reasons.append("Rejected: broker option execution must remain disabled.")
    if portfolio_equity is not None:
        projected_exposure = current_option_exposure + proposal.estimated_total_premium
        if projected_exposure / portfolio_equity > limits.max_portfolio_option_exposure:
            reasons.append("Rejected: projected portfolio option exposure exceeds limit.")

    return OptionRiskDecision(
        proposal_id=proposal.proposal_id,
        approved=len(reasons) == 0,
        reasons=["Approved for future simulation only."] if not reasons else reasons,
        estimated_premium_at_risk=proposal.estimated_total_premium,
        contracts=proposal.contracts,
        broker_option_execution_enabled=limits.broker_option_execution_enabled,
    )
