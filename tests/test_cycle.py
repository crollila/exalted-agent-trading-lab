"""Full-cycle integration tests with a fake broker and fake LLM (no network).

These prove the wiring: researcher -> strategist -> risk analyst -> engine ->
broker, plus the two failure modes that must be LOUD (LLM down) and SAFE
(kill switch engaged).
"""

from __future__ import annotations

import json

import pytest

from src.broker import AccountInfo, AssetInfo, Broker, ClockInfo, OrderResult, SnapshotInfo
from src.cycle import run_team_cycle
from src.kill_switch import engage
from src.ledger import read_trades
from tests.test_agents_parsing import make_llm


@pytest.fixture(autouse=True)
def no_earnings_lookup(monkeypatch):
    """Cycle tests must never hit yfinance; earnings degrade to empty."""

    monkeypatch.setattr("src.cycle.days_to_earnings", lambda symbols, data_dir, **kw: {})


class FakeBroker(Broker):
    """Overrides every network call; records submissions."""

    def __init__(self, market_open=True):
        super().__init__(api_key="k", secret_key="s")
        self.market_open = market_open
        self.submitted: list[tuple[str, int, str]] = []

    def account(self):
        return AccountInfo(equity=1_000_000.0, last_equity=995_000.0, cash=900_000.0, buying_power=1_800_000.0)

    def positions(self):
        return []

    def clock(self):
        return ClockInfo(is_open=self.market_open, next_open=None, next_close=None, timestamp=None)

    def orders_today(self):
        return []

    def notional_submitted_today(self, price_of):
        return 0.0

    def snapshots(self, symbols):
        return {
            s.upper(): SnapshotInfo(symbol=s.upper(), price=200.0, prev_close=198.0, day_change_pct=0.0101)
            for s in symbols
        }

    def news(self, symbols, limit=12, lookback_hours=36):
        return []

    def movers(self, top=8):
        return []

    def resolve_option(self, underlying, option_type, *, ref_price, dte_target=30, moneyness="atm"):
        return None

    def asset(self, symbol):
        return AssetInfo(symbol=symbol.upper(), tradable=True, shortable=True)

    def submit_market_order(self, symbol, qty, side):
        from src.kill_switch import assert_clear

        assert_clear(self.kill_switch_path)
        self.submitted.append((symbol, qty, side))
        return OrderResult(submitted=True, order_id=f"order-{len(self.submitted)}", status="accepted")


RESEARCH_REPLY = json.dumps({
    "market_view": "Semis leading.",
    "key_events": [],
    "ideas": [{"symbol": "NVDA", "direction": "long", "note": "strength", "source_ids": []}],
    "risks": [],
})
STRATEGIST_REPLY = json.dumps({
    "portfolio_view": "Flat book; add leadership.",
    "proposals": [{
        "symbol": "NVDA", "action": "buy", "weight_pct": 0.10,
        "thesis": "semis leadership", "exit_plan": "stop -5%", "confidence": 0.7,
    }],
    "no_trade_reason": None,
})
RISK_REPLY = json.dumps({
    "verdicts": [{"index": 0, "verdict": "approve", "reason": "sized fine"}],
})


def test_full_cycle_submits_order(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)  # kill switch default path -> tmp
    broker = FakeBroker()
    llm = make_llm(settings, [RESEARCH_REPLY, STRATEGIST_REPLY, RISK_REPLY])

    result = run_team_cycle(settings, "team_alpha", broker=broker, llm=llm)

    assert result.ok and result.error is None
    assert result.orders_submitted == 1
    assert broker.submitted == [("NVDA", 500, "buy")]  # 10% of $1M at $200

    trades = read_trades(settings.data_dir, "team_alpha")
    assert len(trades) == 1
    assert trades[0]["thesis"] == "semis leadership"

    # Audit file exists and tells the full story.
    assert result.audit_path is not None
    audit = json.loads(open(result.audit_path, encoding="utf-8").read())
    assert audit["researcher"]["market_view"] == "Semis leading."
    assert audit["risk_engine"][0]["approved"] is True


def test_cycle_dry_run_submits_nothing(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)
    broker = FakeBroker()
    llm = make_llm(settings, [RESEARCH_REPLY, STRATEGIST_REPLY, RISK_REPLY])

    result = run_team_cycle(settings, "team_alpha", broker=broker, llm=llm, dry_run=True)

    assert result.ok
    assert result.orders_submitted == 0
    assert broker.submitted == []
    assert read_trades(settings.data_dir, "team_alpha") == []


def test_cycle_llm_failure_is_loud_not_silent(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    broker = FakeBroker()
    llm = make_llm(settings, [RuntimeError("api down"), RuntimeError("api down")])

    result = run_team_cycle(settings, "team_alpha", broker=broker, llm=llm)

    assert not result.ok
    assert result.error is not None and "AGENT FAILURE" in result.error
    assert broker.submitted == []
    # The audit records the error too.
    audit = json.loads(open(result.audit_path, encoding="utf-8").read())
    assert "error" in audit


def test_cycle_skips_when_market_closed(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)
    broker = FakeBroker(market_open=False)
    llm = make_llm(settings, [])

    result = run_team_cycle(settings, "team_alpha", broker=broker, llm=llm)
    assert result.skipped_reason is not None
    assert "market closed" in result.skipped_reason


def test_cycle_respects_kill_switch(settings, monkeypatch, tmp_path):
    monkeypatch.chdir(settings.data_dir.parent)
    engage("test pause")  # default path under the tmp cwd
    broker = FakeBroker()
    llm = make_llm(settings, [])

    result = run_team_cycle(settings, "team_alpha", broker=broker, llm=llm)
    assert result.skipped_reason is not None
    assert "KILL SWITCH" in result.skipped_reason
    assert broker.submitted == []


def test_strategist_no_trade_is_recorded(settings, monkeypatch):
    monkeypatch.chdir(settings.data_dir.parent)
    broker = FakeBroker()
    no_trade = json.dumps({
        "portfolio_view": "Nothing beats holdings.",
        "proposals": [],
        "no_trade_reason": "no edge vs current book",
    })
    llm = make_llm(settings, [RESEARCH_REPLY, no_trade])

    result = run_team_cycle(settings, "team_alpha", broker=broker, llm=llm)
    assert result.ok
    assert result.orders_submitted == 0
    assert result.no_trade_reason == "no edge vs current book"
