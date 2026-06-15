"""Paper options adapter: single-leg execution, refusals, team credentials.

All Alpaca calls are mocked. No real network or credentials are used.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.brokers.options_adapter import (
    OptionsAdapterNotConfigured,
    OptionsExecutionAdapter,
    OptionsExecutionRefused,
    build_occ_symbol,
)
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.brokers.paper_auth import client_for_source
from src.competition.execution import execute_routed_proposals
from src.competition.risk_engine import AccountContext, evaluate_proposal
from src.competition.router import RoutedProposal, route_proposals
from src.config.permissions import TradingPermissions
from src.config.settings import Settings

PAPER_URL = PAPER_BASE_URL
EXPIRY = (date.today() + timedelta(days=30)).isoformat()


class FakeTradingClient:
    def __init__(self):
        self.submitted = []

    def submit_order(self, request):
        self.submitted.append(request)
        return SimpleNamespace(id=f"opt-{len(self.submitted)}", symbol=getattr(request, "symbol", None))


def option_order(**over):
    legs = over.pop("legs", [{"side": "long", "option_type": "call", "strike": 510.0, "expiration": EXPIRY}])
    contract = {"legs": legs, "expiration": over.pop("expiration", EXPIRY)}
    values = dict(
        proposal_id="p",
        symbol="SPY",
        action=TradeAction.BUY,
        asset_class=AssetClass.OPTION,
        quantity=1,
        contracts=1,
        option_symbol="SPY",
        option_contract=contract,
        dry_run=False,
        risk_approved=True,
    )
    values.update(over)
    return OrderRequest(**values)


# --- OCC symbol ---


def test_build_occ_symbol():
    assert build_occ_symbol("spy", date(2024, 9, 20), "call", 510.0) == "SPY240920C00510000"
    assert build_occ_symbol("SPY", date(2024, 9, 20), "put", 512.5) == "SPY240920P00512500"


# --- single-leg execution through mocked client ---


def test_long_call_submits_through_mocked_client():
    adapter = OptionsExecutionAdapter()
    fake = FakeTradingClient()
    result = adapter.submit(option_order(), fake)
    assert len(fake.submitted) == 1
    symbol = fake.submitted[0].symbol
    assert symbol.startswith("SPY")
    assert symbol.endswith("C00510000")
    assert result.id == "opt-1"


def test_long_put_submits_through_mocked_client():
    adapter = OptionsExecutionAdapter()
    fake = FakeTradingClient()
    order = option_order(legs=[{"side": "long", "option_type": "put", "strike": 480.0, "expiration": EXPIRY}])
    adapter.submit(order, fake)
    assert fake.submitted[0].symbol.endswith("P00480000")


def test_injected_submit_fn_used():
    calls = []
    adapter = OptionsExecutionAdapter(submit_fn=lambda o, c: calls.append(o) or SimpleNamespace(id="x"))
    adapter.submit(option_order(), FakeTradingClient())
    assert len(calls) == 1


# --- refusals ---


def test_refuses_0dte():
    adapter = OptionsExecutionAdapter()
    order = option_order(
        legs=[{"side": "long", "option_type": "call", "strike": 510.0, "expiration": date.today().isoformat()}],
        expiration=date.today().isoformat(),
    )
    with pytest.raises(OptionsExecutionRefused, match="0DTE"):
        adapter.submit(order, FakeTradingClient())


def test_refuses_naked_short_call():
    adapter = OptionsExecutionAdapter()
    order = option_order(legs=[{"side": "short", "option_type": "call", "strike": 510.0, "expiration": EXPIRY}])
    with pytest.raises(OptionsExecutionRefused, match="naked|uncovered"):
        adapter.submit(order, FakeTradingClient())


def test_refuses_single_short_leg():
    adapter = OptionsExecutionAdapter()
    order = option_order(legs=[{"side": "short", "option_type": "put", "strike": 480.0, "expiration": EXPIRY}])
    with pytest.raises(OptionsExecutionRefused):
        adapter.submit(order, FakeTradingClient())


def test_refuses_unapproved_quantity():
    adapter = OptionsExecutionAdapter()
    with pytest.raises(OptionsExecutionRefused, match="contract quantity"):
        adapter.submit(option_order(contracts=None, quantity=1), FakeTradingClient())


def test_refuses_unapproved_risk_decision():
    adapter = OptionsExecutionAdapter()
    with pytest.raises(OptionsExecutionRefused, match="approved"):
        adapter.submit(option_order(risk_approved=False), FakeTradingClient())


def test_refuses_missing_contract_legs():
    adapter = OptionsExecutionAdapter()
    order = OrderRequest(
        proposal_id="p", symbol="SPY", action=TradeAction.BUY, asset_class=AssetClass.OPTION,
        quantity=1, contracts=1, option_symbol="SPY", option_contract={"legs": []},
        dry_run=False, risk_approved=True,
    )
    with pytest.raises(OptionsExecutionRefused, match="legs"):
        adapter.submit(order, FakeTradingClient())


def test_disabled_adapter_refuses():
    adapter = OptionsExecutionAdapter(enabled=False)
    with pytest.raises(OptionsAdapterNotConfigured):
        adapter.submit(option_order(), FakeTradingClient())


def test_spread_refused_by_default():
    adapter = OptionsExecutionAdapter()  # enable_spreads False
    legs = [
        {"side": "long", "option_type": "call", "strike": 500.0, "expiration": EXPIRY},
        {"side": "short", "option_type": "call", "strike": 510.0, "expiration": EXPIRY},
    ]
    with pytest.raises(OptionsExecutionRefused, match="spread"):
        adapter.submit(option_order(legs=legs), FakeTradingClient())


def test_spread_executes_when_enabled():
    adapter = OptionsExecutionAdapter(enable_spreads=True)
    fake = FakeTradingClient()
    legs = [
        {"side": "long", "option_type": "call", "strike": 500.0, "expiration": EXPIRY},
        {"side": "short", "option_type": "call", "strike": 510.0, "expiration": EXPIRY},
    ]
    adapter.submit(option_order(legs=legs), fake)
    assert len(fake.submitted) == 1


def test_adapter_status_flags():
    default = OptionsExecutionAdapter()
    assert default.configured is True
    assert default.single_leg_enabled is True
    assert default.spreads_enabled is False
    assert OptionsExecutionAdapter(enable_spreads=True).spreads_enabled is True


# --- wrapper-level endpoint/mode refusals (protect options too) ---


def _settings(base_url=PAPER_URL, paper=True):
    return Settings(
        alpaca_api_key="k", alpaca_secret_key="s", alpaca_paper=paper, alpaca_base_url=base_url,
        database_path="data/test.sqlite3", dry_run=False, starting_equity=1_000_000,
        min_cash_pct=0.1, max_position_pct=0.2, max_daily_turnover_pct=0.3, max_new_positions_per_day=5,
    )


def test_options_refused_on_live_endpoint():
    with pytest.raises(ValueError, match="Live Alpaca endpoint"):
        AlpacaClientWrapper(settings=_settings(base_url="https://api.alpaca.markets"))


def test_options_refused_in_non_paper_mode():
    with pytest.raises(ValueError, match="paper"):
        AlpacaClientWrapper(settings=_settings(paper=False))


# --- Alpaca rejection is logged and does not crash the loop ---


def test_alpaca_rejection_logged_not_fatal():
    class RejectingWrapper:
        def submit_paper_option_order(self, order):
            raise RuntimeError("403 options trading not enabled for this account")

    helpers = __import__("competition_helpers")
    proposal = helpers.option_long_call(team_id="team_alpha")
    perms = TradingPermissions.from_env(env={"ENABLE_PAPER_OPTIONS": "true"})
    acct = AccountContext(equity=1_000_000.0, cash=1_000_000.0, buying_power=2_000_000.0)
    routing = route_proposals([proposal], perms, acct)
    assert routing.execution_eligible  # option is eligible

    records = execute_routed_proposals(
        routing.execution_eligible, client=RejectingWrapper(), dry_run=False
    )
    assert records[0].submitted is False
    assert "403" in records[0].detail or "failed" in records[0].detail.lower()


# --- team credentials only, no global fallback ---


def _team_env():
    return {
        "ALPACA_API_KEY": "GLOBAL_KEY", "ALPACA_SECRET_KEY": "GLOBAL_SECRET",
        "ALPACA_PAPER": "true", "ALPACA_BASE_URL": PAPER_URL,
        "TEAM_ALPHA_ALPACA_API_KEY": "ALPHA_KEY", "TEAM_ALPHA_ALPACA_SECRET_KEY": "ALPHA_SECRET",
        "TEAM_ALPHA_ALPACA_PAPER": "true", "TEAM_ALPHA_ALPACA_BASE_URL": PAPER_URL,
        "TEAM_BETA_ALPACA_API_KEY": "BETA_KEY", "TEAM_BETA_ALPACA_SECRET_KEY": "BETA_SECRET",
        "TEAM_BETA_ALPACA_PAPER": "true", "TEAM_BETA_ALPACA_BASE_URL": PAPER_URL,
    }


def _submit_option_via_source(source: str) -> str:
    captured = {}

    def factory(settings):
        captured["api_key"] = settings.alpaca_api_key
        return FakeTradingClient()

    client = client_for_source(
        source,
        base_settings=_settings(),
        env=_team_env(),
        client_factory=factory,
        options_adapter=OptionsExecutionAdapter(),
    )
    client.submit_paper_option_order(option_order())
    return captured["api_key"]


def test_team_alpha_options_use_alpha_credentials_only():
    assert _submit_option_via_source("team_alpha") == "ALPHA_KEY"


def test_team_beta_options_use_beta_credentials_only():
    assert _submit_option_via_source("team_beta") == "BETA_KEY"
