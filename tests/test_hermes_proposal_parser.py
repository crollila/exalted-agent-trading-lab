import json

import pytest

from src.agents.hermes_proposal_parser import (
    HERMES_WEALTH_ADVISOR_STRATEGY_ID,
    HermesProposalParser,
)
from src.brokers.order_models import AssetClass, TradeAction


def test_valid_hermes_json_converts_to_trade_proposals():
    result = HermesProposalParser().parse(
        raw_json=json.dumps(_payload()),
        estimated_prices={"MSFT": 420.50},
    )

    assert result.ok
    assert result.errors == []
    assert result.strategy_id == HERMES_WEALTH_ADVISOR_STRATEGY_ID
    assert result.portfolio_notes == "Maintain cash reserve."
    assert len(result.proposals) == 1

    proposal = result.proposals[0]
    assert proposal.strategy_id == HERMES_WEALTH_ADVISOR_STRATEGY_ID
    assert proposal.symbol == "MSFT"
    assert proposal.action == TradeAction.BUY
    assert proposal.asset_class == AssetClass.STOCK
    assert proposal.target_weight == 0.08
    assert proposal.estimated_price == 420.50
    assert proposal.thesis == "Positive momentum and strong balance sheet."
    assert proposal.confidence == 0.72


def test_invalid_json_fails_safely_without_traceback():
    result = HermesProposalParser().parse(
        raw_json="{not json",
        estimated_prices={"MSFT": 420.50},
    )

    assert not result.ok
    assert result.proposals == []
    assert result.errors
    assert "Invalid JSON" in result.errors[0]


def test_missing_fields_are_rejected():
    payload = _payload()
    del payload["proposals"][0]["thesis"]

    result = HermesProposalParser().parse(
        raw_json=json.dumps(payload),
        estimated_prices={"MSFT": 420.50},
    )

    assert _rejected(result)


def test_empty_symbol_is_rejected():
    result = _parse_with_override(symbol=" ")

    assert _rejected(result)


def test_non_buy_action_is_rejected_for_now():
    result = _parse_with_override(action="sell")

    assert _rejected(result)
    assert any("only buy proposals" in error for error in result.errors)


def test_non_stock_asset_class_is_rejected():
    result = _parse_with_override(asset_class="crypto")

    assert _rejected(result)
    assert any("only stock proposals" in error for error in result.errors)


def test_options_are_rejected():
    result = _parse_with_override(asset_class="option")

    assert _rejected(result)
    assert any("options are disabled" in error for error in result.errors)


@pytest.mark.parametrize("target_weight", [0, -0.01, 0.21])
def test_target_weight_must_be_positive_and_within_risk_policy(target_weight):
    result = _parse_with_override(target_weight=target_weight)

    assert _rejected(result)


def test_empty_thesis_is_rejected():
    result = _parse_with_override(thesis=" ")

    assert _rejected(result)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_must_be_between_zero_and_one(confidence):
    result = _parse_with_override(confidence=confidence)

    assert _rejected(result)


def test_extra_fields_are_rejected():
    proposal = _payload()["proposals"][0]
    proposal["order_now"] = True

    result = HermesProposalParser().parse(
        raw_json=json.dumps(
            {
                "strategy_id": HERMES_WEALTH_ADVISOR_STRATEGY_ID,
                "proposals": [proposal],
                "portfolio_notes": "Maintain cash reserve.",
            }
        ),
        estimated_prices={"MSFT": 420.50},
    )

    assert _rejected(result)


def test_missing_local_estimated_price_is_rejected():
    result = HermesProposalParser().parse(
        raw_json=json.dumps(_payload()),
        estimated_prices={},
    )

    assert _rejected(result)
    assert any("missing local estimated price" in error for error in result.errors)


def _parse_with_override(**proposal_fields):
    payload = _payload()
    payload["proposals"][0].update(proposal_fields)
    return HermesProposalParser().parse(
        raw_json=json.dumps(payload),
        estimated_prices={"MSFT": 420.50},
    )


def _payload():
    return {
        "strategy_id": HERMES_WEALTH_ADVISOR_STRATEGY_ID,
        "proposals": [
            {
                "symbol": "MSFT",
                "action": "buy",
                "asset_class": "stock",
                "target_weight": 0.08,
                "thesis": "Positive momentum and strong balance sheet.",
                "confidence": 0.72,
            }
        ],
        "portfolio_notes": "Maintain cash reserve.",
    }


def _rejected(result):
    return not result.ok and result.proposals == [] and result.errors
