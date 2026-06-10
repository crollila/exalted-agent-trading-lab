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
