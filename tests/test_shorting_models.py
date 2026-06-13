import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.risk.shorting_models import ShortAction, ShortProposal, ShortRiskDecision, ShortRiskLimits
from src.risk.trade_validator import TradeValidator


def test_valid_short_proposal_model_works():
    proposal = ShortProposal(
        strategy_id="future_short_research",
        symbol=" aapl ",
        action=ShortAction.SELL_SHORT,
        asset_class=AssetClass.STOCK,
        target_short_weight=0.05,
        estimated_price=100,
        thesis="Future paper-short thesis for a weakening stock.",
        confidence=0.7,
        borrow_available_assumption=True,
        borrow_fee_assumption=0.02,
        max_loss_exit_price=115,
        forced_cover_threshold=120,
    )

    assert proposal.symbol == "AAPL"
    assert proposal.action == ShortAction.SELL_SHORT
    assert proposal.target_short_weight == 0.05
    assert proposal.borrow_available_assumption is True


def test_valid_short_risk_limit_and_decision_models_work():
    limits = ShortRiskLimits(
        shorting_permission_enabled=False,
        max_short_exposure=0.10,
        max_gross_exposure=1.0,
        max_net_exposure=0.5,
        max_loss_per_short_position=0.02,
    )
    decision = ShortRiskDecision(
        proposal_id="proposal-1",
        approved=False,
        reasons=["Rejected: shorting is disabled."],
        estimated_short_exposure=0.0,
    )

    assert limits.shorting_permission_enabled is False
    assert limits.require_borrow_available_assumption is True
    assert decision.approved is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asset_class", AssetClass.OPTION),
        ("symbol", ""),
        ("thesis", " "),
        ("confidence", 1.1),
        ("confidence", -0.1),
        ("estimated_price", 0),
        ("target_short_weight", 0),
        ("target_short_weight", 1.1),
        ("action", "sell"),
    ],
)
def test_invalid_short_proposal_fields_are_rejected(field, value):
    payload = _valid_short_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ShortProposal(**payload)


def test_short_proposal_requires_explicit_borrow_assumption():
    payload = _valid_short_payload()
    payload.pop("borrow_available_assumption")

    with pytest.raises(ValidationError):
        ShortProposal(**payload)


def test_short_proposal_requires_short_exposure_input():
    payload = _valid_short_payload()
    payload.pop("target_short_weight")

    with pytest.raises(ValidationError, match="target_short_weight or notional_exposure is required"):
        ShortProposal(**payload)


def test_short_proposal_rejects_extra_fields():
    payload = _valid_short_payload()
    payload["broker_order_id"] = "must-not-exist"

    with pytest.raises(ValidationError):
        ShortProposal(**payload)


def test_current_trade_proposal_behavior_is_unchanged():
    proposal = TradeProposal(
        strategy_id="existing_long_strategy",
        symbol="AAPL",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        target_weight=0.10,
        estimated_price=100,
        thesis="Existing long-only proposal still works.",
        confidence=0.8,
    )

    assert proposal.action == TradeAction.BUY
    assert proposal.asset_class == AssetClass.STOCK


def test_current_risk_engine_still_rejects_shorting_in_executable_flow():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
    proposal = TradeProposal(
        strategy_id="existing_sell_attempt",
        symbol="AAPL",
        action=TradeAction.SELL,
        asset_class=AssetClass.STOCK,
        quantity=1,
        estimated_price=100,
        thesis="Executable flow still rejects shorting.",
        confidence=0.8,
    )

    decision = TradeValidator.default().validate(proposal, portfolio)

    assert decision.approved is False
    assert "Rejected: sell quantity exceeds current position and shorting is disabled." in decision.reasons


def test_compare_strategies_behavior_is_unchanged(tmp_path):
    database_path = tmp_path / "comparison.sqlite3"
    result = _run_cli(tmp_path, "compare-strategies", database_path=database_path)

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout
    with sqlite3.connect(database_path) as conn:
        strategy_ids = [
            row[0]
            for row in conn.execute("SELECT strategy_id FROM runs ORDER BY started_at ASC, id ASC").fetchall()
        ]
    assert strategy_ids == ["cash_only", "spy_buy_hold", "momentum_v1"]


def test_fixture_sweep_behavior_is_unchanged(tmp_path):
    result = _run_cli(tmp_path, "fixture-sweep", database_path=tmp_path / "sweep.sqlite3")

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout
    assert "Overall robust champion:" in result.stdout
    assert "momentum_crash" in result.stdout


def _valid_short_payload():
    return {
        "strategy_id": "future_short_research",
        "symbol": "AAPL",
        "action": "sell_short",
        "asset_class": "stock",
        "target_short_weight": 0.05,
        "estimated_price": 100,
        "thesis": "Future paper-short thesis.",
        "confidence": 0.7,
        "borrow_available_assumption": True,
        "max_loss_exit_price": 115,
    }


def _run_cli(tmp_path, *args, database_path):
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
