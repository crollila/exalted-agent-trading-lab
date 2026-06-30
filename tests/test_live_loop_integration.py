"""Live-loop integration tests (Phase 7X): bounded prompt + portfolio-before-buy."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import src.main as main
from src.competition.llm_cycle import build_llm_context


# --- 1) live proposal prompt uses bounded context, excludes raw audit floods ---


def test_live_prompt_includes_bounded_memory_and_excludes_raw_audit():
    ctx = build_llm_context("team_alpha", client=None, price_fn=None)
    assert "bounded_memory" in ctx
    assert "memory_metadata" in ctx
    bm = ctx["bounded_memory"]
    # The bounded block declares what it excludes and contains no raw audit JSONL.
    assert "raw_audit" in bm["_bounds"]["excludes"]
    blob = json.dumps(bm)
    assert "iterations.jsonl" not in blob
    assert "chat_history" not in json.dumps(bm.get("working_memory", {}))


def test_prompt_memory_metadata_has_required_fields():
    ctx = build_llm_context("team_alpha", client=None, price_fn=None)
    meta = ctx["memory_metadata"]
    for key in ("daily_summaries_included", "lesson_ids_included", "scorecard_included",
                "bounded_context_chars", "malformed_or_unavailable"):
        assert key in meta
    assert isinstance(meta["bounded_context_chars"], int)


# --- 2/3/4) portfolio review runs before new entries; blocked -> review only ---


@pytest.fixture
def loop_recorder(monkeypatch):
    """Stub the loop's heavy steps and record the order of management vs new-buy."""

    calls: list[str] = []

    monkeypatch.setattr(main, "read_kill_switch",
                        lambda: SimpleNamespace(engaged=False, describe=lambda: ""))
    monkeypatch.setattr(main, "run_refresh_proposal_attribution", lambda *a, **k: None)
    monkeypatch.setattr(main, "run_week_competition_status", lambda *a, **k: None)
    monkeypatch.setattr(main, "run_export_team_scorecards", lambda *a, **k: None)
    monkeypatch.setattr(main, "_post_discord_iteration_update", lambda **k: None)
    monkeypatch.setattr(main, "_post_discord_competition_summary", lambda **k: None)
    monkeypatch.setattr(main, "_write_iteration_audit", lambda **k: None)
    monkeypatch.setattr(main, "_maybe_auto_send_eod", lambda *a, **k: None)
    monkeypatch.setattr(main, "_maybe_run_weekly", lambda *a, **k: None)
    monkeypatch.setattr(main, "_cheap_loop_market_open", lambda: True)

    def _gate(tid):
        return (SimpleNamespace(should_run_full_cycle=True, recommend_review_only=False,
                                reason="full"), None)
    monkeypatch.setattr(main, "_evaluate_team_cheap_gate", _gate)

    def _week_cycle(team, proposal_source=None, review_only=False, **kwargs):
        calls.append(f"week_cycle:{team}:review_only={review_only}")
    monkeypatch.setattr(main, "run_week_cycle_cli", _week_cycle)

    return calls


def test_portfolio_management_runs_before_new_buys(loop_recorder, monkeypatch):
    def _mgmt(tid, settings, *, dry_run_loop, kill_switch_engaged):
        loop_recorder.append(f"portfolio_management:{tid}")
        return {"new_buys_blocked": False, "recommended": "trim:NVDA", "submitted": 1,
                "eligible": True, "rejected_reason": None, "new_buys_blocked_reason": None}
    monkeypatch.setattr(main, "_run_portfolio_management", _mgmt)

    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=False,
                                    dry_run_loop=False, sleep_fn=lambda s: None)
    # Management must be invoked before the new-buy cycle for the team.
    assert loop_recorder == ["portfolio_management:team_alpha", "week_cycle:team_alpha:review_only=False"]


def test_blocked_health_runs_review_only_no_new_buy(loop_recorder, monkeypatch):
    def _mgmt(tid, settings, *, dry_run_loop, kill_switch_engaged):
        return {"new_buys_blocked": True, "recommended": "exit:LOSS", "submitted": 1,
                "eligible": True, "rejected_reason": None,
                "new_buys_blocked_reason": "Zero buying power"}
    monkeypatch.setattr(main, "_run_portfolio_management", _mgmt)

    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=False,
                                    dry_run_loop=False, sleep_fn=lambda s: None)
    # When health blocks new buys, the new-buy cycle runs review-only (no new orders).
    assert loop_recorder == ["week_cycle:team_alpha:review_only=True"]
