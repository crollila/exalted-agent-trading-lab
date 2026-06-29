"""Tests for weekly synthesis + EOD Discord channel fallback (Phase 7W)."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

from types import SimpleNamespace

import src.main as main
from src.competition.memory_config import MemoryConfig
from src.competition.playbook import TeamPlaybook
from src.competition.position_review import build_team_portfolio_review
from src.competition.weekly_synthesis import (
    build_weekly_review,
    render_weekly_discord,
    render_weekly_markdown,
)
from src.config.portfolio_limits import PortfolioLimits

NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


def _review(team="team_alpha"):
    return build_team_portfolio_review(
        team, equity=80_000, cash=-150_000, buying_power=0.0,
        raw_positions=[
            {"symbol": "NVDA", "qty": 1000, "side": "long", "avg_entry_price": 200.0,
             "current_price": 160.0, "market_value": 160000.0, "cost_basis": 200000.0,
             "unrealized_pl": -40000.0, "unrealized_plpc": -0.20},
        ],
        attribution_entries=[],
        limits=PortfolioLimits(),
    )


def test_weekly_synthesis_never_submits_orders():
    # Structural guarantee: the weekly module never touches a broker/submit path.
    import src.competition.weekly_synthesis as ws
    src = inspect.getsource(ws)
    assert "submit_order" not in src
    assert "AlpacaClientWrapper" not in src


def test_weekly_synthesis_promotes_and_is_reportable():
    pb = TeamPlaybook(team_id="team_alpha")
    weekly = build_weekly_review(
        "team_alpha", review=_review(), recent_daily=[], recent_learnings=[],
        playbook=pb, config=MemoryConfig(), now=NOW,
    )
    # High-impact, evidence-backed lessons (capital exhaustion) get promoted.
    assert weekly.promoted_lessons
    md = render_weekly_markdown(weekly)
    assert "Weekly review" in md
    disc = render_weekly_discord(weekly)
    assert "Paper-only research summary. No live trading." in disc


def test_weekly_recurring_theme_requires_repeats():
    pb = TeamPlaybook(team_id="team_beta")
    learnings = [
        {"mistakes_or_missed": ["Chased a breakout that failed"]},
        {"mistakes_or_missed": ["Chased a breakout that failed"]},
        {"mistakes_or_missed": ["Chased a breakout that failed"]},
    ]
    weekly = build_weekly_review(
        "team_beta",
        review=build_team_portfolio_review(
            "team_beta", equity=100_000, cash=50_000, buying_power=50_000,
            raw_positions=[], attribution_entries=[], limits=PortfolioLimits()),
        recent_daily=[], recent_learnings=learnings, playbook=pb, config=MemoryConfig(), now=NOW,
    )
    # A theme appearing across 3 days is high-impact -> promoted.
    assert any("breakout" in p.lower() for p in weekly.promoted_lessons)


def test_weekly_review_has_no_secrets():
    pb = TeamPlaybook(team_id="team_alpha")
    weekly = build_weekly_review(
        "team_alpha", review=_review(), recent_daily=[], recent_learnings=[],
        playbook=pb, config=MemoryConfig(), now=NOW,
    )
    blob = json.dumps(weekly.as_dict()).lower()
    for needle in ("secret", "api_key", "token", "password", "bearer"):
        assert needle not in blob


# --- EOD Discord channel fallback --------------------------------------------


def _fake_config(*, enabled=True, special=None, teams=None, token="tok"):
    return SimpleNamespace(
        enabled=enabled,
        special_channel_ids=special or {},
        team_channel_ids=teams or {},
        token=token,
    )


def test_eod_prefers_paper_trading_log_channel(monkeypatch):
    sent = {}
    monkeypatch.setattr(main, "_discord_iteration_update_config",
                        lambda: _fake_config(special={"paper_trading_log": 999},
                                             teams={"team_alpha": 111}))
    import src.discord_bot.competition_updates as cu
    monkeypatch.setattr(cu, "_http_send", lambda ch, msg, tok: sent.update(channel=ch))
    ok, label = main._send_eod_to_discord("team_alpha", "hi")
    assert ok is True
    assert label == "paper_trading_log"
    assert sent["channel"] == 999


def test_eod_falls_back_to_team_channel(monkeypatch):
    sent = {}
    monkeypatch.setattr(main, "_discord_iteration_update_config",
                        lambda: _fake_config(special={}, teams={"team_alpha": 111}))
    import src.discord_bot.competition_updates as cu
    monkeypatch.setattr(cu, "_http_send", lambda ch, msg, tok: sent.update(channel=ch))
    ok, label = main._send_eod_to_discord("team_alpha", "hi")
    assert ok is True
    assert label == "team_alpha_channel"
    assert sent["channel"] == 111


def test_eod_not_sent_when_disabled(monkeypatch):
    monkeypatch.setattr(main, "_discord_iteration_update_config",
                        lambda: _fake_config(enabled=False))
    ok, label = main._send_eod_to_discord("team_alpha", "hi")
    assert ok is False and label == "disabled"
