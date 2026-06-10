import pytest

from pydantic import ValidationError

from src.brokers.order_models import TradeAction, TradeProposal
from src.risk.position_sizing import dollars_for_target_weight, estimate_trade_value, shares_for_dollars


def test_dollars_for_target_weight():
    assert dollars_for_target_weight(10000, 0.2) == 2000


def test_shares_for_dollars():
    assert shares_for_dollars(1000, 100) == 10


def test_estimate_trade_value():
    assert estimate_trade_value(5, 20) == 100


def test_invalid_price_rejected():
    with pytest.raises(ValueError):
        shares_for_dollars(1000, 0)


def test_trade_proposal_requires_positive_estimated_price():
    with pytest.raises(ValidationError):
        TradeProposal(
            strategy_id="test",
            symbol="SPY",
            action=TradeAction.BUY,
            quantity=1,
            estimated_price=0,
            thesis="test",
            confidence=0.5,
        )
