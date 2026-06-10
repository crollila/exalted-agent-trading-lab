from types import SimpleNamespace

import pytest

from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.config.settings import Settings


class FakeTradingClient:
    def __init__(self):
        self.submitted_order = None

    def get_account(self):
        return SimpleNamespace(equity="10000", cash="5000", buying_power="5000")

    def get_all_positions(self):
        return [SimpleNamespace(symbol="SPY")]

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def submit_order(self, order_request):
        self.submitted_order = order_request
        return SimpleNamespace(id="paper-order-1")


def settings(
    *,
    api_key="paper-key",
    secret_key="paper-secret",
    paper=True,
    base_url=PAPER_BASE_URL,
):
    return Settings(
        alpaca_api_key=api_key,
        alpaca_secret_key=secret_key,
        alpaca_paper=paper,
        alpaca_base_url=base_url,
        database_path="data/test.sqlite3",
        dry_run=True,
        starting_equity=10000,
        min_cash_pct=0.10,
        max_position_pct=0.20,
        max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )


def approved_order(**overrides):
    values = {
        "proposal_id": "proposal-1",
        "symbol": "SPY",
        "action": TradeAction.BUY,
        "asset_class": AssetClass.STOCK,
        "quantity": 2,
        "dry_run": False,
        "risk_approved": True,
    }
    values.update(overrides)
    return OrderRequest(**values)


def test_refuses_missing_alpaca_paper_setting():
    with pytest.raises(ValueError, match="ALPACA_PAPER=true"):
        AlpacaClientWrapper(settings=settings(paper=None))


def test_refuses_non_paper_mode():
    with pytest.raises(ValueError, match="ALPACA_PAPER=true"):
        AlpacaClientWrapper(settings=settings(paper=False))


def test_refuses_non_paper_base_url():
    with pytest.raises(ValueError, match=PAPER_BASE_URL):
        AlpacaClientWrapper(settings=settings(base_url="https://api.alpaca.markets"))


def test_refuses_missing_credentials_before_creating_client():
    created = False

    def factory(_settings):
        nonlocal created
        created = True
        return FakeTradingClient()

    wrapper = AlpacaClientWrapper(settings=settings(api_key=None), client_factory=factory)

    with pytest.raises(RuntimeError, match="Missing Alpaca paper credentials"):
        wrapper.get_account()

    assert created is False


def test_get_account_positions_and_market_open_are_mocked():
    fake_client = FakeTradingClient()
    wrapper = AlpacaClientWrapper(settings=settings(), client_factory=lambda _settings: fake_client)

    assert wrapper.get_account().equity == "10000"
    assert len(wrapper.get_positions()) == 1
    assert wrapper.is_market_open() is True


def test_submit_paper_order_uses_fake_client_only():
    fake_client = FakeTradingClient()
    wrapper = AlpacaClientWrapper(settings=settings(), client_factory=lambda _settings: fake_client)

    result = wrapper.submit_paper_order(approved_order())

    assert result.id == "paper-order-1"
    assert fake_client.submitted_order.symbol == "SPY"


def test_submit_paper_order_rejects_unapproved_order_before_client_submit():
    fake_client = FakeTradingClient()
    wrapper = AlpacaClientWrapper(settings=settings(), client_factory=lambda _settings: fake_client)

    with pytest.raises(ValueError, match="approved risk decision"):
        wrapper.submit_paper_order(approved_order(risk_approved=False))

    assert fake_client.submitted_order is None


def test_submit_paper_order_rejects_dry_run_order_before_client_submit():
    fake_client = FakeTradingClient()
    wrapper = AlpacaClientWrapper(settings=settings(), client_factory=lambda _settings: fake_client)

    with pytest.raises(ValueError, match="Dry-run orders"):
        wrapper.submit_paper_order(approved_order(dry_run=True))

    assert fake_client.submitted_order is None


def test_submit_paper_order_rejects_non_stock_order_before_client_submit():
    fake_client = FakeTradingClient()
    wrapper = AlpacaClientWrapper(settings=settings(), client_factory=lambda _settings: fake_client)

    with pytest.raises(ValueError, match="Only stock orders"):
        wrapper.submit_paper_order(approved_order(asset_class=AssetClass.OPTION))

    assert fake_client.submitted_order is None
