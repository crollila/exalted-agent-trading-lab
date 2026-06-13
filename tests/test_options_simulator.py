import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.risk.options_models import OptionProposal, OptionRiskLimits
from src.risk.trade_validator import TradeValidator
from src.simulation.options_simulator import simulate_option_proposal


def test_profitable_long_call_simulation_when_premium_rises():
    proposal = _option_proposal(option_type="call", premium=4.0, contracts=1)

    result = simulate_option_proposal(proposal, local_exit_premium=6.5)

    assert result.simulation_only is True
    assert result.position.premium_paid == pytest.approx(400)
    assert result.position.exit_value == pytest.approx(650)
    assert result.position.realized_profit_loss == pytest.approx(250)


def test_losing_long_call_simulation_when_premium_falls():
    proposal = _option_proposal(option_type="call", premium=4.0, contracts=1)

    result = simulate_option_proposal(proposal, local_exit_premium=1.25)

    assert result.position.exit_value == pytest.approx(125)
    assert result.position.realized_profit_loss == pytest.approx(-275)


def test_profitable_long_put_simulation_when_premium_rises():
    proposal = _option_proposal(option_type="put", premium=3.0, contracts=1)

    result = simulate_option_proposal(
        proposal,
        local_exit_premium=5.25,
        local_underlying_price_at_expiration=485,
    )

    assert result.position.realized_profit_loss == pytest.approx(225)
    assert result.position.intrinsic_value_at_expiration == pytest.approx(15)
    assert result.position.expiration_outcome == "in_the_money"


def test_premium_at_risk_calculation_is_deterministic():
    proposal = _option_proposal(premium=2.5, contracts=3)

    result = simulate_option_proposal(proposal, local_exit_premium=2.5)

    assert result.position.premium_paid == pytest.approx(750)
    assert result.position.max_premium_at_risk == pytest.approx(750)


def test_contract_multiplier_is_applied_correctly():
    proposal = _option_proposal(premium=2.0, contracts=2)

    result = simulate_option_proposal(proposal, local_exit_premium=3.0, contract_multiplier=50)

    assert result.position.premium_paid == pytest.approx(200)
    assert result.position.exit_value == pytest.approx(300)
    assert result.position.realized_profit_loss == pytest.approx(100)


def test_return_on_premium_is_calculated():
    proposal = _option_proposal(premium=2.0, contracts=1)

    result = simulate_option_proposal(proposal, local_exit_premium=3.0)

    assert result.position.return_on_premium == pytest.approx(0.5)


def test_risk_limit_event_is_detected_when_premium_at_risk_exceeds_limit():
    proposal = _option_proposal(premium=4.0, contracts=2)
    limits = OptionRiskLimits(max_premium_at_risk=500)

    result = simulate_option_proposal(proposal, local_exit_premium=5.0, risk_limits=limits)

    assert result.risk_events[0].event_type == "premium_at_risk_limit_exceeded"
    assert result.risk_events[0].premium_at_risk == pytest.approx(800)
    assert result.risk_events[0].limit_value == pytest.approx(500)


def test_invalid_option_proposal_is_rejected_by_model_validation():
    with pytest.raises(ValidationError):
        _option_proposal(premium=0)


def test_simulator_uses_only_local_deterministic_inputs(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    proposal = _option_proposal(premium=1.5, contracts=1)

    result = simulate_option_proposal(proposal, local_exit_premium=2.0)

    assert result.position.realized_profit_loss == pytest.approx(50)
    assert result.risk_events == []


def test_simulator_does_not_require_alpaca_credentials_or_network(tmp_path):
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(tmp_path / "comparison.sqlite3")
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout


def test_existing_compare_strategies_behavior_remains_unchanged(tmp_path):
    result = _run_cli("compare-strategies", database_path=tmp_path / "comparison.sqlite3")

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout
    assert "cash_only" in result.stdout
    assert "spy_buy_hold" in result.stdout
    assert "momentum_v1" in result.stdout


def test_existing_fixture_sweep_behavior_remains_unchanged(tmp_path):
    result = _run_cli("fixture-sweep", database_path=tmp_path / "sweep.sqlite3")

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout
    assert "Overall robust champion:" in result.stdout


def test_export_short_simulation_report_still_works(tmp_path):
    report_path = tmp_path / "reports" / "shorting_simulation_report.md"
    result = _run_cli(
        "export-short-simulation-report",
        "--report-path",
        str(report_path),
        database_path=tmp_path / "short_report.sqlite3",
    )

    assert result.returncode == 0
    assert "simulation only" in result.stdout
    assert report_path.exists()


def test_existing_executable_risk_engine_still_rejects_options():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
    proposal = TradeProposal(
        strategy_id="existing_option_attempt",
        symbol="SPY270115P00500000",
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


def _option_proposal(**overrides):
    payload = {
        "strategy_id": "future_options_sim",
        "contract": {
            "underlying_symbol": "SPY",
            "option_type": "call",
            "expiration": date.today() + timedelta(days=30),
            "strike": 500,
            "open_interest": 2000,
        },
        "action": "buy_to_open",
        "contracts": 1,
        "premium": 4.0,
        "estimated_total_premium": 400,
        "thesis": "Future local-only options simulation.",
        "confidence": 0.7,
        "liquidity_open_interest_assumption": "Open interest assumption for local simulation.",
        "assignment_exercise_risk_note": "Long premium only; expiration handling is simulated locally.",
    }
    contract_overrides = {
        key: overrides.pop(key)
        for key in list(overrides)
        if key in {"option_type", "expiration", "strike", "open_interest"}
    }
    payload["contract"].update(contract_overrides)
    payload.update(overrides)
    return OptionProposal(**payload)


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
