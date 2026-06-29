"""Tests for deterministic daily-notional reconciliation + cap enforcement (Phase 7Y).

No broker credentials are required: everything uses pure helpers or fakes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.brokers.order_models import AssetClass
from src.competition import daily_notional as dn
from src.competition import execution as ex
from src.competition import position_execution as pe
from src.competition.market_time import ny_session_start_utc, ny_trading_date
from src.config.portfolio_limits import PortfolioLimits
from src.config.settings import Settings


def _order(status="filled", filled_qty=None, filled_avg=None, qty=None, limit=None, notional=None):
    return SimpleNamespace(status=status, filled_qty=filled_qty, filled_avg_price=filled_avg,
                           qty=qty, limit_price=limit, notional=notional)


# --- 1) current-day submitted orders count toward daily notional --------------


def test_submitted_orders_count_toward_notional():
    orders = [
        _order(status="filled", filled_qty=10, filled_avg=100.0),   # $1,000
        _order(status="partially_filled", filled_qty=5, filled_avg=50.0),  # $250
        _order(status="new", qty=2, limit=25.0),                    # $50 (unfilled limit)
    ]
    assert dn.daily_notional_from_orders(orders) == pytest.approx(1300.0)


# --- 3) rejected/cancelled/expired/replaced orders do not count ---------------


@pytest.mark.parametrize("bad", ["rejected", "canceled", "cancelled", "expired", "replaced", "suspended"])
def test_rejected_or_cancelled_orders_excluded(bad):
    orders = [
        _order(status="filled", filled_qty=10, filled_avg=100.0),  # counts ($1,000)
        _order(status=bad, filled_qty=10, filled_avg=100.0),       # excluded
    ]
    assert dn.daily_notional_from_orders(orders) == pytest.approx(1000.0)
    assert dn.order_is_submitted(_order(status=bad)) is False


# --- 2) prior-day orders do not count (attribution date-scoping) --------------


def test_prior_day_attribution_excluded():
    today = ny_trading_date()
    now_utc = datetime.now(timezone.utc)
    today_entry = SimpleNamespace(broker_submitted=True, quantity=10, entry_price=100.0,
                                  timestamp=now_utc.isoformat())
    prior_entry = SimpleNamespace(broker_submitted=True, quantity=10, entry_price=100.0,
                                  timestamp=(now_utc - timedelta(days=3)).isoformat())
    unsubmitted = SimpleNamespace(broker_submitted=False, quantity=99, entry_price=100.0,
                                  timestamp=now_utc.isoformat())
    total = dn.daily_notional_from_attribution([today_entry, prior_entry, unsubmitted])
    assert total == pytest.approx(1000.0)  # only today's submitted entry


def test_broker_daily_notional_since_uses_session_start_and_excludes_rejected():
    captured = {}

    class FakeClient:
        def get_orders(self, filter=None):  # noqa: A002 - matches alpaca-py
            captured["after"] = filter.after
            return [
                _order(status="filled", filled_qty=4, filled_avg=250.0),  # $1,000
                _order(status="rejected", filled_qty=4, filled_avg=250.0),  # excluded
            ]

    settings = Settings(
        alpaca_api_key="k", alpaca_secret_key="s", alpaca_paper=True, alpaca_base_url=PAPER_BASE_URL,
        database_path="data/test.sqlite3", dry_run=True, starting_equity=10000,
        min_cash_pct=0.1, max_position_pct=0.2, max_daily_turnover_pct=0.3, max_new_positions_per_day=5,
    )
    wrapper = AlpacaClientWrapper(settings=settings, client_factory=lambda _s: FakeClient())
    after = ny_session_start_utc()
    assert wrapper.daily_notional_since(after) == pytest.approx(1000.0)
    assert captured["after"] == after  # scoped to today's ET session start


# --- 4) a next order that would exceed cap is rejected ------------------------


def test_would_exceed_cap_helper():
    assert dn.would_exceed_cap(900.0, 200.0, 1000.0) is True
    assert dn.would_exceed_cap(800.0, 200.0, 1000.0) is False
    assert dn.would_exceed_cap(900.0, 200.0, None) is False   # no cap configured
    assert dn.would_exceed_cap(900.0, 0.0, 1000.0) is False   # unpriced order


def _routed(notional, conf=0.5, symbol="AAA"):
    decision = SimpleNamespace(approved_notional=notional, approved_quantity=1.0,
                               proposal_type=SimpleNamespace(value="stock_long"))
    proposal = SimpleNamespace(proposal_id=f"p_{symbol}", symbol=symbol, underlying=None,
                               estimated_price=notional, confidence=conf,
                               proposal_type=SimpleNamespace(value="stock_long"))
    return SimpleNamespace(decision=decision, proposal=proposal)


class _RecordingClient:
    def __init__(self):
        self.submitted = []

    def submit_paper_order(self, order):
        self.submitted.append(order)
        return SimpleNamespace(id="ok")


def _patch_exec(monkeypatch):
    monkeypatch.setattr(ex, "is_engaged", lambda _p=None: False)
    monkeypatch.setattr(ex, "build_order_request",
                        lambda routed, dry_run: SimpleNamespace(
                            asset_class=AssetClass.STOCK, short=False, margin=False, option_symbol=None))


def test_entry_exceeding_cap_is_rejected_not_submitted(monkeypatch):
    _patch_exec(monkeypatch)
    client = _RecordingClient()
    records = ex.execute_routed_proposals(
        [_routed(900.0)], client=client, dry_run=False,
        daily_notional_used=200.0, max_daily_notional=1000.0,
    )
    assert records[0].submitted is False
    assert records[0].failure_category == "daily_notional_cap"
    assert "Daily notional cap reached" in records[0].detail
    assert client.submitted == []  # nothing sent


# --- 5) post-submit reconciliation blocks a subsequent excess order -----------


def test_post_submit_running_total_blocks_next_order(monkeypatch):
    _patch_exec(monkeypatch)
    client = _RecordingClient()
    # Each order is $600; cap $1000. First submits (used->600), second would hit
    # 1200 > 1000 and is rejected by the running-total reconciliation.
    records = ex.execute_routed_proposals(
        [_routed(600.0, conf=0.9, symbol="AAA"), _routed(600.0, conf=0.1, symbol="BBB")],
        client=client, dry_run=False, daily_notional_used=0.0, max_daily_notional=1000.0,
    )
    submitted = [r for r in records if r.submitted]
    rejected = [r for r in records if not r.submitted]
    assert len(submitted) == 1 and len(rejected) == 1
    assert len(client.submitted) == 1
    assert "Daily notional cap reached" in rejected[0].detail


# --- 6) sell-to-close follows the same consistent notional policy -------------


def _limits(**over):
    base = PortfolioLimits(enable_paper_sell_to_close=True, max_daily_notional_per_team=1000.0)
    from dataclasses import replace
    return replace(base, **over)


class _StcClient:
    def __init__(self):
        self.sent = []

    def has_credentials(self):
        return True

    def submit_paper_sell_to_close_order(self, order):
        self.sent.append(order)
        return SimpleNamespace(id="stc")


def _pos(symbol, qty, price):
    return {"symbol": symbol, "qty": qty, "side": "long", "current_price": price,
            "avg_entry_price": price, "market_value": qty * price}


def test_sell_to_close_counts_toward_cap_and_is_rejected_when_over(monkeypatch):
    monkeypatch.setattr(pe, "is_engaged", lambda _p=None: False)
    client = _StcClient()
    # Exit sells 100 shares @ $100 = $10,000 notional; cap is $1,000 -> rejected.
    records = pe.execute_sell_to_close(
        [pe.PositionActionProposal(symbol="AAPL", action="exit")],
        client=client, dry_run=False, limits=_limits(),
        refresh_positions=lambda: [_pos("AAPL", 100, 100.0)],
        daily_notional_used=0.0,
    )
    assert records[0].submitted is False
    assert "Daily notional cap reached" in records[0].detail
    assert client.sent == []  # never submitted


def test_sell_to_close_submits_when_within_cap(monkeypatch):
    monkeypatch.setattr(pe, "is_engaged", lambda _p=None: False)
    client = _StcClient()
    records = pe.execute_sell_to_close(
        [pe.PositionActionProposal(symbol="AAPL", action="exit")],
        client=client, dry_run=False, limits=_limits(max_daily_notional_per_team=1_000_000.0),
        refresh_positions=lambda: [_pos("AAPL", 100, 100.0)],
        daily_notional_used=0.0,
    )
    assert records[0].submitted is True
    assert len(client.sent) == 1


# --- 8) diagnostics / reconciliation carry no secrets -------------------------


def test_reconciliation_dataclass_has_no_secrets():
    import json
    rec = dn.NotionalReconciliation(used=1234.5, source="broker", status="ok")
    blob = json.dumps(rec.as_dict()).lower()
    for needle in ("secret", "api_key", "token", "password", "bearer"):
        assert needle not in blob


def test_diagnostic_report_with_notional_has_no_secrets_and_shows_fields():
    from src.competition.loop_diagnostics import (
        TeamLoopFacts, classify_diagnosis, format_team_report,
    )
    facts = TeamLoopFacts(
        team_id="team_alpha", local_iso="2026-06-29T09:35:00-04:00",
        ny_iso="2026-06-29T09:35:00-04:00", market_is_open=True,
        account_ok=True, account_classification="ok",
        equity=1_000_000.0, cash=500_000.0, buying_power=800_000.0,
        orders_today=2, max_daily_orders_per_team=1000,
        daily_notional_today=12_345.67, max_daily_notional_per_team=1_000_000.0,
        daily_notional_source="broker", daily_notional_reconciliation_status="ok",
    )
    report = format_team_report(facts, classify_diagnosis(facts))
    assert "daily_notional_today=$12,345.67 / max_daily_notional_per_team=$1,000,000.00" in report
    assert "source=broker" in report
    assert "reconciliation_status=ok" in report
    low = report.lower()
    for needle in ("secret", "api_key", "apikey", "authorization", "bearer", "password"):
        assert needle not in low
