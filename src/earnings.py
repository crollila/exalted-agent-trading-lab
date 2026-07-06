"""Earnings awareness: days until each symbol's next earnings report.

Holding through earnings unknowingly is the dumbest preventable loss, so the
researcher and strategist get "SYMBOL reports in N days" for every held and
watched symbol. Data comes from yfinance (best-effort) and is cached for a
trading day; failures degrade to a visible "unavailable" note, never silence.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from src.market_time import ny_trading_date

CACHE_FILE = "earnings_cache.json"
LOOKAHEAD_DAYS = 14  # only surface earnings within this window


def _default_fetch(symbol: str) -> date | None:
    """Next earnings date via yfinance; None when unknown."""

    import logging

    import yfinance as yf

    # ETFs (SPY/QQQ) have no earnings; yfinance logs a loud 404 for them.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    try:
        raw = yf.Ticker(symbol).calendar
    except Exception:  # noqa: BLE001 - provider hiccup -> unknown
        return None
    dates = None
    if isinstance(raw, dict):
        dates = raw.get("Earnings Date")
    elif raw is not None and hasattr(raw, "loc"):  # older pandas shape
        try:
            dates = list(raw.loc["Earnings Date"])
        except Exception:  # noqa: BLE001
            dates = None
    if not dates:
        return None
    first = dates[0] if isinstance(dates, (list, tuple)) else dates
    if isinstance(first, datetime):
        return first.date()
    if isinstance(first, date):
        return first
    try:
        return datetime.strptime(str(first)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def days_to_earnings(
    symbols: list[str],
    data_dir: Path,
    *,
    fetch: Callable[[str], date | None] | None = None,
) -> dict[str, int | None]:
    """{symbol: days until next earnings (0=today), None=unknown}. Daily cache.

    Only symbols reporting within LOOKAHEAD_DAYS get a number; everything else
    is None so prompts stay small and non-alarming.
    """

    fetch = fetch or _default_fetch
    today = ny_trading_date()
    cache_path = Path(data_dir) / "runtime" / CACHE_FILE

    cache: dict = {}
    if cache_path.exists():
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            if raw.get("date") == today.isoformat():
                cache = raw.get("symbols", {})
        except (json.JSONDecodeError, OSError):
            cache = {}

    out: dict[str, int | None] = {}
    dirty = False
    for symbol in sorted({s.strip().upper() for s in symbols if s.strip()}):
        if symbol in cache:
            iso = cache[symbol]
            earnings_date = date.fromisoformat(iso) if iso else None
        else:
            earnings_date = fetch(symbol)
            cache[symbol] = earnings_date.isoformat() if earnings_date else None
            dirty = True
        if earnings_date is None:
            out[symbol] = None
            continue
        delta = (earnings_date - today).days
        out[symbol] = delta if 0 <= delta <= LOOKAHEAD_DAYS else None

    if dirty:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"date": today.isoformat(), "symbols": cache}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
    return out


def render_earnings(earnings: dict[str, int | None]) -> list[str]:
    """Compact human lines for prompts: only symbols actually reporting soon."""

    lines = []
    for symbol, days in sorted(earnings.items(), key=lambda kv: (kv[1] is None, kv[1])):
        if days is None:
            continue
        when = "TODAY" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
        lines.append(f"{symbol} reports earnings {when}")
    return lines
