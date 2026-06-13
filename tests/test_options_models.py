import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.risk.options_models import (
    OptionAction,
    OptionContract,
    OptionProposal,
    OptionRiskLimits,
    OptionType,
    check_option_risk,
)
from src.risk.trade_validator import TradeValidator


def test_valid_option_proposal_model_works():
    proposal = OptionProposal(**_valid_option_payload())

    assert proposal.strategy_id == "future_options_research"
    assert proposal.contract.underlying_symbol == "SPY"
    assert proposal.contract.option_type == OptionType.CALL
    assert proposal.action == OptionAction.BUY_TO_OPEN
    assert proposal.contract.delta == 0.55
    assert proposal.contract.open_interest == 2500


def test_invalid_option_type_is_rejected():
    payload = _valid_option_payload()
    payload["contract"]["option_type"] = "straddle"

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


@pytest.mark.parametrize("action", ["sell_to_open", "sell_to_close"])
def test_sell_to_open_and_naked_short_actions_are_rejected(action):
    payload = _valid_option_payload()
    payload["action"] = action

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


def test_zero_dte_is_rejected():
    payload = _valid_option_payload()
    payload["contract"]["expiration"] = date.today()

    with pytest.raises(ValidationError, match="0DTE options are disabled"):
        OptionProposal(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strike", 0),
        ("contracts", 0),
        ("premium", 0),
        ("estimated_total_premium", 0),
    ],
)
def test_invalid_strike_contracts_and_premium_are_rejected(field, value):
    payload = _valid_option_payload()
    if field == "strike":
        payload["contract"][field] = value
    else:
        payload[field] = value

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


def test_missing_thesis_is_rejected():
    payload = _valid_option_payload()
    payload["thesis"] = " "

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


def test_missing_assignment_exercise_risk_note_is_rejected():
    payload = _valid_option_payload()
    payload["assignment_exercise_risk_note"] = ""

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_invalid_confidence_is_rejected(confidence):
    payload = _valid_option_payload()
    payload["confidence"] = confidence

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


def test_option_models_reject_extra_fields():
    payload = _valid_option_payload()
    payload["broker_order_id"] = "must-not-exist"

    with pytest.raises(ValidationError):
        OptionProposal(**payload)


def test_excessive_contracts_and_premium_are_rejected_by_option_risk_check():
    payload = _valid_option_payload()
    payload["contracts"] = 2
    payload["estimated_total_premium"] = 1200
    proposal = OptionProposal(**payload)
    limits = OptionRiskLimits(
        options_permission_enabled=True,
        max_contracts_per_trade=1,
        max_premium_at_risk=1000,
    )

    decision = check_option_risk(proposal, limits)

    assert decision.approved is False
    assert "Rejected: option contract count exceeds max contracts per trade." in decision.reasons
    assert "Rejected: option premium at risk exceeds max premium at risk." in decision.reasons


def test_default_option_risk_limits_keep_options_execution_disabled():
    limits = OptionRiskLimits()

    assert limits.options_permission_enabled is False
    assert limits.no_zero_dte is True
    assert limits.allow_naked_short_options is False
    assert limits.live_options_enabled is False
    assert limits.broker_option_execution_enabled is False


def test_current_trade_proposal_behavior_is_unchanged():
    proposal = TradeProposal(
        strategy_id="existing_long_strategy",
        symbol="AAPL",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.10,
        estimated_price=100,
        thesis="Existing stock-only proposal still works.",
        confidence=0.8,
    )

    assert proposal.action == TradeAction.BUY
    assert proposal.asset_class == AssetClass.STOCK


def test_current_risk_engine_still_rejects_options_in_executable_flow():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
    proposal = TradeProposal(
        strategy_id="existing_option_attempt",
        symbol="SPY270115C00500000",
        action=TradeAction.BUY,
        asset_class=AssetClass.OPTION,
        quantity=1,
        estimated_price=10,
        thesis="Executable flow still rejects options.",
        confidence=0.8,
    )

    decision = TradeValidator.default().validate(proposal, portfolio)

    assert decision.approved is False
    assert "Rejected: only stock trades are allowed." in decision.reasons
    assert "Rejected: options are disabled." in decision.reasons


def test_compare_strategies_behavior_is_unchanged(tmp_path):
    database_path = tmp_path / "comparison.sqlite3"
    result = _run_cli("compare-strategies", database_path=database_path)

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout
    with sqlite3.connect(database_path) as conn:
        strategy_ids = [
            row[0]
            for row in conn.execute("SELECT strategy_id FROM runs ORDER BY started_at ASC, id ASC").fetchall()
        ]
    assert strategy_ids == ["cash_only", "spy_buy_hold", "momentum_v1"]


def test_fixture_sweep_behavior_is_unchanged(tmp_path):
    result = _run_cli("fixture-sweep", database_path=tmp_path / "sweep.sqlite3")

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout
    assert "Overall robust champion:" in result.stdout


def _valid_option_payload():
    return {
        "strategy_id": "future_options_research",
        "contract": {
            "underlying_symbol": " spy ",
            "option_type": "call",
            "expiration": date.today() + timedelta(days=30),
            "strike": 500,
            "open_interest": 2500,
            "delta": 0.55,
            "gamma": 0.02,
            "theta": -0.04,
            "vega": 0.12,
        },
        "action": "buy_to_open",
        "contracts": 1,
        "premium": 4.25,
        "estimated_total_premium": 425,
        "thesis": "Future paper-options thesis for a defined-risk call.",
        "confidence": 0.65,
        "liquidity_open_interest_assumption": "Open interest appears sufficient for future simulation.",
        "assignment_exercise_risk_note": "Long options can expire worthless; exercise/assignment handling is not enabled.",
    }


def _run_cli(*args, database_path):
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "src.main", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
