from datetime import datetime, timezone

import pytest

from src.brokers.order_models import AssetClass, TradeAction
from src.portfolio.portfolio_state import PortfolioState
from src.strategies.momentum_v1 import MomentumV1Strategy


def test_positive_momentum_produces_buy_proposal():
    strategy = MomentumV1Strategy(price_history={"MSFT": [100, 110]})
    proposals = strategy.generate_proposals(_empty_portfolio())

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.strategy_id == "momentum_v1"
    assert proposal.symbol == "MSFT"
    assert proposal.action == TradeAction.BUY
    assert proposal.asset_class == AssetClass.STOCK
    assert proposal.target_weight == 0.10
    assert proposal.estimated_price == 110
    assert "10.00%" in proposal.thesis
    assert proposal.confidence == pytest.approx(0.60)


def test_negative_or_flat_momentum_produces_no_proposals():
    strategy = MomentumV1Strategy(
        price_history={
            "AAPL": [100, 100],
            "TSLA": [100, 95],
        }
    )

    proposals = strategy.generate_proposals(_empty_portfolio())

    assert proposals == []


def test_non_stock_assets_are_not_proposed():
    strategy = MomentumV1Strategy(
        price_history={
            "BTCUSD": [100, 120],
            "MSFT": [100, 105],
        },
        asset_classes={
            "BTCUSD": AssetClass.CRYPTO,
            "MSFT": AssetClass.STOCK,
        },
    )

    proposals = strategy.generate_proposals(_empty_portfolio())

    assert [proposal.symbol for proposal in proposals] == ["MSFT"]
    assert all(proposal.asset_class == AssetClass.STOCK for proposal in proposals)


def test_target_weights_are_risk_policy_compatible():
    strategy = MomentumV1Strategy(
        price_history={
            "NVDA": [100, 115],
            "MSFT": [100, 110],
        },
        target_weight=0.20,
    )

    proposals = strategy.generate_proposals(_empty_portfolio())

    assert proposals
    assert all(proposal.target_weight <= 0.20 for proposal in proposals)


def test_target_weight_above_risk_policy_limit_is_rejected():
    with pytest.raises(ValueError, match="no more than 0.20"):
        MomentumV1Strategy(price_history={"NVDA": [100, 115]}, target_weight=0.21)


def test_output_ordering_is_deterministic():
    strategy = MomentumV1Strategy(
        price_history={
            "MSFT": [100, 110],
            "AAPL": [100, 110],
            "NVDA": [100, 120],
        }
    )

    proposals = strategy.generate_proposals(_empty_portfolio())

    assert [proposal.symbol for proposal in proposals] == ["NVDA", "AAPL", "MSFT"]


def _empty_portfolio() -> PortfolioState:
    return PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
