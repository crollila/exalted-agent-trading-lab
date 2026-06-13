import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.portfolio.portfolio_state import PortfolioState
from src.risk.shorting_models import ShortProposal
from src.risk.trade_validator import TradeValidator
from src.simulation.shorting_simulator import simulate_short_proposal


def test_profitable_short_simulation_when_price_falls():
    proposal = _short_proposal(estimated_price=100, target_short_weight=0.10)

    result = simulate_short_proposal(
        proposal=proposal,
        starting_equity=10000,
        local_prices=(95, 90),
    )

    assert result.simulation_only is True
    assert result.position.opening_short_notional == pytest.approx(1000)
    assert result.position.quantity == pytest.approx(10)
    assert result.position.cover_price == 90
    assert result.position.gross_profit_loss_before_fees == pytest.approx(100)
    assert result.position.realized_profit_loss == pytest.approx(100)
    assert result.position.unrealized_profit_loss == pytest.approx(100)


def test_losing_short_simulation_when_price_rises():
    proposal = _short_proposal(estimated_price=100, target_short_weight=0.10)

    result = simulate_short_proposal(
        proposal=proposal,
        starting_equity=10000,
        local_prices=(103, 108),
    )

    assert result.position.cover_price == 108
    assert result.position.gross_profit_loss_before_fees == pytest.approx(-80)
    assert result.position.realized_profit_loss == pytest.approx(-80)


def test_forced_cover_trigger_is_detected():
    proposal = _short_proposal(
        estimated_price=100,
        target_short_weight=0.10,
        forced_cover_threshold=112,
    )

    result = simulate_short_proposal(
        proposal=proposal,
        starting_equity=10000,
        local_prices=(105, 113, 90),
    )

    assert result.position.forced_cover_triggered is True
    assert result.position.cover_price == 113
    assert result.risk_events[0].event_type == "forced_cover"
    assert result.risk_events[0].trigger_price == 113


def test_borrow_fee_estimate_affects_result():
    proposal = _short_proposal(
        estimated_price=100,
        target_short_weight=0.10,
        borrow_fee_assumption=0.02,
    )

    result = simulate_short_proposal(
        proposal=proposal,
        starting_equity=10000,
        local_prices=(90,),
    )

    assert result.position.gross_profit_loss_before_fees == pytest.approx(100)
    assert result.position.borrow_fee_estimate == pytest.approx(20)
    assert result.position.realized_profit_loss == pytest.approx(80)


def test_exposures_are_deterministic():
    proposal = _short_proposal(estimated_price=50, notional_exposure=1500)

    result = simulate_short_proposal(
        proposal=proposal,
        starting_equity=10000,
        local_prices=(45,),
    )

    assert result.position.opening_short_notional == pytest.approx(1500)
    assert result.position.quantity == pytest.approx(30)
    assert result.gross_exposure == pytest.approx(0.15)
    assert result.net_exposure == pytest.approx(-0.15)
    assert result.short_exposure == pytest.approx(0.15)


def test_invalid_short_proposal_is_rejected_by_model_validation():
    with pytest.raises(ValidationError):
        _short_proposal(estimated_price=0)


def test_simulator_requires_only_local_deterministic_inputs(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    proposal = _short_proposal(estimated_price=100, target_short_weight=0.10)

    result = simulate_short_proposal(
        proposal=proposal,
        starting_equity=10000,
        local_prices=(99, 98),
    )

    assert result.simulation_only is True
    assert result.position.cover_price == 98


@pytest.mark.parametrize("local_prices", [(), (100, 0)])
def test_simulator_rejects_missing_or_invalid_local_prices(local_prices):
    proposal = _short_proposal(estimated_price=100, target_short_weight=0.10)

    with pytest.raises(ValueError):
        simulate_short_proposal(
            proposal=proposal,
            starting_equity=10000,
            local_prices=local_prices,
        )


def test_compare_strategies_behavior_remains_unchanged(tmp_path):
    database_path = tmp_path / "comparison.sqlite3"
    result = _run_cli("compare-strategies", database_path=database_path)

    assert result.returncode == 0
    with sqlite3.connect(database_path) as conn:
        strategy_ids = [
            row[0]
            for row in conn.execute("SELECT strategy_id FROM runs ORDER BY started_at ASC, id ASC").fetchall()
        ]
    assert strategy_ids == ["cash_only", "spy_buy_hold", "momentum_v1"]


def test_fixture_sweep_behavior_remains_unchanged(tmp_path):
    result = _run_cli("fixture-sweep", database_path=tmp_path / "sweep.sqlite3")

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout
    assert "Overall robust champion:" in result.stdout


def test_executable_risk_engine_still_rejects_shorting():
    portfolio = PortfolioState(
        equity=10000,
        cash=10000,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
    proposal = TradeProposal(
        strategy_id="sell_without_position",
        symbol="AAPL",
        action=TradeAction.SELL,
        asset_class=AssetClass.STOCK,
        quantity=1,
        estimated_price=100,
        thesis="Existing risk engine still rejects shorting.",
        confidence=0.8,
    )

    decision = TradeValidator.default().validate(proposal, portfolio)

    assert decision.approved is False
    assert "Rejected: sell quantity exceeds current position and shorting is disabled." in decision.reasons


def _short_proposal(**overrides):
    payload = {
        "strategy_id": "future_short_sim",
        "symbol": "AAPL",
        "action": "sell_short",
        "asset_class": "stock",
        "target_short_weight": 0.10,
        "estimated_price": 100,
        "thesis": "Future local-only short simulation.",
        "confidence": 0.7,
        "borrow_available_assumption": True,
    }
    payload.update(overrides)
    if "notional_exposure" in overrides:
        payload.pop("target_short_weight", None)
    return ShortProposal(**payload)


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

