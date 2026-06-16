"""Phase 7S: Discord team-thought updates per cheap-loop iteration.

Mocked Discord sender only — no real Discord API calls, no network, no secrets.
Proves the briefs build safely from local artifacts, posting rules gate by mode /
market state, secrets are redacted, and Discord failures never crash the loop.
"""

from __future__ import annotations

import pytest

import src.main as main
from src.competition.cycle_gate import GateDecision
from src.discord_bot.competition_updates import (
    ACTION_CHEAP_SKIP,
    ACTION_FULL_CYCLE,
    ACTION_MARKET_CLOSED,
    ACTION_REVIEW_ONLY,
    DiscordIterationUpdateConfig,
    build_competition_iteration_summary,
    build_team_iteration_update,
    compact_list,
    compact_sentence,
    gather_team_iteration_context,
    iteration_updates_status,
    post_competition_iteration_summary,
    post_team_iteration_update,
    redact_secrets,
    should_post_for_action,
    summarize_memory_for_discord,
    truncate_discord_message,
)

SECRET_TOKEN = "MT234567890abcdefABCDEF.GhIjKl.mnopQRSTuvwx-yz0123456789ABCD"
SECRET_KEY = "sk-THISisASECRETkeyNeverPrint123456"


class FakeSender:
    """Records sends; optionally raises to simulate Discord failures."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[int, str, str]] = []
        self.fail = fail

    def __call__(self, channel_id: int, message: str, token: str) -> None:
        self.calls.append((channel_id, message, token))
        if self.fail:
            raise RuntimeError("simulated Discord API failure")


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "ENABLE_DISCORD_ITERATION_UPDATES": "true",
        "DISCORD_BOT_TOKEN": "test-bot-token-1234567890",
        "DISCORD_TEAM_ALPHA_CHANNEL_ID": "111",
        "DISCORD_TEAM_BETA_CHANNEL_ID": "222",
        "DISCORD_TOURNAMENT_RESULTS_CHANNEL_ID": "333",
        "DISCORD_UPDATE_MIN_INTERVAL_SECONDS": "0",
    }
    env.update(overrides)
    return env


def _cfg(**overrides: str) -> DiscordIterationUpdateConfig:
    return DiscordIterationUpdateConfig.from_env(_env(**overrides))


def _gate(reason: str = "Minimum interval elapsed; full cycle recommended.", review_only: bool = False) -> GateDecision:
    return GateDecision(
        team_id="team_alpha",
        should_run_full_cycle=True,
        reason=reason,
        recommend_review_only=review_only,
        trigger_flags=["interval_elapsed", "mode:exploration"],
    )


def _full_ctx(team_id: str = "team_alpha") -> dict:
    return {
        "team_id": team_id,
        "iteration": 3,
        "cycle_action": ACTION_FULL_CYCLE,
        "market_state": "open",
        "kill_switch_engaged": False,
        "mode": "exploration",
        "gate_reason": "Minimum interval elapsed (31m >= 30m); full cycle recommended.",
        "pm_decision": "reduce_gross_exposure",
        "pm_no_trade": True,
        "pm_max_new": 0,
        "team_return": 0.0,
        "spy_return": 0.014,
        "excess_return": -0.014,
        "spy_relative_result": "trailing SPY",
        "why_vs_spy": "short exposure lagged",
        "hypothesis": "favor index/megacap leadership",
        "weakest_symbol": "XYZ",
        "strongest_symbol": "SPY",
        "proposals_count": 2,
        "approved_count": 1,
        "rejected_count": 1,
        "simulation_only_count": 0,
        "orders_submitted": 1,
        "broker_rejected_count": 0,
        "worked_count": 4,
        "failed_count": 12,
        "mixed_count": 0,
        "keep_doing": ["hold SPY"],
        "stop_doing": ["add shorts"],
        "test_next": ["rotate weakest"],
        "watchlist": ["NVDA", "META"],
        "avoid_next_cycle": ["insufficient buying power buys"],
        "llm_model_used": "gpt-test-strategy",
    }


# --- message builders -------------------------------------------------------


def test_builds_alpha_update_with_gate_pm_attribution_and_thesis():
    msg = build_team_iteration_update("team_alpha", _full_ctx("team_alpha"))
    assert "Team Alpha - Iteration Brief (iteration 3)" in msg
    assert "Cycle decision: Full strategy cycle" in msg
    assert "Minimum interval elapsed" in msg  # gate "why"
    assert "Portfolio Manager: reduce_gross_exposure" in msg
    assert "no_trade=True" in msg
    assert "favor index/megacap leadership" in msg  # thesis
    assert "Worked: 4" in msg and "Failed: 12" in msg  # attribution/learning
    assert "Submitted paper orders: 1" in msg
    assert "Model: gpt-test-strategy" in msg
    assert "Paper-only. LLMs do not execute trades." in msg


def test_builds_beta_update():
    ctx = _full_ctx("team_beta")
    ctx["cycle_action"] = ACTION_REVIEW_ONLY
    msg = build_team_iteration_update("team_beta", ctx)
    assert "Team Beta - Iteration Brief" in msg
    assert "Cycle decision: Review-only" in msg


def test_missing_data_produces_na_not_crash():
    msg = build_team_iteration_update("team_alpha", {"team_id": "team_alpha", "cycle_action": ACTION_CHEAP_SKIP})
    assert "n/a" in msg
    assert "Cycle decision: Cheap skip" in msg
    # No exception, and the safety badge is always present.
    assert "Paper-only" in msg


def test_summary_builds_leader_and_scoreboard_safely(monkeypatch):
    import src.discord_bot.competition_updates as cu

    class _Card:
        def __init__(self, equity, ret, excess, spy):
            self.current_equity = equity
            self.team_return = ret
            self.excess_return_vs_spy = excess
            self.spy_benchmark_return = spy

    cards = {
        "team_alpha": _Card(972000.0, 0.0, -0.014, 0.014),
        "team_beta": _Card(1002000.0, 0.002, -0.012, 0.014),
    }
    monkeypatch.setattr(cu, "load_latest_scorecard", lambda team_id: cards.get(team_id), raising=False)
    # load_latest_scorecard is imported inside the function from the scorecard module.
    monkeypatch.setattr("src.competition.scorecard.load_latest_scorecard", lambda team_id: cards.get(team_id))

    msg = build_competition_iteration_summary(kill_switch_engaged=False, next_wake_seconds=3600)
    assert "Leader: Team Beta" in msg  # higher (less negative) excess
    assert "SPY return: +0.0140" in msg
    assert "Paper-only" in msg
    assert "Next scheduled wake: ~3600s" in msg


def test_summary_safe_when_no_scorecards(monkeypatch):
    monkeypatch.setattr("src.competition.scorecard.load_latest_scorecard", lambda team_id: None)
    msg = build_competition_iteration_summary()
    assert "Leader: n/a" in msg


# --- brief-mode compaction (Phase 7S follow-up) -----------------------------

_HUGE_MEMORY_PARAGRAPH = (
    "The team rotated heavily out of energy and into megacap technology after a "
    "string of disappointing oil prints, then re-added a small short hedge against "
    "regional banks, then reconsidered because the hedge bled carry, and meanwhile "
    "the portfolio manager flagged that gross exposure had crept above target so "
    "several discretionary buys were deferred to the following session entirely. "
) * 4


def _brief_cfg(**overrides: str):
    return DiscordIterationUpdateConfig.from_env(_env(DISCORD_ITERATION_UPDATE_STYLE="brief", **overrides))


def test_compact_sentence_caps_and_collapses_whitespace():
    out = compact_sentence("first sentence here. second sentence ignored.", 180)
    assert out == "first sentence here."
    long = compact_sentence("word " * 200, 180)
    assert len(long) <= 180
    assert "\n" not in compact_sentence("line one\nline two\nline three", 180)
    assert compact_sentence("", 180) == "n/a"
    assert compact_sentence(None, 180) == "n/a"


def test_compact_list_caps_items_with_overflow_suffix():
    assert compact_list(["NVDA", "META", "AAPL", "AMZN"], 2) == "NVDA, META (+2)"
    assert compact_list([], 2, fallback="n/a") == "n/a"
    assert compact_list(["   "], 2, fallback="none") == "none"


def test_summarize_memory_flags_contradictions():
    class _Mem:
        symbols_to_favor = ["NVDA", "META"]
        symbols_to_avoid = ["META", "TSLA"]
        sectors_to_favor: list = []
        sectors_to_avoid: list = []
        compact_summary = _HUGE_MEMORY_PARAGRAPH
        recurring_winning_patterns: list = []

    out = summarize_memory_for_discord(_Mem(), 180)
    assert out == "Memory has mixed signals; reconcile before next full cycle."
    assert _HUGE_MEMORY_PARAGRAPH not in out


def test_summarize_memory_compacts_long_summary():
    mem = {"compact_summary": _HUGE_MEMORY_PARAGRAPH, "symbols_to_favor": [], "symbols_to_avoid": []}
    out = summarize_memory_for_discord(mem, 180)
    assert len(out) <= 180
    assert _HUGE_MEMORY_PARAGRAPH not in out


def test_brief_mode_does_not_dump_raw_memory_paragraph():
    ctx = _full_ctx("team_alpha")
    ctx["compact_summary"] = _HUGE_MEMORY_PARAGRAPH
    ctx["memory_summary"] = _HUGE_MEMORY_PARAGRAPH  # simulate an unsummarized artifact
    msg = build_team_iteration_update("team_alpha", ctx, style="brief")
    assert _HUGE_MEMORY_PARAGRAPH not in msg
    # The "What changed" line stays one compact, capped bullet.
    what_changed = next(line for line in msg.splitlines() if line.startswith("- What changed:"))
    assert len(what_changed) <= len("- What changed: ") + 180


def test_brief_mode_compacts_long_what_changed_and_why():
    ctx = _full_ctx("team_alpha")
    ctx["gate_reason"] = _HUGE_MEMORY_PARAGRAPH
    ctx["why_vs_spy"] = _HUGE_MEMORY_PARAGRAPH
    ctx["hypothesis"] = _HUGE_MEMORY_PARAGRAPH
    msg = build_team_iteration_update("team_alpha", ctx, style="brief")
    assert _HUGE_MEMORY_PARAGRAPH not in msg
    for prefix in ("Why:", "- Why vs SPY:", "- Strongest thesis:"):
        line = next(line for line in msg.splitlines() if line.startswith(prefix))
        assert len(line) <= len(prefix) + 1 + 180


def test_brief_mode_contradictory_memory_becomes_warning():
    ctx = _full_ctx("team_alpha")
    ctx["symbols_to_favor"] = ["NVDA", "META"]
    ctx["symbols_to_avoid"] = ["META"]
    ctx["compact_summary"] = _HUGE_MEMORY_PARAGRAPH
    msg = build_team_iteration_update("team_alpha", ctx, style="brief")
    assert "- What changed: Memory has mixed signals; reconcile before next full cycle." in msg
    assert _HUGE_MEMORY_PARAGRAPH not in msg


def test_brief_mode_message_stays_under_max_chars():
    ctx = _full_ctx("team_alpha")
    # Stuff every free-text and list field with oversized content.
    ctx["gate_reason"] = _HUGE_MEMORY_PARAGRAPH
    ctx["why_vs_spy"] = _HUGE_MEMORY_PARAGRAPH
    ctx["hypothesis"] = _HUGE_MEMORY_PARAGRAPH
    ctx["compact_summary"] = _HUGE_MEMORY_PARAGRAPH
    ctx["keep_doing"] = [f"keep doing thing number {i}" for i in range(50)]
    ctx["stop_doing"] = [f"stop doing thing number {i}" for i in range(50)]
    ctx["watchlist"] = [f"SYM{i}" for i in range(50)]
    ctx["test_next"] = [f"test {i}" for i in range(50)]
    ctx["avoid_next_cycle"] = [f"avoid pattern {i}" for i in range(50)]
    msg = build_team_iteration_update("team_alpha", ctx, style="brief")
    cfg = _brief_cfg()
    # Even with every free-text/list field maxed out, the brief stays under the
    # hard cap (no truncation needed) because each section is independently capped.
    assert len(msg) <= cfg.max_chars
    # Realistic content lands in the compact target band (~900-1300 chars).
    realistic = build_team_iteration_update("team_alpha", _full_ctx("team_alpha"), style="brief")
    assert len(realistic) <= 1300


def test_brief_mode_no_secrets_in_built_message():
    ctx = _full_ctx("team_alpha")
    ctx["gate_reason"] = f"interval elapsed; token={SECRET_TOKEN}"
    ctx["why_vs_spy"] = f"key leaked {SECRET_KEY}"
    msg = redact_secrets(build_team_iteration_update("team_alpha", ctx, style="brief"))
    assert SECRET_TOKEN not in msg
    assert SECRET_KEY not in msg


# --- truncation + redaction -------------------------------------------------


def test_truncates_long_messages_under_limit():
    long = "x" * 5000
    out = truncate_discord_message(long, 1800)
    assert len(out) <= 1800
    assert out.endswith("(truncated)")


def test_redacts_token_like_strings_and_keys():
    text = f"token={SECRET_TOKEN} key={SECRET_KEY} authorization: Bot {SECRET_TOKEN}"
    out = redact_secrets(text, env={"DISCORD_BOT_TOKEN": SECRET_TOKEN})
    assert SECRET_TOKEN not in out
    assert SECRET_KEY not in out
    assert "[REDACTED]" in out


def test_redacts_env_secret_values():
    out = redact_secrets("my OPENAI_API_KEY is supersecretvalue123", env={"OPENAI_API_KEY": "supersecretvalue123"})
    assert "supersecretvalue123" not in out


# --- posting rules ----------------------------------------------------------


def test_market_closed_does_not_post_by_default():
    should, _ = should_post_for_action(_cfg(), cycle_action=ACTION_FULL_CYCLE, market_state="closed")
    assert should is False


def test_market_closed_posts_only_when_enabled():
    cfg = _cfg(DISCORD_POST_WHEN_MARKET_CLOSED="true")
    should, _ = should_post_for_action(cfg, cycle_action=ACTION_FULL_CYCLE, market_state="closed")
    assert should is True


def test_full_cycle_posts_when_enabled():
    should, _ = should_post_for_action(_cfg(), cycle_action=ACTION_FULL_CYCLE, market_state="open")
    assert should is True
    should_off, _ = should_post_for_action(
        _cfg(DISCORD_POST_FULL_CYCLE="false"), cycle_action=ACTION_FULL_CYCLE, market_state="open"
    )
    assert should_off is False


def test_review_only_posts_when_enabled():
    should, _ = should_post_for_action(_cfg(), cycle_action=ACTION_REVIEW_ONLY, market_state="open")
    assert should is True


def test_cheap_skip_does_not_post_by_default():
    should, _ = should_post_for_action(_cfg(), cycle_action=ACTION_CHEAP_SKIP, market_state="open")
    assert should is False
    should_on, _ = should_post_for_action(
        _cfg(DISCORD_POST_CHEAP_SKIP="true"), cycle_action=ACTION_CHEAP_SKIP, market_state="open"
    )
    assert should_on is True


def test_disabled_never_posts():
    should, reason = should_post_for_action(
        _cfg(ENABLE_DISCORD_ITERATION_UPDATES="false"), cycle_action=ACTION_FULL_CYCLE, market_state="open"
    )
    assert should is False
    assert "disabled" in reason


# --- send integration (mocked sender) ---------------------------------------


def test_full_cycle_sends_to_team_channel(tmp_path):
    sender = FakeSender()
    result = post_team_iteration_update(
        "team_alpha",
        iteration=1,
        cycle_action=ACTION_FULL_CYCLE,
        gate_decision=_gate(),
        market_state="open",
        kill_switch_engaged=False,
        config=_cfg(),
        sender=sender,
        state_path=tmp_path / "state.json",
    )
    assert result["sent"] is True
    assert len(sender.calls) == 1
    channel_id, message, token = sender.calls[0]
    assert channel_id == 111
    assert "Team Alpha - Iteration Brief" in message


def test_send_failure_does_not_crash(tmp_path, capsys):
    sender = FakeSender(fail=True)
    result = post_team_iteration_update(
        "team_alpha",
        cycle_action=ACTION_FULL_CYCLE,
        gate_decision=_gate(),
        market_state="open",
        config=_cfg(),
        sender=sender,
        state_path=tmp_path / "state.json",
    )
    assert result["sent"] is False
    assert result["reason"] == "send_failed"
    out = capsys.readouterr().out
    assert "Discord warning" in out
    # The recorded last-error state is also redacted-safe.
    status = iteration_updates_status(_cfg(), state_path=tmp_path / "state.json")
    assert status["teams"]["team_alpha"]["last_error"] is not None


def test_cheap_skip_send_is_skipped_by_default(tmp_path):
    sender = FakeSender()
    result = post_team_iteration_update(
        "team_alpha",
        cycle_action=ACTION_CHEAP_SKIP,
        gate_decision=_gate(),
        market_state="open",
        config=_cfg(),
        sender=sender,
        state_path=tmp_path / "state.json",
    )
    assert result["sent"] is False
    assert sender.calls == []


def test_min_interval_blocks_repeat_send(tmp_path):
    cfg = _cfg(DISCORD_UPDATE_MIN_INTERVAL_SECONDS="3600")
    sender = FakeSender()
    state = tmp_path / "state.json"
    first = post_team_iteration_update(
        "team_alpha", cycle_action=ACTION_FULL_CYCLE, gate_decision=_gate(),
        market_state="open", config=cfg, sender=sender, state_path=state,
    )
    second = post_team_iteration_update(
        "team_alpha", cycle_action=ACTION_FULL_CYCLE, gate_decision=_gate(),
        market_state="open", config=cfg, sender=sender, state_path=state,
    )
    assert first["sent"] is True
    assert second["sent"] is False
    assert "min interval" in second["reason"]
    assert len(sender.calls) == 1


def test_summary_sends_to_summary_channel(tmp_path, monkeypatch):
    monkeypatch.setattr("src.competition.scorecard.load_latest_scorecard", lambda team_id: None)
    sender = FakeSender()
    result = post_competition_iteration_summary(
        config=_cfg(), sender=sender, kill_switch_engaged=False, state_path=tmp_path / "state.json"
    )
    assert result["sent"] is True
    assert sender.calls[0][0] == 333


# --- dry-run CLI ------------------------------------------------------------


def test_dry_run_cli_prints_message_and_does_not_send(monkeypatch, capsys):
    monkeypatch.setenv("ENABLE_DISCORD_ITERATION_UPDATES", "true")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("DISCORD_TEAM_ALPHA_CHANNEL_ID", "111")

    sent: list = []
    monkeypatch.setattr(
        "src.discord_bot.competition_updates._http_send",
        lambda *a, **k: sent.append(a),
    )

    main.run_discord_iteration_update(team="team_alpha", dry_run=True)
    out = capsys.readouterr().out
    assert "Iteration Brief" in out
    assert sent == []  # dry-run never calls the real sender
    assert SECRET_TOKEN not in out  # no secrets printed


# --- Phase 7S.2: scoreboard posts at most once per loop iteration -----------


def _stub_loop_heavy(monkeypatch):
    """Stub the loop's heavy/real steps so only the Discord wiring exercises."""
    from types import SimpleNamespace

    monkeypatch.setattr(main, "read_kill_switch", lambda: SimpleNamespace(engaged=False, describe=lambda: ""))
    monkeypatch.setattr(main, "run_refresh_proposal_attribution", lambda *a, **k: None)
    monkeypatch.setattr(main, "run_week_competition_status", lambda *a, **k: None)
    monkeypatch.setattr(main, "run_export_team_scorecards", lambda *a, **k: None)
    monkeypatch.setattr(main, "run_week_cycle_cli", lambda *a, **k: None)
    monkeypatch.setattr(main, "routing_status", lambda *a, **k: {})

    def _gate(tid):
        return (
            GateDecision(
                team_id=tid,
                should_run_full_cycle=True,
                reason="full cycle",
                recommend_review_only=False,
                trigger_flags=["mode:exploration"],
            ),
            None,
        )

    monkeypatch.setattr(main, "_evaluate_team_cheap_gate", _gate)


def _record_http_sends(monkeypatch, tmp_path):
    """Redirect dedup state to tmp and record every (channel_id, message) send."""
    import src.discord_bot.competition_updates as cu

    monkeypatch.setattr(cu, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(cu, "_http_send", lambda channel_id, message, token: calls.append((channel_id, message)))
    return calls


def _set_loop_env(monkeypatch, **overrides):
    for key, value in _env(**overrides).items():
        monkeypatch.setenv(key, value)


def test_loop_both_posts_two_team_updates_and_one_summary(monkeypatch, tmp_path):
    _set_loop_env(monkeypatch)
    _stub_loop_heavy(monkeypatch)
    calls = _record_http_sends(monkeypatch, tmp_path)

    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=False, dry_run_loop=False)

    channels = [c for c, _ in calls]
    assert channels.count(111) == 1  # team_alpha brief
    assert channels.count(222) == 1  # team_beta brief
    assert channels.count(333) == 1  # scoreboard summary exactly once
    assert len(calls) == 3


def test_loop_summary_not_posted_inside_team_loop(monkeypatch, tmp_path):
    _set_loop_env(monkeypatch)
    _stub_loop_heavy(monkeypatch)
    calls = _record_http_sends(monkeypatch, tmp_path)

    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=False, dry_run_loop=False)

    channels = [c for c, _ in calls]
    # Exactly one summary, and it lands after both team briefs (not interleaved).
    assert channels.count(333) == 1
    assert channels.index(333) == len(channels) - 1


def test_loop_summary_disabled_posts_zero_summaries(monkeypatch, tmp_path):
    _set_loop_env(monkeypatch, DISCORD_POST_COMPETITION_SUMMARY="false")
    _stub_loop_heavy(monkeypatch)
    calls = _record_http_sends(monkeypatch, tmp_path)

    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=False, dry_run_loop=False)

    channels = [c for c, _ in calls]
    assert channels.count(333) == 0
    assert channels.count(111) == 1 and channels.count(222) == 1


def test_loop_single_team_does_not_spam_summaries(monkeypatch, tmp_path):
    _set_loop_env(monkeypatch)
    _stub_loop_heavy(monkeypatch)
    calls = _record_http_sends(monkeypatch, tmp_path)

    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=False, dry_run_loop=False)

    channels = [c for c, _ in calls]
    assert channels.count(333) == 0  # no head-to-head scoreboard for a single team
    assert channels.count(111) == 1
    assert channels.count(222) == 0


def test_loop_summary_failure_does_not_crash(monkeypatch, tmp_path, capsys):
    _set_loop_env(monkeypatch)
    _stub_loop_heavy(monkeypatch)
    import src.discord_bot.competition_updates as cu

    monkeypatch.setattr(cu, "DEFAULT_STATE_PATH", tmp_path / "state.json")

    def _boom(channel_id, message, token):
        if channel_id == 333:
            raise RuntimeError("simulated summary API failure")

    monkeypatch.setattr(cu, "_http_send", _boom)

    # Must complete the iteration without raising.
    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=False, dry_run_loop=False)
    out = capsys.readouterr().out
    assert "Cheap competition loop iteration 1" in out


def test_summary_dedup_skips_repeat_in_same_iteration(tmp_path):
    cfg = _cfg()
    sender = FakeSender()
    state = tmp_path / "state.json"

    first = post_competition_iteration_summary(config=cfg, sender=sender, iteration=7, state_path=state)
    second = post_competition_iteration_summary(config=cfg, sender=sender, iteration=7, state_path=state)
    assert first["sent"] is True
    assert second["sent"] is False
    assert "already posted this iteration" in second["reason"]
    assert len(sender.calls) == 1

    # A later iteration is free to post again.
    third = post_competition_iteration_summary(config=cfg, sender=sender, iteration=8, state_path=state)
    assert third["sent"] is True
    assert len(sender.calls) == 2


def test_gather_context_degrades_safely(monkeypatch):
    # Even if a loader raises, gathering must not crash and must return a dict.
    monkeypatch.setattr(
        "src.competition.scorecard.load_latest_scorecard",
        lambda team_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ctx = gather_team_iteration_context("team_alpha", cycle_action=ACTION_CHEAP_SKIP)
    assert ctx["team_id"] == "team_alpha"
    msg = build_team_iteration_update("team_alpha", ctx)
    assert "Paper-only" in msg
