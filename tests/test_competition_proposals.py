import pytest
from pydantic import ValidationError

from competition_helpers import option_long_call, stock_long, stock_short
from src.competition.proposals import CompetitionProposal, ProposalType


def test_stock_long_requires_target_weight():
    with pytest.raises(ValidationError):
        stock_long(target_weight=None)


def test_short_requires_borrow_and_stop_and_max_loss():
    with pytest.raises(ValidationError):
        stock_short(borrow_availability_assumption=None)
    with pytest.raises(ValidationError):
        stock_short(stop_level=None)
    with pytest.raises(ValidationError):
        stock_short(max_loss_estimate=None)


def test_short_requires_exposure_impact():
    with pytest.raises(ValidationError):
        stock_short(gross_exposure_impact=None)


def test_option_requires_assignment_note_and_legs():
    with pytest.raises(ValidationError):
        option_long_call(assignment_exercise_risk_note="   ")
    with pytest.raises(ValidationError):
        option_long_call(legs=[])


def test_option_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CompetitionProposal(
            team_id="t",
            agent_id="a",
            strategy_id="s",
            proposal_type=ProposalType.STOCK_LONG,
            symbol="SPY",
            action="open_long",
            thesis="x",
            confidence=0.5,
            estimated_price=1.0,
            target_weight=0.1,
            max_loss_thesis="x",
            invalidation_condition="x",
            expected_catalyst="x",
            risk_notes="x",
            data_sources=["s"],
            data_provenance="fixture",
            intended_holding_period="x",
            unexpected_field="boom",
        )


def test_helpers_classify_types():
    assert stock_short().is_short is True
    assert option_long_call().is_option is True
    assert stock_long().is_short is False


def test_computed_premium_at_risk_is_deterministic():
    proposal = option_long_call(contracts=2, net_premium_per_contract=3.0)
    assert proposal.computed_premium_at_risk() == 3.0 * 2 * 100
