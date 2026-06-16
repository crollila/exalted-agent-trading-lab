"""Phase 7T: Tomorrow Plan artifact + strict off-hours quiet mode.

No network, no broker, no LLM, no secrets. Proves the Tomorrow Plan builds
deterministically from local artifacts (degrading to n/a on missing data),
detects mode contradictions and favor/avoid overlaps, persists JSON + Markdown
under data/reviews/, keeps Discord posting disabled by default, and that the
cheap loop goes quiet (but stays alive) outside trading hours.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import src.main as main
from src.competition.cycle_gate import GateDecision
from src.competition.daily_review import DailySpyAttribution, DailyTeamReview
from src.competition.quiet_mode import OFF_HOURS_SLEEP_NOTICE, OffHoursQuietConfig
from src.competition.tomorrow_plan import (
    MODE_CONSERVATION,
    MODE_HOLD_OBSERVE,
    MODE_RISK_REDUCTION,
    TomorrowPlan,
    build_tomorrow_plan,
    export_tomorrow_plan,
    format_tomorrow_plan_discord,
    format_tomorrow_plan_markdown,
    format_tomorrow_plan_terminal,
    post_tomorrow_plan_to_discord,
)
from src.learning.team_memory import TeamLearningLedger

SECRET_TOKEN = "MT234567890abcdefABCDEF.GhIjKl.mnopQRSTuvwx-yz0123456789ABCD"
SECRET_KEY = "sk-THISisASECRETkeyNeverPrint123456"


# --- builder fixtures -------------------------------------------------------


def _review(**overrides) -> DailyTeamReview:
    base = dict(
        team_id="team_alpha",
        date="2026-06-15",
        spy_relative_result="beat SPY by +0.0100 excess",
        helped=["NVDA (semis_ai)", "MSFT (megacap_software_cloud)"],
        hurt=["TSLA (high_beta_auto_ev)"],
        stop_doing=["adding short exposure that lags SPY"],
        keep_doing=["holding/adding NVDA"],
        test_next=["size up the semis_ai winners"],
        recommended_mode="exploration",
        watch_symbols=["NVDA", "MSFT", "TSLA"],
        reduce_churn=False,
    )
    base.update(overrides)
    return DailyTeamReview(**base)


def _attribution(**overrides) -> DailySpyAttribution:
    a = DailySpyAttribution(team_id="team_alpha")
    a.excess_return = 0.01
    a.top_winners = [{"symbol": "NVDA", "metric": 0.05, "bucket": "semis_ai"}]
    a.top_losers = [{"symbol": "TSLA", "metric": -0.03, "bucket": "high_beta_auto_ev"}]
    a.submitted_orders = 2
    a.drivers = ["outperformed"]
    for key, value in overrides.items():
        setattr(a, key, value)
    return a


def _ledger(**overrides) -> TeamLearningLedger:
    led = TeamLearningLedger(team_id="team_alpha")
    led.mode = "exploration"
    led.watchlist = ["NVDA"]
    led.avoid_next_cycle = []
    for key, value in overrides.items():
        setattr(led, key, value)
    return led


# --- 1. builds from daily review + learning + attribution -------------------


def test_build_from_review_learning_attribution():
    plan = build_tomorrow_plan(
        "team_alpha",
        _review(),
        _ledger(),
        _attribution(),
        None,
        SimpleNamespace(low_buying_power=False, max_new_proposals=3),
        generated_at="2026-06-15T00:00:00+00:00",
    )
    assert plan.team_id == "team_alpha"
    assert plan.source_date == "2026-06-15"
    assert "NVDA (semis_ai)" in plan.what_worked_today
    assert "TSLA (high_beta_auto_ev)" in plan.what_failed_today
    assert plan.keep_doing == ["holding/adding NVDA"]
    assert plan.test_tomorrow == ["size up the semis_ai winners"]
    assert "NVDA" in plan.watchlist
    # No contradiction, no risk signals -> review's exploration mode is honored.
    assert plan.recommended_mode == "exploration"
    assert plan.consistency_warning == ""
    # Safety reminder + a deterministic-gate rule are always present.
    assert "Paper-only" in plan.safety_reminder
    assert any("deterministic risk gates" in r.lower() for r in plan.tomorrow_rules)


# --- 2. missing data -> safe n/a values -------------------------------------


def test_missing_data_produces_safe_na_values():
    plan = build_tomorrow_plan("team_beta", None, None, None, None, None,
                               generated_at="2026-06-15T00:00:00+00:00")
    assert plan.equity == "n/a"
    assert plan.rank == "n/a"
    assert plan.recommended_mode == MODE_HOLD_OBSERVE
    assert plan.what_worked_today == ["no update available"]
    assert plan.what_failed_today == ["no update available"]
    assert plan.watchlist == ["n/a"]
    assert plan.avoid_list == ["n/a"]
    assert plan.risk_constraints == "n/a"
    assert plan.portfolio_manager_stance == "no update available"
    assert plan.consistency_warning == ""
    assert plan.mixed_signal_warning == ""
    # Even with nothing, the safety net rule is present.
    assert plan.tomorrow_rules
    assert "hold" in plan.executive_summary.lower()


# --- 3. contradictory mode -> consistency warning ---------------------------


def test_contradiction_produces_consistency_warning():
    # Daily review wants exploration; ledger says conservation -> disagree.
    plan = build_tomorrow_plan(
        "team_alpha",
        _review(recommended_mode="exploration"),
        _ledger(mode="conservation"),
        _attribution(),
        None,
        None,
        generated_at="2026-06-15T00:00:00+00:00",
    )
    assert plan.consistency_warning
    assert "disagree" in plan.consistency_warning
    # Safer stance chosen (no risk signals here -> conservation).
    assert plan.recommended_mode in {MODE_CONSERVATION, MODE_RISK_REDUCTION}


def test_risk_signals_force_risk_reduction():
    plan = build_tomorrow_plan(
        "team_alpha",
        _review(recommended_mode="exploration"),
        _ledger(mode="exploration"),
        _attribution(broker_rejections=2, broker_rejection_categories=["insufficient_buying_power"]),
        None,
        SimpleNamespace(low_buying_power=True),
        generated_at="2026-06-15T00:00:00+00:00",
    )
    assert plan.recommended_mode == MODE_RISK_REDUCTION
    assert any("free buying power" in r.lower() for r in plan.tomorrow_rules)


# --- 4. favor/avoid overlap -> mixed-signal warning -------------------------


def test_favor_avoid_overlap_produces_mixed_signal_warning():
    plan = build_tomorrow_plan(
        "team_alpha",
        _review(watch_symbols=["NVDA", "MSFT"], keep_doing=["holding/adding NVDA"]),
        _ledger(avoid_next_cycle=["avoid NVDA again — kept lagging SPY"]),
        _attribution(),
        None,
        None,
        generated_at="2026-06-15T00:00:00+00:00",
    )
    assert plan.mixed_signal_warning
    assert "NVDA" in plan.mixed_signal_warning


def test_no_false_mixed_signal_from_free_text():
    # Avoid list is plain English with no ticker tokens -> no false overlap.
    plan = build_tomorrow_plan(
        "team_alpha",
        _review(watch_symbols=["NVDA"]),
        _ledger(avoid_next_cycle=["Insufficient buying power; free room before new buys."]),
        _attribution(),
        None,
        None,
        generated_at="2026-06-15T00:00:00+00:00",
    )
    assert plan.mixed_signal_warning == ""


# --- 5 & 6. export writes JSON + Markdown to data/reviews path; both teams ---


def test_export_writes_json_and_markdown(tmp_path):
    reviews = tmp_path / "reviews"
    plan, (json_path, md_path) = export_tomorrow_plan(
        "team_alpha",
        scorecard_dir=tmp_path / "scorecards",
        attribution_dir=tmp_path / "attribution",
        learning_dir=tmp_path / "learning",
        reviews_dir=reviews,
        competition_status=None,
    )
    assert json_path.exists() and md_path.exists()
    assert json_path.name == "team_alpha_tomorrow_plan_latest.json"
    assert md_path.name == "team_alpha_tomorrow_plan_latest.md"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["team_id"] == "team_alpha"
    assert "Tomorrow Plan" in md_path.read_text(encoding="utf-8")


def test_export_both_creates_both_plans(tmp_path):
    reviews = tmp_path / "reviews"
    for team in ("team_alpha", "team_beta"):
        export_tomorrow_plan(
            team,
            scorecard_dir=tmp_path / "scorecards",
            attribution_dir=tmp_path / "attribution",
            learning_dir=tmp_path / "learning",
            reviews_dir=reviews,
            competition_status=None,
        )
    assert (reviews / "team_alpha_tomorrow_plan_latest.json").exists()
    assert (reviews / "team_beta_tomorrow_plan_latest.json").exists()
    assert (reviews / "team_alpha_tomorrow_plan_latest.md").exists()
    assert (reviews / "team_beta_tomorrow_plan_latest.md").exists()


def test_run_export_tomorrow_plan_both_dispatches_each_team(monkeypatch):
    seen = []
    monkeypatch.setattr(
        main, "export_tomorrow_plan",
        lambda tid: (TomorrowPlan(team_id=tid, generated_at="x", source_date="y"), ("a.json", "b.md")),
    )
    monkeypatch.setattr(main, "post_tomorrow_plan_to_discord", lambda plan: {"sent": False})

    def _capture(plan, saved_paths=None):
        seen.append(plan.team_id)
        return ""

    monkeypatch.setattr(main, "format_tomorrow_plan_terminal", _capture)
    main.run_export_tomorrow_plan(team="both")
    assert seen == ["team_alpha", "team_beta"]


# --- 7. Discord tomorrow plan disabled by default ---------------------------


def test_discord_tomorrow_plan_disabled_by_default():
    plan = build_tomorrow_plan("team_alpha", _review(), _ledger(), _attribution(), None, None)
    calls = []
    result = post_tomorrow_plan_to_discord(
        plan, env={}, sender=lambda c, m, t: calls.append((c, m))
    )
    assert result["sent"] is False
    assert result["reason"] == "disabled"
    assert calls == []


def test_discord_tomorrow_plan_posts_when_enabled():
    plan = build_tomorrow_plan("team_alpha", _review(), _ledger(), _attribution(), None, None)
    calls = []
    env = {
        "DISCORD_POST_TOMORROW_PLAN": "true",
        "DISCORD_TOMORROW_PLAN_CHANNEL": "strategy_lab",
        "DISCORD_BOT_TOKEN": "test-bot-token-1234567890",
        "DISCORD_STRATEGY_LAB_CHANNEL_ID": "555",
        "DISCORD_UPDATE_MIN_INTERVAL_SECONDS": "0",
    }
    result = post_tomorrow_plan_to_discord(
        plan, env=env, sender=lambda c, m, t: calls.append((c, m))
    )
    assert result["sent"] is True
    assert calls and calls[0][0] == 555


def test_discord_tomorrow_plan_dry_run_does_not_send(capsys):
    plan = build_tomorrow_plan("team_alpha", _review(), _ledger(), _attribution(), None, None)
    env = {"DISCORD_POST_TOMORROW_PLAN": "true", "DISCORD_TOMORROW_PLAN_CHANNEL": "strategy_lab"}
    calls = []
    result = post_tomorrow_plan_to_discord(
        plan, env=env, sender=lambda c, m, t: calls.append((c, m)), dry_run=True
    )
    assert result["sent"] is False
    assert result["reason"] == "dry_run"
    assert calls == []
    assert "Tomorrow Plan" in capsys.readouterr().out


# --- 12. no secrets in any rendering ----------------------------------------


def test_no_secrets_in_renderings():
    plan = build_tomorrow_plan(
        "team_alpha",
        _review(),
        _ledger(avoid_next_cycle=[f"token {SECRET_TOKEN}"]),
        _attribution(),
        None,
        None,
    )
    for text in (
        format_tomorrow_plan_terminal(plan),
        format_tomorrow_plan_markdown(plan),
    ):
        assert SECRET_KEY not in text
    # Discord rendering is redacted by the send path.
    env = {
        "DISCORD_POST_TOMORROW_PLAN": "true",
        "DISCORD_TOMORROW_PLAN_CHANNEL": "strategy_lab",
        "DISCORD_BOT_TOKEN": SECRET_TOKEN,
        "DISCORD_STRATEGY_LAB_CHANNEL_ID": "555",
        "DISCORD_UPDATE_MIN_INTERVAL_SECONDS": "0",
    }
    sent = {}
    post_tomorrow_plan_to_discord(
        plan, env=env, sender=lambda c, m, t: sent.update({"msg": m})
    )
    assert SECRET_TOKEN not in sent["msg"]


# --- off-hours quiet config -------------------------------------------------


def test_quiet_config_defaults_are_quiet():
    cfg = OffHoursQuietConfig.from_env(env={})
    assert cfg.strict_market_hours_only is True
    assert cfg.allow_off_hours_status_refresh is False
    assert cfg.allow_off_hours_attribution_refresh is False
    assert cfg.allow_off_hours_live_equity_refresh is False
    assert cfg.allow_off_hours_discord is False
    assert cfg.allow_off_hours_llm_review is False
    assert cfg.post_one_sleep_notice is True
    assert cfg.quiet_when_closed(False) is True
    assert cfg.quiet_when_closed(True) is False
    assert cfg.quiet_when_closed(None) is False


def test_quiet_config_strict_off_is_not_quiet():
    cfg = OffHoursQuietConfig.from_env(env={"STRICT_MARKET_HOURS_ONLY": "false"})
    assert cfg.quiet_when_closed(False) is False


# --- loop quiet-mode behavior -----------------------------------------------


def _patch_loop_actions(monkeypatch, *, market_open):
    """Record every heavy loop action; control the market clock."""

    calls: list = []
    monkeypatch.setattr(main, "read_kill_switch", lambda: SimpleNamespace(engaged=False, describe=lambda: ""))
    monkeypatch.setattr(main, "_cheap_loop_market_open", lambda: market_open)
    monkeypatch.setattr(main, "run_refresh_proposal_attribution", lambda *a, **k: calls.append("refresh"))
    monkeypatch.setattr(main, "run_week_competition_status", lambda *a, **k: calls.append("status"))
    monkeypatch.setattr(main, "run_export_team_scorecards", lambda *a, **k: calls.append("export"))
    monkeypatch.setattr(main, "run_llm_daily_review", lambda **kw: calls.append(("llm_daily", kw.get("team"))))
    monkeypatch.setattr(main, "_discord_iteration_update_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(
        main, "_post_discord_iteration_update", lambda **kw: calls.append(("discord_team", kw.get("team_id")))
    )
    monkeypatch.setattr(
        main, "_post_discord_competition_summary", lambda **kw: calls.append("discord_summary")
    )

    def _gate(tid):
        return GateDecision(team_id=tid, should_run_full_cycle=True, reason="full"), None

    monkeypatch.setattr(main, "_evaluate_team_cheap_gate", _gate)
    monkeypatch.setattr(main, "run_week_cycle_cli", lambda **kw: calls.append(("cycle", kw.get("review_only"))))
    return calls


def test_market_closed_strict_skips_everything(monkeypatch, capsys):
    calls = _patch_loop_actions(monkeypatch, market_open=False)
    monkeypatch.delenv("STRICT_MARKET_HOURS_ONLY", raising=False)
    main.run_cheap_competition_loop(
        once=True, team="both", market_hours_only=True, llm_review_when_skipped=True,
        llm_daily_review_at_close=True,
    )
    # Quiet by default: no refresh, no status, no live equity, no LLM, no Discord, no cycles.
    assert calls == []
    out = capsys.readouterr().out
    assert OFF_HOURS_SLEEP_NOTICE in out


def test_market_closed_explicit_allow_permits_only_that_action(monkeypatch):
    calls = _patch_loop_actions(monkeypatch, market_open=False)
    monkeypatch.setenv("ALLOW_OFF_HOURS_ATTRIBUTION_REFRESH", "true")
    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=True)
    # Only the explicitly-allowed attribution refresh ran.
    assert "refresh" in calls
    assert "status" not in calls
    assert not any(isinstance(c, tuple) and c[0] == "cycle" for c in calls)
    assert not any(isinstance(c, tuple) and c[0] == "discord_team" for c in calls)


def test_market_closed_allow_discord_only_posts(monkeypatch):
    calls = _patch_loop_actions(monkeypatch, market_open=False)
    monkeypatch.setenv("ALLOW_OFF_HOURS_DISCORD", "true")
    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=True)
    assert ("discord_team", "team_alpha") in calls
    assert ("discord_team", "team_beta") in calls
    assert "discord_summary" in calls
    assert "refresh" not in calls
    assert "status" not in calls


def test_market_open_keeps_existing_behavior(monkeypatch):
    calls = _patch_loop_actions(monkeypatch, market_open=True)
    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=True)
    # Market open: cheap steps + full cycles run as before.
    assert "refresh" in calls
    assert "status" in calls
    assert "export" in calls
    assert any(isinstance(c, tuple) and c[0] == "cycle" for c in calls)


def test_off_hours_notice_does_not_spam(monkeypatch, capsys):
    _patch_loop_actions(monkeypatch, market_open=False)

    ticks = {"n": 0}

    def _sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        main.run_cheap_competition_loop(
            once=False, team="both", market_hours_only=True, sleep_fn=_sleep
        )
    out = capsys.readouterr().out
    # Printed once for the closed-market stretch, not once per iteration.
    assert out.count(OFF_HOURS_SLEEP_NOTICE) == 1


def test_market_hours_quiet_status_no_secrets(monkeypatch, capsys):
    monkeypatch.setattr(main, "_cheap_loop_market_open", lambda: False)
    monkeypatch.setenv("ALPACA_SECRET_KEY", SECRET_KEY)
    main.run_market_hours_quiet_status()
    out = capsys.readouterr().out
    assert "STRICT_MARKET_HOURS_ONLY" in out
    assert "Market: closed" in out
    assert SECRET_KEY not in out
