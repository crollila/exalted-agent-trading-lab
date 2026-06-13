from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.risk.shorting_models import ShortProposal


class ShortSimulationRiskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    symbol: str
    trigger_price: float = Field(gt=0.0)
    message: str


class ShortPositionSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    entry_price: float = Field(gt=0.0)
    cover_price: float = Field(gt=0.0)
    quantity: float = Field(gt=0.0)
    opening_short_notional: float = Field(gt=0.0)
    gross_profit_loss_before_fees: float
    borrow_fee_estimate: float = Field(ge=0.0)
    realized_profit_loss: float
    unrealized_profit_loss: float
    forced_cover_triggered: bool


class ShortSimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    strategy_id: str
    simulation_only: bool = True
    position: ShortPositionSimulation
    gross_exposure: float = Field(ge=0.0)
    net_exposure: float
    short_exposure: float = Field(ge=0.0)
    risk_events: list[ShortSimulationRiskEvent]


def simulate_short_proposal(
    proposal: ShortProposal,
    starting_equity: float,
    local_prices: tuple[float, ...],
) -> ShortSimulationResult:
    if starting_equity <= 0:
        raise ValueError("starting_equity must be greater than zero")
    if not local_prices:
        raise ValueError("local_prices must contain at least one deterministic price")
    if any(price <= 0 for price in local_prices):
        raise ValueError("local_prices must all be greater than zero")

    opening_notional = _opening_notional(proposal=proposal, starting_equity=starting_equity)
    quantity = opening_notional / proposal.estimated_price
    cover_price, risk_events = _cover_price_and_events(proposal=proposal, local_prices=local_prices)
    gross_profit_loss = (proposal.estimated_price - cover_price) * quantity
    borrow_fee_estimate = opening_notional * (proposal.borrow_fee_assumption or 0.0)
    realized_profit_loss = gross_profit_loss - borrow_fee_estimate
    exposure = opening_notional / starting_equity

    return ShortSimulationResult(
        proposal_id=proposal.proposal_id,
        strategy_id=proposal.strategy_id,
        position=ShortPositionSimulation(
            symbol=proposal.symbol,
            entry_price=proposal.estimated_price,
            cover_price=cover_price,
            quantity=quantity,
            opening_short_notional=opening_notional,
            gross_profit_loss_before_fees=gross_profit_loss,
            borrow_fee_estimate=borrow_fee_estimate,
            realized_profit_loss=realized_profit_loss,
            unrealized_profit_loss=gross_profit_loss,
            forced_cover_triggered=bool(risk_events),
        ),
        gross_exposure=exposure,
        net_exposure=-exposure,
        short_exposure=exposure,
        risk_events=risk_events,
    )


def _opening_notional(proposal: ShortProposal, starting_equity: float) -> float:
    if proposal.notional_exposure is not None:
        return proposal.notional_exposure
    if proposal.target_short_weight is not None:
        return starting_equity * proposal.target_short_weight
    raise ValueError("target_short_weight or notional_exposure is required")


def _cover_price_and_events(
    proposal: ShortProposal,
    local_prices: tuple[float, ...],
) -> tuple[float, list[ShortSimulationRiskEvent]]:
    threshold = proposal.forced_cover_threshold or proposal.max_loss_exit_price
    if threshold is None:
        return local_prices[-1], []

    for price in local_prices:
        if price >= threshold:
            return price, [
                ShortSimulationRiskEvent(
                    event_type="forced_cover",
                    symbol=proposal.symbol,
                    trigger_price=price,
                    message="Simulation-only forced cover threshold was reached.",
                )
            ]

    return local_prices[-1], []

