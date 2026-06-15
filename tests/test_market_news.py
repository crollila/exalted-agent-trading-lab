from src.competition.proposals import DataProvenance
from src.research.market_data import (
    WEEK_COMPETITION_WATCHLIST,
    latest_price,
    latest_prices,
    spy_return,
)
from src.research.news import NewsConfig, fetch_news, news_provider_status


def test_spy_return_computes_when_prices_available():
    assert spy_return(500.0, 505.0) == 0.01


def test_spy_return_unknown_when_price_missing():
    assert spy_return(None, 505.0) is None
    assert spy_return(500.0, None) is None
    assert spy_return(0.0, 505.0) is None


def test_latest_price_unknown_without_fn():
    price, provenance = latest_price("SPY", None)
    assert price is None
    assert provenance == DataProvenance.UNKNOWN


def test_latest_price_live_with_fn():
    price, provenance = latest_price("SPY", lambda s: 500.0)
    assert price == 500.0
    assert provenance == DataProvenance.LIVE


def test_latest_price_degrades_on_error():
    def boom(symbol):
        raise RuntimeError("data down")

    price, provenance = latest_price("SPY", boom)
    assert price is None
    assert provenance == DataProvenance.UNKNOWN


def test_latest_prices_tags_each_symbol():
    prices = latest_prices(("SPY", "QQQ"), lambda s: 100.0)
    assert prices["SPY"]["price"] == 100.0
    assert prices["SPY"]["provenance"] == "live"


def test_watchlist_has_expected_symbols():
    for symbol in ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL", "AMZN"):
        assert symbol in WEEK_COMPETITION_WATCHLIST


# --- news ---


def test_news_unavailable_by_default():
    status = news_provider_status(NewsConfig(enabled=False, provider="none"))
    assert status["available"] is False
    result = fetch_news(("SPY",), config=NewsConfig(enabled=False, provider="none"))
    assert result["provenance"] == "unknown"
    assert result["items"] == []


def test_news_available_with_fetcher():
    config = NewsConfig(enabled=True, provider="demo")
    result = fetch_news(("SPY",), config=config, fetcher=lambda syms: [{"headline": "x"}])
    assert result["provenance"] == "live"
    assert result["items"] == [{"headline": "x"}]


def test_news_degrades_when_fetcher_fails():
    config = NewsConfig(enabled=True, provider="demo")

    def boom(syms):
        raise RuntimeError("news down")

    result = fetch_news(("SPY",), config=config, fetcher=boom)
    assert result["provenance"] == "unknown"
    assert "failed" in result["note"]
