"""Safety tests for the deterministic sell-to-close / trim path (Phase 7V).

These lock in the hard invariants: never oversell, full close never creates a
short, no-position sells are rejected, positions are refreshed immediately before
submission, and the broker path refuses anything that could open a short.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.competition.position_execution import (
    PositionActionProposal,
    build_sell_to_close_order,
    execute_sell_to_close,
    validate_sell_to_close,
)
from src.config.portfolio_limits import PortfolioLimits
from src.config.settings import Settings


def _limits(**overrides) -> PortfolioLimits:
    base = PortfolioLimits(enable_paper_sell_to_close=True)
    return replace(base, **overrides)


def _pos(symbol: str, qty: float, side: str = "long") -> dict:
    return {"symbol": symbol, "qty": qty, "side": side, "current_price": 100.0, "market_value": qty * 100.0}


# --- deterministic validation -------------------------------------------------


def test_trim_never_oversells_caps_to_held():
    positions = [_pos("AAPL", 100)]
    proposal = PositionActionProposal(symbol="AAPL", action="trim", requested_qty=250)
    decision = validate_sell_to_close(proposal, positions, limits=_limits())
    assert decision.approved is True
    assert decision.approved_qty == 100  # capped to held, never 250
    assert decision.approved_qty <= decision.held_qty


def test_exit_sells_exactly_held_and_cannot_go_short():
    positions = [_pos("AAPL", 100)]
    proposal = PositionActionProposal(symbol="AAPL", action="exit")
    decision = validate_sell_to_close(proposal, positions, limits=_limits())
    assert decision.approved is True
    assert decision.approved_qty == 100  # full close, position goes flat (never -1)
    # Resulting position = held - approved = 0, never negative.
    assert decision.held_qty - decision.approved_qty == 0


def test_no_position_sell_is_rejected():
    decision = validate_sell_to_close(
        PositionActionProposal(symbol="TSLA", action="exit"), [], limits=_limits()
    )
    assert decision.approved is False
    assert decision.approved_qty == 0
    assert any("No held LONG position" in r for r in decision.reasons)


def test_short_position_cannot_be_sold_to_close():
    # A net-short holding must not be reduced via sell-to-close (that's buy-to-cover).
    positions = [_pos("XYZ", -1000, side="short")]
    decision = validate_sell_to_close(
        PositionActionProposal(symbol="XYZ", action="exit"), positions, limits=_limits()
    )
    assert decision.approved is False
    assert any("buy-to-cover" in r for r in decision.reasons)


def test_disabled_flag_blocks_sell_to_close():
    positions = [_pos("AAPL", 100)]
    decision = validate_sell_to_close(
        PositionActionProposal(symbol="AAPL", action="exit"),
        positions, limits=PortfolioLimits(enable_paper_sell_to_close=False),
    )
    assert decision.approved is False


def test_daily_exit_cap_enforced():
    positions = [_pos("AAPL", 100)]
    decision = validate_sell_to_close(
        PositionActionProposal(symbol="AAPL", action="exit"),
        positions, limits=_limits(max_position_exits_per_day=2), exits_used_today=2,
    )
    assert decision.approved is False
    assert any("Daily exit cap" in r for r in decision.reasons)


# --- order construction -------------------------------------------------------


def test_built_order_is_reduce_only_sell():
    positions = [_pos("AAPL", 100)]
    decision = validate_sell_to_close(
        PositionActionProposal(symbol="AAPL", action="exit"), positions, limits=_limits()
    )
    order = build_sell_to_close_order(decision, dry_run=False)
    assert order.action == TradeAction.SELL
    assert order.sell_to_close is True
    assert order.short is False and order.margin is False
    assert order.asset_class == AssetClass.STOCK
    assert order.quantity == 100


# --- execution refreshes positions immediately before submit ------------------


class RecordingClient:
    def __init__(self):
        self.calls: list[str] = []
        self.submitted: list[OrderRequest] = []

    def has_credentials(self):
        return True

    def submit_paper_sell_to_close_order(self, order):
        self.calls.append("submit_paper_sell_to_close_order")
        self.submitted.append(order)
        return SimpleNamespace(id="paper-stc-1")

    def submit_paper_order(self, *_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError("sell-to-close must not route through the long-buy path")


def test_execution_refreshes_positions_immediately_before_submit():
    client = RecordingClient()
    calls = {"n": 0}

    def refresh():
        # Simulate the holding shrinking between snapshots: the executor must use
        # the freshly-refreshed (smaller) quantity, never a stale larger one.
        calls["n"] += 1
        return [_pos("AAPL", 40)]

    records = execute_sell_to_close(
        [PositionActionProposal(symbol="AAPL", action="exit")],
        client=client, dry_run=False, limits=_limits(), refresh_positions=refresh,
    )
    assert calls["n"] >= 1  # refreshed before acting
    assert records[0].submitted is True
    assert records[0].approved_qty == 40  # used refreshed qty, not a stale value
    assert client.submitted[0].quantity == 40


def test_execution_dry_run_does_not_submit():
    client = RecordingClient()
    records = execute_sell_to_close(
        [PositionActionProposal(symbol="AAPL", action="exit")],
        client=client, dry_run=True, limits=_limits(),
        refresh_positions=lambda: [_pos("AAPL", 100)],
    )
    assert records[0].submitted is False
    assert client.calls == []  # nothing submitted in dry-run


# --- broker-level guard rails -------------------------------------------------


def _settings() -> Settings:
    return Settings(
        alpaca_api_key="paper-key", alpaca_secret_key="paper-secret",
        alpaca_paper=True, alpaca_base_url=PAPER_BASE_URL,
        database_path="data/test.sqlite3", dry_run=False, starting_equity=10000,
        min_cash_pct=0.10, max_position_pct=0.20, max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )


class FakeBroker:
    def __init__(self):
        self.order = None

    def submit_order(self, order):
        self.order = order
        return SimpleNamespace(id="ok")


def _wrapper(broker) -> AlpacaClientWrapper:
    return AlpacaClientWrapper(settings=_settings(), client_factory=lambda _s: broker)


def test_broker_sell_to_close_accepts_valid_reduce_only():
    broker = FakeBroker()
    order = OrderRequest(
        proposal_id="p", symbol="AAPL", action=TradeAction.SELL, asset_class=AssetClass.STOCK,
        quantity=10, sell_to_close=True, dry_run=False, risk_approved=True,
    )
    _wrapper(broker).submit_paper_sell_to_close_order(order)
    assert broker.order is not None


def test_broker_sell_to_close_rejects_short_flag():
    order = OrderRequest(
        proposal_id="p", symbol="AAPL", action=TradeAction.SELL, asset_class=AssetClass.STOCK,
        quantity=10, sell_to_close=True, short=True, dry_run=False, risk_approved=True,
    )
    with pytest.raises(ValueError, match="never open/increase a short"):
        _wrapper(FakeBroker()).submit_paper_sell_to_close_order(order)


def test_broker_long_buy_path_rejects_sell_to_close_flag():
    # Defense in depth: a sell_to_close order can never traverse the long-buy path.
    order = OrderRequest(
        proposal_id="p", symbol="AAPL", action=TradeAction.SELL, asset_class=AssetClass.STOCK,
        quantity=10, sell_to_close=True, dry_run=False, risk_approved=True,
    )
    with pytest.raises(ValueError):
        _wrapper(FakeBroker()).submit_paper_order(order)


def test_broker_long_buy_path_rejects_sell_action():
    order = OrderRequest(
        proposal_id="p", symbol="AAPL", action=TradeAction.SELL, asset_class=AssetClass.STOCK,
        quantity=10, dry_run=False, risk_approved=True,
    )
    with pytest.raises(ValueError, match="BUY action"):
        _wrapper(FakeBroker()).submit_paper_order(order)
