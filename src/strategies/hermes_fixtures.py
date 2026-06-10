from __future__ import annotations

import json
from typing import Mapping

from src.agents.hermes_proposal_parser import HermesProposalParser
from src.brokers.order_models import TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.strategies.base import Strategy


HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID = "hermes_conservative_fixture"
HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID = "hermes_aggressive_fixture"

LOCAL_HERMES_FIXTURE_PRICES: dict[str, float] = {
    "SPY": 500.0,
    "NVDA": 112.0,
    "MSFT": 104.0,
    "AAPL": 98.0,
}

CONSERVATIVE_HERMES_FIXTURE_JSON = json.dumps(
    {
        "strategy_id": HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
        "proposals": [
            {
                "symbol": "MSFT",
                "action": "buy",
                "asset_class": "stock",
                "target_weight": 0.06,
                "thesis": "Local fixture: modest exposure to a stable positive-momentum stock.",
                "confidence": 0.66,
            },
            {
                "symbol": "SPY",
                "action": "buy",
                "asset_class": "stock",
                "target_weight": 0.04,
                "thesis": "Local fixture: small diversified benchmark exposure while preserving cash.",
                "confidence": 0.62,
            },
        ],
        "portfolio_notes": "Conservative local fixture with low target weights and no runtime calls.",
    }
)

AGGRESSIVE_HERMES_FIXTURE_JSON = json.dumps(
    {
        "strategy_id": HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID,
        "proposals": [
            {
                "symbol": "NVDA",
                "action": "buy",
                "asset_class": "stock",
                "target_weight": 0.14,
                "thesis": "Local fixture: higher-conviction momentum exposure within policy limits.",
                "confidence": 0.76,
            },
            {
                "symbol": "MSFT",
                "action": "buy",
                "asset_class": "stock",
                "target_weight": 0.10,
                "thesis": "Local fixture: secondary quality momentum exposure within policy limits.",
                "confidence": 0.70,
            },
            {
                "symbol": "SPY",
                "action": "buy",
                "asset_class": "stock",
                "target_weight": 0.05,
                "thesis": "Local fixture: benchmark ballast while keeping daily turnover below policy limits.",
                "confidence": 0.64,
            },
        ],
        "portfolio_notes": "Aggressive local fixture with higher weights but no runtime calls.",
    }
)


class HermesFixtureStrategy(Strategy):
    name: str
    strategy_id: str

    def __init__(
        self,
        strategy_id: str,
        name: str,
        raw_json: str,
        estimated_prices: Mapping[str, float] | None = None,
        parser: HermesProposalParser | None = None,
    ):
        self.strategy_id = strategy_id
        self.name = name
        self.raw_json = raw_json
        self.estimated_prices = estimated_prices or LOCAL_HERMES_FIXTURE_PRICES
        self.parser = parser or HermesProposalParser(allowed_strategy_ids=(strategy_id,))

    def generate_proposals(self, portfolio: PortfolioState) -> list[TradeProposal]:
        result = self.parser.parse(
            raw_json=self.raw_json,
            estimated_prices=self.estimated_prices,
        )
        if not result.ok:
            joined_errors = "; ".join(result.errors)
            raise ValueError(f"Invalid Hermes fixture payload for {self.strategy_id}: {joined_errors}")
        return result.proposals


class HermesConservativeFixtureStrategy(HermesFixtureStrategy):
    def __init__(
        self,
        raw_json: str = CONSERVATIVE_HERMES_FIXTURE_JSON,
        estimated_prices: Mapping[str, float] | None = None,
        parser: HermesProposalParser | None = None,
    ):
        super().__init__(
            strategy_id=HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
            name="Hermes Conservative Fixture",
            raw_json=raw_json,
            estimated_prices=estimated_prices,
            parser=parser,
        )


class HermesAggressiveFixtureStrategy(HermesFixtureStrategy):
    def __init__(
        self,
        raw_json: str = AGGRESSIVE_HERMES_FIXTURE_JSON,
        estimated_prices: Mapping[str, float] | None = None,
        parser: HermesProposalParser | None = None,
    ):
        super().__init__(
            strategy_id=HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID,
            name="Hermes Aggressive Fixture",
            raw_json=raw_json,
            estimated_prices=estimated_prices,
            parser=parser,
        )
