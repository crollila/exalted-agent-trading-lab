"""Tests for loop reliability fixes (Phase 7U): market-time scoping, read-only
broker helpers, and daily-order reconciliation that never submits."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.competition import market_time
from src.config.settings import Settings


# --- Market time helpers ---------------------------------------------------


def test_ny_session_start_is_midnight_et_in_utc():
    # 2026-06-29 02:00 UTC is 2026-06-28 22:00 ET -> session start is 2026-06-28 04:00 UTC (00:00 ET).
    moment = datetime(2026, 6, 29, 2, 0, tzinfo=timezone.utc)
    start = market_time.ny_session_start_utc(moment)
    assert market_time.to_ny(start).hour == 0
    assert market_time.to_ny(start).date() == date(2026, 6, 28)


def test_ny_trading_date_uses_eastern_not_utc():
    # 2026-06-29 01:30 UTC is still 2026-06-28 in ET.
    moment = datetime(2026, 6, 29, 1, 30, tzinfo=timezone.utc)
    assert market_time.ny_trading_date(moment) == date(2026, 6, 28)


# --- Read-only broker helpers ----------------------------------------------


class RecordingClient:
    """Fake Alpaca trading client that records every method invoked."""

    def __init__(self):
        self.calls: list[str] = []

    def get_account(self):
        self.calls.append("get_account")
        return SimpleNamespace(equity="1000", cash="500", buying_power="500")

    def get_clock(self):
        self.calls.append("get_clock")
        return SimpleNamespace(
            is_open=False,
            timestamp=datetime(2026, 6, 29, 5, 0, tzinfo=timezone.utc),
            next_open=datetime(2026, 6, 29, 13, 30, tzinfo=timezone.utc),
            next_close=datetime(2026, 6, 29, 20, 0, tzinfo=timezone.utc),
        )

    def get_orders(self, filter=None):  # noqa: A002 - matches alpaca-py signature
        self.calls.append("get_orders")
        self.last_filter = filter
        return [SimpleNamespace(id="o1"), SimpleNamespace(id="o2"), SimpleNamespace(id="o3")]

    def submit_order(self, *_a, **_k):  # pragma: no cover - must never be called here
        self.calls.append("submit_order")
        raise AssertionError("diagnostic/reconciliation must never submit an order")


def _paper_settings() -> Settings:
    return Settings(
        alpaca_api_key="paper-key",
        alpaca_secret_key="paper-secret",
        alpaca_paper=True,
        alpaca_base_url=PAPER_BASE_URL,
        database_path="data/test.sqlite3",
        dry_run=True,
        starting_equity=10000,
        min_cash_pct=0.10,
        max_position_pct=0.20,
        max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )


def _wrapper(recorder: RecordingClient) -> AlpacaClientWrapper:
    return AlpacaClientWrapper(settings=_paper_settings(), client_factory=lambda _s: recorder)


def test_get_clock_snapshot_is_readonly_and_complete():
    rec = RecordingClient()
    snap = _wrapper(rec).get_clock_snapshot()
    assert snap["is_open"] is False
    assert snap["next_open"] is not None and snap["next_close"] is not None
    assert rec.calls == ["get_clock"]
    assert "submit_order" not in rec.calls


def test_count_orders_since_is_readonly():
    rec = RecordingClient()
    after = market_time.ny_session_start_utc()
    count = _wrapper(rec).count_orders_since(after)
    assert count == 3
    assert rec.calls == ["get_orders"]
    assert "submit_order" not in rec.calls


def test_count_orders_since_is_scoped_to_today_et_session():
    """The order count filters from today's ET session start, so prior-day orders
    (a stale counter) never block a fresh trading day."""

    rec = RecordingClient()
    after = market_time.ny_session_start_utc()
    _wrapper(rec).count_orders_since(after)
    assert rec.last_filter.after == after
    # The filter lower bound is midnight ET (today), not the epoch / a stale value.
    assert market_time.to_ny(rec.last_filter.after).hour == 0


def test_orders_today_reconciliation_degrades_to_zero(monkeypatch):
    """When the broker order listing fails, reconciliation returns 0 (never raises)."""

    import src.main as main

    class Boom:
        def has_credentials(self):
            return True

        def count_orders_since(self, _after):
            raise RuntimeError("alpaca down")

    monkeypatch.setattr(main, "client_for_source", lambda *a, **k: Boom())
    assert main._orders_today_for_source("team_alpha", _paper_settings()) == 0


def test_orders_today_reconciliation_counts_when_available(monkeypatch):
    import src.main as main

    monkeypatch.setattr(main, "client_for_source", lambda *a, **k: _wrapper(RecordingClient()))
    assert main._orders_today_for_source("team_alpha", _paper_settings()) == 3
