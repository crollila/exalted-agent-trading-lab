import json
from datetime import datetime, timezone

import pytest

from src.agents.hermes_proposal_parser import HermesProposalParser
from src.brokers.order_models import AssetClass, TradeAction
from src.portfolio.portfolio_state import PortfolioState
from src.risk.risk_rules import RiskRules
from src.strategies.hermes_fixtures import (
    CONSERVATIVE_HERMES_FIXTURE_JSON,
    HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
    HermesAggressiveFixtureStrategy,
    HermesConservativeFixtureStrategy,
)


def test_hermes_conservative_fixture_creates_valid_trade_proposals():
    proposals = HermesConservativeFixtureStrategy().generate_proposals(_portfolio())

    assert proposals
    assert all(proposal.strategy_id == "hermes_conservative_fixture" for proposal in proposals)
    assert all(proposal.action == TradeAction.BUY for proposal in proposals)
    assert all(proposal.asset_class == AssetClass.STOCK for proposal in proposals)
    assert all(proposal.target_weight <= RiskRules().max_position_pct for proposal in proposals)
    assert all(proposal.estimated_price > 0 for proposal in proposals)


def test_hermes_aggressive_fixture_creates_valid_trade_proposals():
    proposals = HermesAggressiveFixtureStrategy().generate_proposals(_portfolio())

    assert proposals
    assert all(proposal.strategy_id == "hermes_aggressive_fixture" for proposal in proposals)
    assert all(proposal.action == TradeAction.BUY for proposal in proposals)
    assert all(proposal.asset_class == AssetClass.STOCK for proposal in proposals)
    assert all(proposal.target_weight <= RiskRules().max_position_pct for proposal in proposals)
    assert sum(proposal.target_weight for proposal in proposals if proposal.target_weight is not None) <= 0.30


def test_hermes_fixture_strategy_uses_hermes_parser():
    parser = TrackingHermesProposalParser()
    strategy = HermesConservativeFixtureStrategy(parser=parser)

    proposals = strategy.generate_proposals(_portfolio())

    assert proposals
    assert parser.calls == [
        {
            "raw_json": CONSERVATIVE_HERMES_FIXTURE_JSON,
            "estimated_prices": {"SPY": 500.0, "NVDA": 112.0, "MSFT": 104.0, "AAPL": 98.0},
        }
    ]


def test_invalid_hermes_fixture_payload_is_rejected_safely():
    payload = json.loads(CONSERVATIVE_HERMES_FIXTURE_JSON)
    payload["proposals"][0]["action"] = "sell"
    strategy = HermesConservativeFixtureStrategy(raw_json=json.dumps(payload))

    with pytest.raises(ValueError, match="only buy proposals"):
        strategy.generate_proposals(_portfolio())


class TrackingHermesProposalParser(HermesProposalParser):
    def __init__(self):
        super().__init__(allowed_strategy_ids=(HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,))
        self.calls = []

    def parse(self, raw_json, estimated_prices):
        self.calls.append(
            {
                "raw_json": raw_json,
                "estimated_prices": dict(estimated_prices),
            }
        )
        return super().parse(raw_json=raw_json, estimated_prices=estimated_prices)


def _portfolio():
    return PortfolioState(
        equity=10000.0,
        cash=10000.0,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
