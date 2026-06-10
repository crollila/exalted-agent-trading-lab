from datetime import datetime, timezone

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState, Position
from src.risk.trade_validator import TradeValidator


def portfolio(cash=10000, equity=10000, positions=None):
    return PortfolioState(
        equity=equity,
        cash=cash,
        positions=positions or {},
        timestamp=datetime.now(timezone.utc),
    )


def test_rejects_options():
    proposal = TradeProposal(
        strategy_id="test",
        symbol="SPY250101C00500000",
        action=TradeAction.BUY,
        asset_class=AssetClass.OPTION,
        quantity=1,
        estimated_price=10,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio())
    assert not decision.approved


def test_rejects_non_stock_trades():
    proposal = TradeProposal(
        strategy_id="test",
        symbol="BTCUSD",
        action=TradeAction.BUY,
        asset_class=AssetClass.CRYPTO,
        quantity=1,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio())
    assert not decision.approved


def test_rejects_position_above_twenty_percent():
    proposal = TradeProposal(
        strategy_id="test",
        symbol="NVDA",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.30,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio())
    assert not decision.approved


def test_rejects_low_cash():
    proposal = TradeProposal(
        strategy_id="test",
        symbol="NVDA",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.95,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio())
    assert not decision.approved


def test_rejects_short_sell():
    proposal = TradeProposal(
        strategy_id="test",
        symbol="TSLA",
        action=TradeAction.SELL,
        asset_class=AssetClass.STOCK,
        quantity=10,
        estimated_price=200,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio())
    assert not decision.approved


def test_rejects_sell_orders_that_exceed_current_position():
    current_positions = {
        "TSLA": Position(
            symbol="TSLA",
            quantity=5,
            market_value=1000,
            average_entry_price=200,
        )
    }
    proposal = TradeProposal(
        strategy_id="test",
        symbol="TSLA",
        action=TradeAction.SELL,
        asset_class=AssetClass.STOCK,
        quantity=6,
        estimated_price=200,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio(positions=current_positions))
    assert not decision.approved


def test_rejects_more_than_five_new_positions_per_day():
    validator = TradeValidator.default()
    account = portfolio()
    decisions = []

    for index in range(6):
        proposal = TradeProposal(
            strategy_id="test",
            symbol=f"STK{index}",
            action=TradeAction.BUY,
            asset_class=AssetClass.STOCK,
            target_weight=0.01,
            estimated_price=100,
            thesis="test",
            confidence=0.5,
        )
        decisions.append(validator.validate(proposal, account))

    assert [decision.approved for decision in decisions] == [True, True, True, True, True, False]


def test_approves_reasonable_buy():
    proposal = TradeProposal(
        strategy_id="test",
        symbol="MSFT",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.10,
        estimated_price=100,
        thesis="test",
        confidence=0.5,
    )

    decision = TradeValidator.default().validate(proposal, portfolio())
    assert decision.approved
    assert decision.approved_quantity == 10
    assert decision.approved_trade_value == 1000
