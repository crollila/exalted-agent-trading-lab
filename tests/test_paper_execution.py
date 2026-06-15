from types import SimpleNamespace

import pytest

from competition_helpers import option_long_call, stock_long, stock_short
from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.brokers.options_adapter import OptionsAdapterNotConfigured, OptionsExecutionAdapter
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.competition.execution import build_order_request, execute_routed_proposals
from src.competition.risk_engine import AccountContext, evaluate_proposal
from src.competition.router import RoutedProposal, route_proposals
from src.config.permissions import TradingPermissions
from src.config.settings import Settings
from src.safety.kill_switch import engage


class FakeTradingClient:
    def __init__(self):
        self.submitted_orders = []

    def get_account(self):
        return SimpleNamespace(equity="1000000", cash="1000000", buying_power="2000000")

    def get_all_positions(self):
        return []

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def submit_order(self, order_request):
        self.submitted_orders.append(order_request)
        return SimpleNamespace(id=f"paper-{len(self.submitted_orders)}")


def settings(base_url=PAPER_BASE_URL, paper=True):
    return Settings(
        alpaca_api_key="k",
        alpaca_secret_key="s",
        alpaca_paper=paper,
        alpaca_base_url=base_url,
        database_path="data/test.sqlite3",
        dry_run=False,
        starting_equity=1_000_000,
        min_cash_pct=0.10,
        max_position_pct=0.20,
        max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )


def wrapper(ks_path=None, options_adapter=None):
    fake = FakeTradingClient()
    return (
        AlpacaClientWrapper(
            settings=settings(),
            client_factory=lambda _s: fake,
            kill_switch_path=ks_path,
            options_adapter=options_adapter,
        ),
        fake,
    )


def acct():
    return AccountContext(equity=1_000_000.0, cash=1_000_000.0, buying_power=2_000_000.0)


def perms(**o):
    return TradingPermissions.from_env(env={k: str(v) for k, v in o.items()})


# --- endpoint refusals ---


def test_live_endpoint_rejected():
    with pytest.raises(ValueError, match="Live Alpaca endpoint"):
        AlpacaClientWrapper(settings=settings(base_url="https://api.alpaca.markets"))


# --- short / margin submission goes through fake client only ---


def test_short_order_submits_via_fake_client():
    client, fake = wrapper()
    order = OrderRequest(
        proposal_id="p", symbol="XYZ", action=TradeAction.SELL,
        asset_class=AssetClass.STOCK, quantity=10, short=True,
        dry_run=False, risk_approved=True,
    )
    client.submit_paper_short_order(order)
    assert len(fake.submitted_orders) == 1


def test_margin_order_submits_via_fake_client():
    client, fake = wrapper()
    order = OrderRequest(
        proposal_id="p", symbol="AAPL", action=TradeAction.BUY,
        asset_class=AssetClass.STOCK, quantity=10, margin=True,
        dry_run=False, risk_approved=True,
    )
    client.submit_paper_margin_order(order)
    assert len(fake.submitted_orders) == 1


def test_short_path_requires_short_flag():
    client, _ = wrapper()
    order = OrderRequest(
        proposal_id="p", symbol="XYZ", action=TradeAction.SELL,
        asset_class=AssetClass.STOCK, quantity=10, short=False,
        dry_run=False, risk_approved=True,
    )
    with pytest.raises(ValueError, match="short=True"):
        client.submit_paper_short_order(order)


# --- options adapter boundary ---


def _long_call_option_order(**over):
    from datetime import date, timedelta

    expiry = (date.today() + timedelta(days=30)).isoformat()
    values = dict(
        proposal_id="p", symbol="SPY", action=TradeAction.BUY,
        asset_class=AssetClass.OPTION, quantity=1, contracts=1,
        option_symbol="SPY",
        option_contract={
            "legs": [{"side": "long", "option_type": "call", "strike": 510.0, "expiration": expiry}],
            "expiration": expiry,
        },
        dry_run=False, risk_approved=True,
    )
    values.update(over)
    return OrderRequest(**values)


def test_disabled_adapter_raises_clear_error():
    adapter = OptionsExecutionAdapter(enabled=False)
    client, _ = wrapper(options_adapter=adapter)
    with pytest.raises(OptionsAdapterNotConfigured, match="disabled"):
        client.submit_paper_option_order(_long_call_option_order())


def test_option_order_with_mock_adapter_submits():
    calls = []
    adapter = OptionsExecutionAdapter(submit_fn=lambda o, c: calls.append(o) or SimpleNamespace(id="opt-1"))
    client, _ = wrapper(options_adapter=adapter)
    result = client.submit_paper_option_order(_long_call_option_order())
    assert result.id == "opt-1"
    assert len(calls) == 1


# --- kill switch blocks broker submission ---


def test_kill_switch_blocks_broker_submission(tmp_path):
    ks = tmp_path / "ks.json"
    engage(reason="halt", path=ks)
    client, fake = wrapper(ks_path=str(ks))
    order = OrderRequest(
        proposal_id="p", symbol="SPY", action=TradeAction.BUY,
        asset_class=AssetClass.STOCK, quantity=2, dry_run=False, risk_approved=True,
    )
    from src.safety.kill_switch import KillSwitchEngaged

    with pytest.raises(KillSwitchEngaged):
        client.submit_paper_order(order)
    assert fake.submitted_orders == []


# --- execute_routed_proposals integration ---


def test_execute_dry_run_does_not_submit():
    routing = route_proposals([stock_long()], perms(), acct())
    records = execute_routed_proposals(routing.execution_eligible, client=None, dry_run=True)
    assert len(records) == 1
    assert records[0].submitted is False
    assert "Dry-run" in records[0].detail


def test_execute_live_paper_submits_with_fake_client(tmp_path):
    ks = tmp_path / "ks.json"
    client, fake = wrapper(ks_path=str(ks))
    routing = route_proposals([stock_long()], perms(), acct())
    records = execute_routed_proposals(
        routing.execution_eligible, client=client, dry_run=False, kill_switch_path=str(ks)
    )
    assert records[0].submitted is True
    assert len(fake.submitted_orders) == 1


def test_execute_blocked_when_kill_switch_engaged(tmp_path):
    ks = tmp_path / "ks.json"
    engage(reason="halt", path=ks)
    client, fake = wrapper(ks_path=str(ks))
    routing = route_proposals([stock_long()], perms(), acct())
    records = execute_routed_proposals(
        routing.execution_eligible, client=client, dry_run=False, kill_switch_path=str(ks)
    )
    assert records[0].submitted is False
    assert fake.submitted_orders == []


def test_build_order_request_short_sets_flags():
    decision = evaluate_proposal(stock_short(), perms(ENABLE_PAPER_SHORTING="true"), acct())
    order = build_order_request(RoutedProposal(proposal=stock_short(), decision=decision), dry_run=False)
    assert order.short is True
    assert order.action == TradeAction.SELL
