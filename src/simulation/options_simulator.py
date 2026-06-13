from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.risk.options_models import OptionAction, OptionProposal, OptionRiskLimits, OptionType


class OptionSimulationRiskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    underlying_symbol: str
    message: str
    premium_at_risk: float = Field(ge=0.0)
    limit_value: float = Field(ge=0.0)


class OptionPositionSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlying_symbol: str
    option_type: OptionType
    action: OptionAction
    strike: float = Field(gt=0.0)
    contracts: int = Field(gt=0)
    contract_multiplier: int = Field(gt=0)
    entry_premium: float = Field(gt=0.0)
    exit_premium: float = Field(ge=0.0)
    premium_paid: float = Field(gt=0.0)
    exit_value: float = Field(ge=0.0)
    realized_profit_loss: float
    max_premium_at_risk: float = Field(gt=0.0)
    return_on_premium: float
    underlying_price_at_expiration: float | None = Field(default=None, gt=0.0)
    intrinsic_value_at_expiration: float | None = Field(default=None, ge=0.0)
    expiration_outcome: str | None = None


class OptionSimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    strategy_id: str
    simulation_only: bool = True
    position: OptionPositionSimulation
    risk_events: list[OptionSimulationRiskEvent]


def simulate_option_proposal(
    proposal: OptionProposal,
    local_exit_premium: float,
    *,
    contract_multiplier: int = 100,
    risk_limits: OptionRiskLimits | None = None,
    local_underlying_price_at_expiration: float | None = None,
) -> OptionSimulationResult:
    if proposal.action not in {OptionAction.BUY_TO_OPEN, OptionAction.BUY_TO_CLOSE}:
        raise ValueError("only long-premium option actions are supported")
    if local_exit_premium < 0:
        raise ValueError("local_exit_premium must be greater than or equal to zero")
    if contract_multiplier <= 0:
        raise ValueError("contract_multiplier must be greater than zero")
    if local_underlying_price_at_expiration is not None and local_underlying_price_at_expiration <= 0:
        raise ValueError("local_underlying_price_at_expiration must be greater than zero")

    premium_paid = proposal.premium * proposal.contracts * contract_multiplier
    exit_value = local_exit_premium * proposal.contracts * contract_multiplier
    realized_profit_loss = exit_value - premium_paid
    intrinsic_value = _intrinsic_value_at_expiration(
        proposal=proposal,
        underlying_price=local_underlying_price_at_expiration,
    )

    position = OptionPositionSimulation(
        underlying_symbol=proposal.contract.underlying_symbol,
        option_type=proposal.contract.option_type,
        action=proposal.action,
        strike=proposal.contract.strike,
        contracts=proposal.contracts,
        contract_multiplier=contract_multiplier,
        entry_premium=proposal.premium,
        exit_premium=local_exit_premium,
        premium_paid=premium_paid,
        exit_value=exit_value,
        realized_profit_loss=realized_profit_loss,
        max_premium_at_risk=premium_paid,
        return_on_premium=realized_profit_loss / premium_paid,
        underlying_price_at_expiration=local_underlying_price_at_expiration,
        intrinsic_value_at_expiration=intrinsic_value,
        expiration_outcome=_expiration_outcome(
            proposal=proposal,
            underlying_price=local_underlying_price_at_expiration,
        ),
    )

    return OptionSimulationResult(
        proposal_id=proposal.proposal_id,
        strategy_id=proposal.strategy_id,
        position=position,
        risk_events=_risk_events(
            proposal=proposal,
            premium_at_risk=premium_paid,
            risk_limits=risk_limits,
        ),
    )


def _risk_events(
    proposal: OptionProposal,
    premium_at_risk: float,
    risk_limits: OptionRiskLimits | None,
) -> list[OptionSimulationRiskEvent]:
    if risk_limits is None or premium_at_risk <= risk_limits.max_premium_at_risk:
        return []

    return [
        OptionSimulationRiskEvent(
            event_type="premium_at_risk_limit_exceeded",
            underlying_symbol=proposal.contract.underlying_symbol,
            premium_at_risk=premium_at_risk,
            limit_value=risk_limits.max_premium_at_risk,
            message="Simulation-only option premium at risk exceeded the configured limit.",
        )
    ]


def _intrinsic_value_at_expiration(
    proposal: OptionProposal,
    underlying_price: float | None,
) -> float | None:
    if underlying_price is None:
        return None
    if proposal.contract.option_type == OptionType.CALL:
        return max(underlying_price - proposal.contract.strike, 0.0)
    return max(proposal.contract.strike - underlying_price, 0.0)


def _expiration_outcome(
    proposal: OptionProposal,
    underlying_price: float | None,
) -> str | None:
    intrinsic_value = _intrinsic_value_at_expiration(proposal=proposal, underlying_price=underlying_price)
    if intrinsic_value is None:
        return None
    if intrinsic_value > 0:
        return "in_the_money"
    if underlying_price == proposal.contract.strike:
        return "at_the_money"
    return "out_of_the_money"
