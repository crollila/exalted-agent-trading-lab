"""Allowlisted Alpaca market-data helpers (quotes/bars/SPY benchmark).

Read-only and provenance-tagged. These helpers fetch latest trade prices for an
allowlisted watchlist and the SPY benchmark. They never submit orders. A price
function can be injected for tests; the default lazily builds an Alpaca
historical-data client from paper credentials.
"""

from __future__ import annotations

from typing import Any, Callable

from src.competition.proposals import DataProvenance
from src.config.settings import Settings

# Small allowlisted watchlist for live market context.
WEEK_COMPETITION_WATCHLIST = (
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMD",
    "META",
    "GOOGL",
    "AMZN",
)

PriceFn = Callable[[str], float]


def build_alpaca_price_fn(settings: Settings) -> PriceFn:
    """Build a latest-trade price function from paper credentials.

    Uses the Alpaca historical-data client. The returned function raises on
    failure; callers should degrade to 'unknown' rather than inventing prices.
    """

    if not (settings.alpaca_api_key and settings.alpaca_secret_key):
        raise RuntimeError("Alpaca credentials required for market data.")

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest

    client = StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )

    def price_fn(symbol: str) -> float:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol.upper())
        result = client.get_stock_latest_trade(request)
        trade = result[symbol.upper()]
        return float(trade.price)

    return price_fn


def latest_price(symbol: str, price_fn: PriceFn | None) -> tuple[float | None, DataProvenance]:
    """Return (price, provenance). Provenance is 'unknown' when unavailable."""

    if price_fn is None:
        return None, DataProvenance.UNKNOWN
    try:
        return float(price_fn(symbol)), DataProvenance.LIVE
    except Exception:  # noqa: BLE001 - any data failure degrades to unknown, never invents
        return None, DataProvenance.UNKNOWN


def latest_prices(
    symbols: tuple[str, ...],
    price_fn: PriceFn | None,
) -> dict[str, dict[str, Any]]:
    """Return a provenance-tagged price map for the given symbols."""

    out: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        price, provenance = latest_price(symbol, price_fn)
        out[symbol.upper()] = {"price": price, "provenance": provenance.value}
    return out


def spy_return(starting_price: float | None, current_price: float | None) -> float | None:
    """Compute SPY return; None when either price is unavailable."""

    if not starting_price or not current_price or starting_price <= 0:
        return None
    return (current_price - starting_price) / starting_price
