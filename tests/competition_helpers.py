"""Shared factories for competition tests (not a test module)."""

from __future__ import annotations

from datetime import date, timedelta

from src.competition.proposals import (
    CompetitionProposal,
    DataProvenance,
    LegSide,
    OptionLeg,
    OptionType,
    ProposalType,
)

_COMMON = {
    "team_id": "team_alpha",
    "agent_id": "agent_1",
    "strategy_id": "strat_1",
    "intended_holding_period": "swing",
    "expected_catalyst": "demo",
    "data_sources": ["local_runtime_history"],
    "data_provenance": DataProvenance.FIXTURE,
}


def stock_long(**overrides) -> CompetitionProposal:
    values = dict(
        proposal_type=ProposalType.STOCK_LONG,
        symbol="SPY",
        action="open_long",
        thesis="long thesis",
        confidence=0.6,
        estimated_price=500.0,
        target_weight=0.10,
        max_loss_thesis="capped at position",
        invalidation_condition="breaks support",
        risk_notes="standard",
        **_COMMON,
    )
    values.update(overrides)
    return CompetitionProposal(**values)


def stock_short(**overrides) -> CompetitionProposal:
    values = dict(
        proposal_type=ProposalType.STOCK_SHORT,
        symbol="XYZ",
        action="open_short",
        thesis="short thesis",
        confidence=0.6,
        estimated_price=50.0,
        target_weight=0.05,
        max_loss_thesis="capped via stop",
        invalidation_condition="reclaims level",
        risk_notes="squeeze risk",
        max_loss_estimate=500.0,
        stop_level=55.0,
        borrow_availability_assumption="assumed_available",
        gross_exposure_impact=0.05,
        net_exposure_impact=-0.05,
        **_COMMON,
    )
    values.update(overrides)
    return CompetitionProposal(**values)


def margin_long(**overrides) -> CompetitionProposal:
    values = dict(
        proposal_type=ProposalType.MARGIN_STOCK_LONG,
        symbol="AAPL",
        action="open_long_margin",
        thesis="margin long thesis",
        confidence=0.6,
        estimated_price=200.0,
        target_weight=0.15,
        max_loss_thesis="leverage risk",
        invalidation_condition="breaks support",
        risk_notes="leverage",
        gross_exposure_impact=0.15,
        net_exposure_impact=0.15,
        **_COMMON,
    )
    values.update(overrides)
    return CompetitionProposal(**values)


def option_long_call(**overrides) -> CompetitionProposal:
    expiry = overrides.pop("expiration", date.today() + timedelta(days=30))
    leg = OptionLeg(side=LegSide.LONG, option_type=OptionType.CALL, strike=510.0, expiration=expiry, estimated_premium=4.0)
    values = dict(
        proposal_type=ProposalType.OPTION_LONG_CALL,
        symbol="SPY",
        underlying="SPY",
        action="buy_to_open",
        thesis="defined risk call",
        confidence=0.5,
        estimated_price=500.0,
        max_loss_thesis="premium only",
        invalidation_condition="thesis fails",
        risk_notes="theta decay",
        expiration=expiry,
        contracts=1,
        net_premium_per_contract=4.0,
        max_premium_at_risk=400.0,
        max_loss=400.0,
        assignment_exercise_risk_note="long call: no assignment obligation",
        legs=[leg],
        **_COMMON,
    )
    values.update(overrides)
    return CompetitionProposal(**values)


def option_debit_spread(**overrides) -> CompetitionProposal:
    expiry = overrides.pop("expiration", date.today() + timedelta(days=30))
    legs = [
        OptionLeg(side=LegSide.LONG, option_type=OptionType.CALL, strike=500.0, expiration=expiry, estimated_premium=8.0),
        OptionLeg(side=LegSide.SHORT, option_type=OptionType.CALL, strike=510.0, expiration=expiry, estimated_premium=4.0),
    ]
    values = dict(
        proposal_type=ProposalType.OPTION_DEBIT_SPREAD,
        symbol="SPY",
        underlying="SPY",
        action="buy_to_open_spread",
        thesis="defined risk debit spread",
        confidence=0.55,
        estimated_price=500.0,
        max_loss_thesis="net debit",
        invalidation_condition="thesis fails",
        risk_notes="defined risk",
        expiration=expiry,
        contracts=1,
        net_premium_per_contract=4.0,
        max_premium_at_risk=400.0,
        max_loss=400.0,
        max_profit=600.0,
        spread_width=10.0,
        assignment_exercise_risk_note="short leg covered by long leg",
        legs=legs,
        **_COMMON,
    )
    values.update(overrides)
    return CompetitionProposal(**values)


def naked_short_call(**overrides) -> CompetitionProposal:
    expiry = overrides.pop("expiration", date.today() + timedelta(days=30))
    leg = OptionLeg(side=LegSide.SHORT, option_type=OptionType.CALL, strike=510.0, expiration=expiry, estimated_premium=4.0)
    values = dict(
        proposal_type=ProposalType.OPTION_DEFINED_RISK_SPREAD,
        symbol="SPY",
        underlying="SPY",
        action="sell_to_open",
        thesis="naked call (should be rejected)",
        confidence=0.5,
        estimated_price=500.0,
        max_loss_thesis="undefined",
        invalidation_condition="thesis fails",
        risk_notes="uncovered",
        expiration=expiry,
        contracts=1,
        net_premium_per_contract=4.0,
        max_premium_at_risk=400.0,
        max_loss=400.0,
        assignment_exercise_risk_note="assignment risk present",
        legs=[
            leg,
            OptionLeg(side=LegSide.LONG, option_type=OptionType.PUT, strike=400.0, expiration=expiry, estimated_premium=1.0),
        ],
        **_COMMON,
    )
    values.update(overrides)
    return CompetitionProposal(**values)
