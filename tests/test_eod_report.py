"""Tests for the end-of-day report + daily learning artifact (Phase 7V)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

from src.competition.daily_learning import build_daily_learning, save_daily_learning
from src.competition.eod_report import (
    OrderLine,
    already_sent,
    build_eod_report,
    mark_sent,
    render_eod_discord,
    render_eod_markdown,
    save_eod_report,
    should_send_eod,
)
from src.competition.position_review import build_team_portfolio_review
from src.config.portfolio_limits import PortfolioLimits

NOW = datetime(2026, 6, 29, 21, 0, tzinfo=timezone.utc)  # 17:00 ET, after close


def _review(team="team_alpha"):
    return build_team_portfolio_review(
        team, equity=100_000, cash=20_000, buying_power=20_000,
        raw_positions=[
            {"symbol": "AAPL", "qty": 100, "side": "long", "avg_entry_price": 100.0,
             "current_price": 130.0, "market_value": 13000.0, "cost_basis": 10000.0,
             "unrealized_pl": 3000.0, "unrealized_plpc": 0.30},
            {"symbol": "LOSS", "qty": 50, "side": "long", "avg_entry_price": 100.0,
             "current_price": 78.0, "market_value": 3900.0, "cost_basis": 5000.0,
             "unrealized_pl": -1100.0, "unrealized_plpc": -0.22},
        ],
        attribution_entries=[],
        limits=PortfolioLimits(),
    )


def _report(market_is_open=False):
    return build_eod_report(
        _review(),
        starting_equity=98_000, spy_daily_return_pct=0.01,
        submitted_orders=[OrderLine("AAPL", "buy", 10, 130.0, 1300.0, "submitted", "momentum")],
        rejected_or_skipped=["LOSS: down 22%, exit"],
        learnings=["Cut losers faster."],
        thesis_changes=["LOSS: thesis invalidated"],
        next_day_watchlist=["NVDA", "MSFT"],
        market_is_open=market_is_open,
        now=NOW,
    )


# --- once-per-trading-date guard ---------------------------------------------


def test_should_send_only_when_closed(tmp_path):
    ok_closed, _ = should_send_eod("team_alpha", market_is_open=False, now=NOW, eod_dir=tmp_path)
    ok_open, _ = should_send_eod("team_alpha", market_is_open=True, now=NOW, eod_dir=tmp_path)
    assert ok_closed is True
    assert ok_open is False


def test_unknown_clock_does_not_autosend(tmp_path):
    ok, why = should_send_eod("team_alpha", market_is_open=None, now=NOW, eod_dir=tmp_path)
    assert ok is False
    assert "unknown" in why.lower()


def test_force_overrides_unknown_clock(tmp_path):
    ok, _ = should_send_eod("team_alpha", market_is_open=None, now=NOW, eod_dir=tmp_path, force=True)
    assert ok is True


def test_does_not_send_twice_for_same_team_and_date(tmp_path):
    ok1, _ = should_send_eod("team_alpha", market_is_open=False, now=NOW, eod_dir=tmp_path)
    assert ok1 is True
    mark_sent("team_alpha", "2026-06-29", eod_dir=tmp_path)
    assert already_sent("team_alpha", "2026-06-29", eod_dir=tmp_path) is True
    ok2, why = should_send_eod("team_alpha", market_is_open=False, now=NOW, eod_dir=tmp_path)
    assert ok2 is False
    assert "Already sent" in why


def test_other_team_not_blocked_by_first(tmp_path):
    mark_sent("team_alpha", "2026-06-29", eod_dir=tmp_path)
    ok, _ = should_send_eod("team_beta", market_is_open=False, now=NOW, eod_dir=tmp_path)
    assert ok is True


# --- report content ----------------------------------------------------------


def test_report_includes_required_sections():
    md = render_eod_markdown(_report())
    for section in ("Performance", "Submitted orders", "Held / watched",
                    "Winners / losers", "Learnings", "Next day"):
        assert section in md
    disc = render_eod_discord(_report())
    assert "EOD team_alpha" in disc
    assert "Paper trading only" in disc


def test_daily_pl_and_excess_computed():
    r = _report()
    assert r.daily_pl == r.ending_equity - 98_000
    assert r.daily_return_pct is not None
    assert r.excess_vs_spy_pct is not None  # daily_return - spy_daily


def test_report_orders_and_holds_present():
    r = _report()
    assert any(o.symbol == "AAPL" for o in r.submitted_orders)
    # LOSS is a deep loser -> recommended exit (not a hold); AAPL winner -> hold.
    held_syms = {h["symbol"] for h in r.held_positions}
    assert "AAPL" in held_syms


def test_closed_market_build_is_safe():
    # Building while closed must not raise and must mark the session closed.
    r = _report(market_is_open=False)
    assert r.session_status == "closed"


def test_no_secrets_in_report_and_learning(tmp_path):
    r = build_eod_report(
        _review(), starting_equity=98_000, spy_daily_return_pct=0.0,
        submitted_orders=[],
        rejected_or_skipped=["ALPACA_SECRET_KEY=leakedsecret123 should be masked"],
        learnings=["token ALPACA_API_KEY=anotherleak987 here"],
        thesis_changes=[], next_day_watchlist=[], market_is_open=False, now=NOW,
    )
    saved = save_eod_report(r, eod_dir=tmp_path)
    blob = saved["json"].read_text(encoding="utf-8")
    assert "leakedsecret123" not in blob
    assert "anotherleak987" not in blob

    learning = build_daily_learning(_review(), now=NOW)
    lpath = save_daily_learning(learning, learning_dir=tmp_path)
    ltext = lpath.read_text(encoding="utf-8")
    for needle in ("secret", "api_key", "bearer", "password"):
        assert needle not in ltext.lower()


def test_daily_learning_links_trades_and_hypotheses():
    learning = build_daily_learning(
        _review(), submitted_orders=[{"symbol": "AAPL", "side": "buy", "quantity": 10}], now=NOW,
    )
    assert learning.trades and learning.trades[0]["symbol"] == "AAPL"
    assert learning.next_day_hypotheses  # always proposes a next-day hypothesis
    assert "Research feedback only" in learning.disclaimer


def test_learning_does_not_expose_mutation_of_config():
    # The learning artifact is data only; it has no method to change limits/env.
    learning = build_daily_learning(_review(), now=NOW)
    assert not hasattr(learning, "apply")
    assert not hasattr(learning, "write_env")
