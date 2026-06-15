from types import SimpleNamespace

from src.competition.proposals import DataProvenance
from src.research.data_tools import (
    ALLOWLISTED_TOOLS,
    alpaca_account_status,
    alpaca_latest_quote,
    gather_research_context,
    spy_benchmark,
)


class FakeClient:
    def has_credentials(self):
        return True

    def get_account(self):
        return SimpleNamespace(equity="1000", cash="500", buying_power="1500")

    def get_positions(self):
        return [SimpleNamespace(symbol="SPY", qty="2")]

    def is_market_open(self):
        return True


def test_account_status_live_when_client_available():
    point = alpaca_account_status(FakeClient())
    assert point.provenance == DataProvenance.LIVE
    assert point.value["equity"] == "1000"


def test_account_status_unknown_without_client():
    point = alpaca_account_status(None)
    assert point.provenance == DataProvenance.UNKNOWN
    assert point.value is None


def test_quote_unknown_without_provider():
    point = alpaca_latest_quote("SPY")
    assert point.provenance == DataProvenance.UNKNOWN
    assert "unknown" in (point.note or "").lower()


def test_spy_benchmark_unknown_when_missing():
    assert spy_benchmark(None).provenance == DataProvenance.UNKNOWN
    assert spy_benchmark(0.01, DataProvenance.FIXTURE).provenance == DataProvenance.FIXTURE


def test_gather_context_records_sources():
    context = gather_research_context(FakeClient(), spy_return_pct=0.01)
    sources = context.sources_used()
    for tool in ("alpaca_account_status", "spy_benchmark", "local_runtime_history"):
        assert tool in sources


def test_research_tools_cannot_submit_orders():
    # Allowlisted tools never expose an order-submission surface.
    for name in ALLOWLISTED_TOOLS:
        assert "submit" not in name and "order" not in name
