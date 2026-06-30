"""Phase 7P: LLM-backed advisory review agents on routed cheap models.

Mocked providers only — no network, no OpenAI calls, no real Alpaca. Proves the
advisory agents improve written quality without ever controlling execution, and
that the deterministic risk / PortfolioManager remain authoritative.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import src.main as main
from src.agents.llm_review_agents import (
    LLMReviewFlags,
    apply_llm_portfolio_manager,
    build_team_debate,
    generate_daily_review_narrative,
    generate_trade_critique,
    merge_portfolio_advice,
    review_status,
    summarize_strategy_memory,
    synthesize_research_sources,
    team_debate_context,
)
from src.competition.daily_review import DailyTeamReview, save_daily_team_review
from src.competition.portfolio_manager import PortfolioDecision
from src.learning.strategy_memory import (
    StrategyMemory,
    build_strategy_memory,
    strategy_memory_context,
    update_strategy_memory,
)


SECRET = "sk-SECRET-NEVER-PRINT"


class MockProvider:
    """Injectable provider; never touches the network."""

    def __init__(self, payload=None, *, name="mock", fail=False, raw=None):
        self.payload = payload if payload is not None else {}
        self.name = name
        self.fail = fail
        self.raw = raw
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider exploded")
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload)


# --- routing: each stage uses its routed model ------------------------------


def test_critique_uses_critique_model():
    env = {"LLM_MODEL_CRITIQUE": "crit-x", "LLM_MODEL": "mid"}
    provider = MockProvider({"concerns": ["c1"], "summary": "ok"})
    out = generate_trade_critique(team_id="team_alpha", context={"candidate_count": 2},
                                  enabled=True, provider=provider, env=env)
    assert out["model_used"] == "crit-x"
    assert out["source"] == "llm"
    assert out["provider_used"] == "mock"
    assert provider.calls == 1


def test_daily_review_narrative_uses_review_model():
    env = {"LLM_MODEL_REVIEW": "rev-x"}
    provider = MockProvider({"narrative": "Beat SPY.", "what_to_do_tomorrow": ["hold"]})
    out = generate_daily_review_narrative(team_id="team_alpha", attribution={"excess_return": 0.03},
                                          enabled=True, provider=provider, env=env)
    assert out["model_used"] == "rev-x"
    assert out["source"] == "llm"
    assert out["narrative"] == "Beat SPY."


def test_daily_review_narrative_can_use_summary_task():
    env = {"LLM_MODEL_SUMMARY": "sum-x", "LLM_MODEL_REVIEW": "rev-x"}
    provider = MockProvider({"narrative": "n"})
    out = generate_daily_review_narrative(team_id="team_alpha", enabled=True,
                                          provider=provider, env=env, task="summary")
    assert out["model_used"] == "sum-x"


def test_summary_uses_summary_model():
    env = {"LLM_MODEL_SUMMARY": "sum-x"}
    provider = MockProvider({"compact_summary": "tight", "key_lessons": ["l1"]})
    out = summarize_strategy_memory(team_id="team_beta", memory={"recommended_mode": "conservation"},
                                    enabled=True, provider=provider, env=env)
    assert out["model_used"] == "sum-x"
    assert out["compact_summary"] == "tight"


def test_research_synthesis_uses_its_model_only_when_enabled():
    env = {"LLM_MODEL_RESEARCH_SYNTHESIS": "rs-x"}
    provider = MockProvider({"source_summary": ["s"], "uncertainty_notes": ["u"]})
    enabled = synthesize_research_sources(team_id="t", sources=[{"source_id": "r1", "summary": "x"}],
                                          enabled=True, provider=provider, env=env)
    assert enabled["source"] == "llm"
    assert enabled["model_used"] == "rs-x"
    # Disabled: deterministic, provider never called, but model name still reported.
    provider2 = MockProvider({"source_summary": ["s"]})
    disabled = synthesize_research_sources(team_id="t", sources=[{"summary": "x"}],
                                           enabled=False, provider=provider2, env=env)
    assert disabled["source"] == "disabled"
    assert disabled["model_used"] == "rs-x"
    assert provider2.calls == 0


# --- disabled / failure / malformed fall back safely ------------------------


def test_disabled_flag_falls_back_to_deterministic():
    provider = MockProvider({"concerns": ["llm"]})
    out = generate_trade_critique(team_id="team_alpha", context={"candidate_count": 0},
                                  enabled=False, provider=provider, env={})
    assert out["source"] == "disabled"
    assert out["available"] is False
    assert out["concerns"]  # deterministic content present
    assert provider.calls == 0


def test_provider_failure_falls_back_safely():
    provider = MockProvider(fail=True)
    out = generate_trade_critique(team_id="team_alpha", context={"candidate_count": 1},
                                  enabled=True, provider=provider, env={})
    assert out["source"] == "fallback"
    assert out["fallback_reason"].startswith("provider_error")
    assert out["concerns"]  # still deterministic


def test_malformed_json_falls_back_safely():
    provider = MockProvider(raw="this is not json {")
    out = summarize_strategy_memory(team_id="t", memory={}, enabled=True, provider=provider, env={})
    assert out["source"] == "fallback"
    assert out["fallback_reason"] == "malformed_json"
    assert out["compact_summary"]


def test_non_object_json_falls_back_safely():
    provider = MockProvider(raw="[1, 2, 3]")
    out = generate_trade_critique(team_id="t", context={}, enabled=True, provider=provider, env={})
    assert out["source"] == "fallback"
    assert out["fallback_reason"] == "non_object_json"


def test_missing_key_routed_provider_falls_back():
    # No injected provider + no API key -> build_routed_provider raises -> safe fallback.
    out = generate_trade_critique(team_id="t", context={}, enabled=True, provider=None,
                                  env={"EXALTED_LLM_PROVIDER": "openai"})
    assert out["source"] == "fallback"
    assert out["fallback_reason"].startswith("provider_unavailable")


# --- review_status: enabled flags + models only, no secrets -----------------


def test_review_status_reports_flags_and_models_only():
    env = {
        "EXALTED_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": SECRET,
        "LLM_MODEL_CRITIQUE": "crit-x",
        "ENABLE_LLM_PORTFOLIO_MANAGER": "false",
        "ENABLE_LLM_CRITIQUE_AGENT": "true",
    }
    status = review_status(env)
    assert status["stages"]["portfolio_manager"]["enabled"] is False
    assert status["stages"]["critique_agent"]["enabled"] is True
    assert status["stages"]["critique_agent"]["model"] == "crit-x"
    assert status["api_key_configured"] is True
    assert SECRET not in repr(status)


def test_review_flags_defaults():
    flags = LLMReviewFlags.from_env({})
    assert flags.portfolio_manager is False
    assert flags.research_synthesis is False
    assert flags.review_agent is True
    assert flags.critique_agent is True
    assert flags.summary_agent is True
    assert flags.daily_review is True


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    main.main()


def test_cli_llm_review_status_no_secrets(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("EXALTED_LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL_CRITIQUE", "gpt-5.4-nano")
    monkeypatch.setenv("ENABLE_LLM_PORTFOLIO_MANAGER", "false")
    _run_cli(monkeypatch, ["llm-review-status"])
    out = capsys.readouterr().out
    assert "critique_agent: enabled=True | model=gpt-5.4-nano" in out
    assert "portfolio_manager: enabled=False" in out
    assert "API key configured: True" in out
    assert SECRET not in out


# --- portfolio manager safety: advisory can only NARROW ---------------------


def _decision(**over) -> PortfolioDecision:
    base = dict(
        team_id="team_alpha",
        decision_type="add",
        allowed_to_generate_new_orders=True,
        max_new_proposals_this_cycle=2,
        risk_notes="deterministic",
    )
    base.update(over)
    return PortfolioDecision(**base)


def test_llm_pm_cannot_widen_cap():
    merged = merge_portfolio_advice(_decision(max_new_proposals_this_cycle=2),
                                    {"max_new_proposals_this_cycle": 5})
    assert merged.max_new_proposals_this_cycle == 2  # never widened


def test_llm_pm_can_narrow_cap():
    merged = merge_portfolio_advice(_decision(max_new_proposals_this_cycle=3),
                                    {"max_new_proposals_this_cycle": 1})
    assert merged.max_new_proposals_this_cycle == 1


def test_llm_pm_can_recommend_no_trade():
    merged = merge_portfolio_advice(_decision(), {"recommend_no_trade": True})
    assert merged.allowed_to_generate_new_orders is False
    assert merged.max_new_proposals_this_cycle == 0
    merged_hold = merge_portfolio_advice(_decision(), {"recommended_decision": "hold"})
    assert merged_hold.max_new_proposals_this_cycle == 0


def test_llm_pm_cannot_unblock_low_buying_power():
    blocked = _decision(
        decision_type="no_trade",
        allowed_to_generate_new_orders=False,
        max_new_proposals_this_cycle=0,
        low_buying_power=True,
    )
    merged = merge_portfolio_advice(blocked, {"recommended_decision": "add",
                                              "max_new_proposals_this_cycle": 3})
    assert merged.allowed_to_generate_new_orders is False
    assert merged.max_new_proposals_this_cycle == 0


def test_llm_pm_cannot_authorize_spreads_or_options():
    decision = _decision(decision_type="add", max_new_proposals_this_cycle=1)
    merged = merge_portfolio_advice(
        decision,
        {"recommended_decision": "option_debit_spread", "authorize_options": True,
         "max_new_proposals_this_cycle": 1},
    )
    # decision_type is unchanged; advice cannot enable options/spreads or widen.
    assert merged.decision_type == "add"
    assert merged.max_new_proposals_this_cycle == 1
    assert "deterministic risk authoritative" in merged.risk_notes


def test_apply_llm_pm_disabled_returns_unchanged():
    decision = _decision(max_new_proposals_this_cycle=2)
    out, meta = apply_llm_portfolio_manager(decision, team_id="team_alpha", enabled=False,
                                            provider=MockProvider({"max_new_proposals_this_cycle": 5}))
    assert out.max_new_proposals_this_cycle == 2
    assert meta["source"] == "disabled"


def test_apply_llm_pm_enabled_narrows_only():
    decision = _decision(max_new_proposals_this_cycle=3)
    provider = MockProvider({"max_new_proposals_this_cycle": 1, "warnings": ["tight risk"]})
    out, meta = apply_llm_portfolio_manager(decision, team_id="team_alpha", enabled=True,
                                            provider=provider, env={"LLM_MODEL_PORTFOLIO_MANAGER": "pm-x"})
    assert out.max_new_proposals_this_cycle == 1
    assert meta["model_used"] == "pm-x"
    assert "tight risk" in out.risk_notes


# --- strategy debate appears in compact context when enabled ----------------


def test_team_debate_context_present_when_enabled():
    debate = team_debate_context("team_alpha", attribution={"excess_return": 0.02,
                                 "top_winners": [{"symbol": "NVDA"}]}, enabled=True)
    assert debate["available"] is True
    assert "bull_case" in debate and "bear_case" in debate


def test_team_debate_context_absent_when_disabled():
    debate = team_debate_context("team_alpha", enabled=False)
    assert debate["available"] is False


def test_build_team_debate_compact_fields():
    debate = build_team_debate(team_id="team_alpha", attribution={"excess_return": 0.05},
                               enabled=False, provider=MockProvider())
    for key in ("bull_case", "bear_case", "what_would_prove_us_wrong",
                "better_than_weakest_holding", "trade_hold_or_observe", "cost_risk_note"):
        assert key in debate
    assert "model_used" in debate


def test_llm_context_includes_team_debate_and_strategy_memory(tmp_path):
    from src.competition.llm_cycle import build_llm_context

    dirs = dict(
        scorecard_dir=tmp_path / "sc",
        learning_dir=tmp_path / "learn",
        competition_dir=tmp_path / "comp",
        attribution_dir=tmp_path / "attr",
        reviews_dir=tmp_path / "reviews",
        team_memory_dir=tmp_path / "mem",
    )
    enabled = build_llm_context("team_alpha", client=None, price_fn=None,
                                review_flags=LLMReviewFlags(critique_agent=True), **dirs)
    assert enabled["team_debate"]["available"] is True
    assert "bull_case" in enabled["team_debate"]
    assert "strategy_memory" in enabled

    disabled = build_llm_context("team_alpha", client=None, price_fn=None,
                                 review_flags=LLMReviewFlags(critique_agent=False, review_agent=False),
                                 **dirs)
    assert disabled["team_debate"]["available"] is False


# --- multi-day strategy memory ----------------------------------------------


def _review(date: str, **over) -> DailyTeamReview:
    base = dict(
        team_id="team_alpha",
        date=date,
        spy_relative_result="beat SPY by +0.0300 excess",
        why_vs_spy="winners worked",
        helped=["NVDA (semis_ai)", "MSFT (megacap_software_cloud)"],
        hurt=["TSLA (high_beta_auto_ev)"],
        keep_doing=["holding/adding NVDA"],
        stop_doing=["overconcentrating in high_beta_auto_ev"],
        test_next=["size up semis_ai winners"],
        recommended_mode="exploration",
        watch_symbols=["NVDA", "TSLA"],
    )
    base.update(over)
    return DailyTeamReview(**base)


def test_strategy_memory_includes_today_and_trailing(tmp_path):
    reviews_dir = tmp_path / "reviews"
    for d in ("2026-06-13", "2026-06-14", "2026-06-15"):
        save_daily_team_review(_review(d), reviews_dir=reviews_dir)
    memory = build_strategy_memory("team_alpha", reviews_dir=reviews_dir)
    assert memory.date == "2026-06-15"
    assert memory.current_day_lessons  # today
    assert memory.trailing_3_day_lessons
    assert memory.trailing_5_day_lessons
    assert "NVDA" in memory.symbols_to_favor
    assert "TSLA" in memory.symbols_to_avoid
    assert "semis_ai" in memory.sectors_to_favor


def test_strategy_memory_recurring_patterns(tmp_path):
    reviews_dir = tmp_path / "reviews"
    for d in ("2026-06-13", "2026-06-14"):
        save_daily_team_review(_review(d), reviews_dir=reviews_dir)
    memory = build_strategy_memory("team_alpha", reviews_dir=reviews_dir)
    # keep_doing/stop_doing appear on both days -> recurring.
    assert "holding/adding NVDA" in memory.recurring_winning_patterns
    assert "overconcentrating in high_beta_auto_ev" in memory.recurring_losing_patterns


def test_strategy_memory_reset_mode_on_losing_streak(tmp_path):
    reviews_dir = tmp_path / "reviews"
    for d in ("2026-06-11", "2026-06-12", "2026-06-13"):
        save_daily_team_review(_review(d, spy_relative_result="trailed SPY by -0.0200 excess"),
                               reviews_dir=reviews_dir)
    memory = build_strategy_memory("team_alpha", reviews_dir=reviews_dir)
    assert memory.recommended_mode == "reset"


def test_update_strategy_memory_writes_artifact_and_compresses(tmp_path):
    reviews_dir = tmp_path / "reviews"
    mem_dir = tmp_path / "mem"
    save_daily_team_review(_review("2026-06-15"), reviews_dir=reviews_dir)
    provider = MockProvider({"compact_summary": "compressed", "key_lessons": ["k"]})
    memory = update_strategy_memory("team_alpha", reviews_dir=reviews_dir, team_memory_dir=mem_dir,
                                    summary_enabled=True, provider=provider,
                                    env={"LLM_MODEL_SUMMARY": "sum-x"})
    assert (mem_dir / "team_alpha_strategy_memory.json").exists()
    assert memory.compact_summary == "compressed"
    assert memory.last_summary_model_used == "sum-x"
    # Reloads from disk.
    loaded = StrategyMemory.load("team_alpha", mem_dir)
    assert loaded is not None and loaded.compact_summary == "compressed"


def test_update_strategy_memory_deterministic_when_summary_disabled(tmp_path):
    reviews_dir = tmp_path / "reviews"
    mem_dir = tmp_path / "mem"
    save_daily_team_review(_review("2026-06-15"), reviews_dir=reviews_dir)
    provider = MockProvider({"compact_summary": "should-not-be-used"})
    memory = update_strategy_memory("team_alpha", reviews_dir=reviews_dir, team_memory_dir=mem_dir,
                                    summary_enabled=False, provider=provider, env={})
    assert provider.calls == 0
    assert "should-not-be-used" not in memory.compact_summary


def test_future_context_includes_compact_multi_day_lessons(tmp_path):
    reviews_dir = tmp_path / "reviews"
    mem_dir = tmp_path / "mem"
    for d in ("2026-06-13", "2026-06-14", "2026-06-15"):
        save_daily_team_review(_review(d), reviews_dir=reviews_dir)
    update_strategy_memory("team_alpha", reviews_dir=reviews_dir, team_memory_dir=mem_dir,
                           summary_enabled=False, env={})
    ctx = strategy_memory_context("team_alpha", team_memory_dir=mem_dir)
    assert ctx["available"] is True
    assert ctx["trailing_3_day_lessons"]
    assert "symbols_to_favor" in ctx
    assert "never bypass risk" in ctx["note"].lower()


def test_strategy_memory_context_safe_without_data(tmp_path):
    ctx = strategy_memory_context("team_beta", team_memory_dir=tmp_path / "mem")
    assert ctx["available"] is False


# --- run-llm-daily-review writes artifact, submits no orders -----------------


def test_run_llm_daily_review_writes_artifact_no_orders(monkeypatch, tmp_path, capsys):
    reviews_dir = tmp_path / "reviews"
    mem_dir = tmp_path / "mem"
    save_daily_team_review(_review("2026-06-15"), reviews_dir=reviews_dir)

    # Point the CLI handler's loaders at temp dirs; no broker is ever constructed.
    from src.competition import daily_review as dr_mod

    monkeypatch.setattr(main, "load_daily_spy_attribution",
                        lambda tid: dr_mod.load_daily_spy_attribution(tid, attribution_dir=tmp_path / "attr"))
    monkeypatch.setattr(main, "export_daily_team_review",
                        lambda tid: dr_mod.export_daily_team_review(
                            tid, scorecard_dir=tmp_path / "sc", attribution_dir=tmp_path / "attr",
                            learning_dir=tmp_path / "learn", reviews_dir=reviews_dir))

    real_update = main.update_strategy_memory
    monkeypatch.setattr(
        main, "update_strategy_memory",
        lambda tid, **kw: real_update(tid, **{**kw, "reviews_dir": reviews_dir,
                                              "team_memory_dir": mem_dir, "summary_enabled": False}),
    )
    monkeypatch.setenv("ALPACA_SECRET_KEY", SECRET)

    main.run_llm_daily_review(team="team_alpha")
    out = capsys.readouterr().out
    assert "submits NO orders" in out
    assert (mem_dir / "team_alpha_strategy_memory.json").exists()
    assert SECRET not in out


# --- cheap loop with --llm-review-when-skipped does NOT run full strategy ----


def _patch_loop(monkeypatch, decision):
    from src.competition.cycle_gate import GateDecision  # noqa: F401

    calls = []
    monkeypatch.setattr(main, "run_refresh_proposal_attribution", lambda *a, **k: calls.append("refresh"))
    monkeypatch.setattr(main, "run_week_competition_status", lambda *a, **k: calls.append("status"))
    monkeypatch.setattr(main, "run_export_team_scorecards", lambda *a, **k: calls.append("export"))
    monkeypatch.setattr(main, "_evaluate_team_cheap_gate", lambda team: (decision, None))
    monkeypatch.setattr(main, "_cheap_loop_market_open", lambda: True)
    monkeypatch.setattr(main, "run_llm_daily_review", lambda **kw: calls.append(("llm_daily", kw.get("team"))))

    def fake_cycle(team, proposal_source, review_only=False, **kwargs):
        calls.append(("cycle", team, review_only))

    monkeypatch.setattr(main, "run_week_cycle_cli", fake_cycle)
    return calls


def test_cheap_loop_llm_review_when_skipped_no_full_cycle(monkeypatch):
    from src.competition.cycle_gate import GateDecision

    decision = GateDecision(team_id="x", should_run_full_cycle=False, reason="cheap")
    calls = _patch_loop(monkeypatch, decision)
    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=False,
                                    llm_review_when_skipped=True)
    # Review-only ran; full strategy cycle (review_only=False) never ran.
    assert ("cycle", "team_alpha", True) in calls
    assert not any(isinstance(c, tuple) and c[0] == "cycle" and c[2] is False for c in calls)
    assert ("llm_daily", "team_alpha") in calls


def test_cheap_loop_llm_review_when_skipped_cli_dispatch(monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "run_cheap_competition_loop", lambda **kw: captured.update(kw))
    _run_cli(monkeypatch, ["run-cheap-competition-loop", "--once", "--dry-run-loop",
                           "--llm-review-when-skipped"])
    assert captured["llm_review_when_skipped"] is True


# --- spreads still refuse safely + no secrets tracked -----------------------


def test_spreads_still_refuse_safely():
    from src.brokers.options_adapter import OptionsExecutionAdapter

    adapter = OptionsExecutionAdapter(enabled=True, enable_spreads=False)
    assert adapter.single_leg_enabled is True
    assert adapter.spreads_enabled is False


def test_no_env_or_data_tracked_by_git():
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert ".env" not in tracked
    assert not any(p == "data" or p.startswith("data/") for p in tracked)
