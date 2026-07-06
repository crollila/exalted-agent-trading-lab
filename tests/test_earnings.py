"""Earnings awareness: caching, windowing, rendering. Fetcher is injected."""

from __future__ import annotations

from datetime import timedelta

from src.earnings import LOOKAHEAD_DAYS, days_to_earnings, render_earnings
from src.market_time import ny_trading_date


def test_days_to_earnings_and_window(tmp_path):
    today = ny_trading_date()
    calendar = {
        "NVDA": today + timedelta(days=2),
        "AAPL": today,                                  # reports today
        "MSFT": today + timedelta(days=LOOKAHEAD_DAYS + 10),  # too far out
        "TSLA": None,                                   # unknown
    }
    fetch_calls: list[str] = []

    def fetch(symbol):
        fetch_calls.append(symbol)
        return calendar.get(symbol)

    result = days_to_earnings(["NVDA", "AAPL", "MSFT", "TSLA"], tmp_path, fetch=fetch)
    assert result["NVDA"] == 2
    assert result["AAPL"] == 0
    assert result["MSFT"] is None  # beyond lookahead window
    assert result["TSLA"] is None


def test_daily_cache_prevents_refetch(tmp_path):
    today = ny_trading_date()
    calls: list[str] = []

    def fetch(symbol):
        calls.append(symbol)
        return today + timedelta(days=3)

    days_to_earnings(["NVDA"], tmp_path, fetch=fetch)
    days_to_earnings(["NVDA"], tmp_path, fetch=fetch)  # second call: cached
    assert calls == ["NVDA"]


def test_render_earnings_only_shows_upcoming():
    lines = render_earnings({"NVDA": 2, "AAPL": 0, "MSFT": None, "TSLA": 1})
    text = "\n".join(lines)
    assert "AAPL reports earnings TODAY" in text
    assert "TSLA reports earnings tomorrow" in text
    assert "NVDA reports earnings in 2 days" in text
    assert "MSFT" not in text
