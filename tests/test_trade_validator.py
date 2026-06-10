from datetime import datetime, timezone

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.risk.trade_validator import TradeValidator


def test_daily_turnover_limit():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )

    validator = TradeValidator.default()
    validator.daily_turnover_value = 2900

    proposal = TradeProposal(
        strategy_id="test",
        symbol="AAPL",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.02,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )

    decision = validator.validate(proposal, portfolio)
    assert not decision.approved


def test_cumulative_daily_turnover_limit():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )

    validator = TradeValidator.default()
    decisions = []

    for symbol in ["AAPL", "MSFT", "NVDA", "AMZN"]:
        proposal = TradeProposal(
            strategy_id="test",
            symbol=symbol,
            action=TradeAction.BUY,
            asset_class=AssetClass.STOCK,
            target_weight=0.10,
            estimated_price=100,
            thesis="test",
            confidence=0.5,
        )
        decisions.append(validator.validate(proposal, portfolio))

    assert [decision.approved for decision in decisions] == [True, True, True, False]


def test_daily_turnover_increments_only_for_approved_decisions():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )

    validator = TradeValidator.default()

    approved_proposal = TradeProposal(
        strategy_id="test",
        symbol="AAPL",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.10,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )
    rejected_proposal = TradeProposal(
        strategy_id="test",
        symbol="MSFT",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.40,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )

    approved = validator.validate(approved_proposal, portfolio)
    rejected = validator.validate(rejected_proposal, portfolio)

    assert approved.approved
    assert approved.estimated_trade_value == 1000
    assert not rejected.approved
    assert rejected.estimated_trade_value == 4000
    assert validator.daily_turnover_value == 1000
