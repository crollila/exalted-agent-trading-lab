"""Phase 7O: LLM model routing + cheap competition loop.

Mocked only — no network, no OpenAI calls, no real Alpaca.
"""

from __future__ import annotations

import pytest

import src.main as main
from src.agents.llm_provider import LLMProviderConfig, LLMProviderError
from src.agents.model_routing import (
    build_routed_provider,
    resolve_all_models,
    resolve_model,
    routing_status,
)
from src.competition.cycle_gate import GateDecision


# --- model routing resolution ----------------------------------------------


def test_routing_chooses_task_specific_var():
    env = {"LLM_MODEL_STRATEGY": "strong", "LLM_MODEL": "mid", "OPENAI_MODEL": "base"}
    assert resolve_model("strategy", env) == "strong"
    assert resolve_model("review", {"LLM_MODEL_REVIEW": "nano", "LLM_MODEL": "mid"}) == "nano"


def test_routing_falls_back_to_llm_model():
    env = {"LLM_MODEL": "mid", "OPENAI_MODEL": "base"}  # no task-specific var
    assert resolve_model("strategy", env) == "mid"
    assert resolve_model("summary", env) == "mid"


def test_routing_falls_back_to_openai_model():
    env = {"OPENAI_MODEL": "base"}  # no task var, no LLM_MODEL
    assert resolve_model("critique", env) == "base"
    assert resolve_model("default", env) == "base"


def test_routing_built_in_default_when_nothing_set():
    assert resolve_model("strategy", {}) == "gpt-4o-mini"


def test_missing_optional_model_vars_does_not_crash():
    env = {"LLM_MODEL_STRATEGY": "strong"}  # others missing
    models = resolve_all_models(env)
    assert models["strategy"] == "strong"
    assert models["review"] == "gpt-4o-mini"  # falls all the way through, no crash


def test_default_task_uses_llm_model_then_openai_model():
    assert resolve_model("default", {"LLM_MODEL": "mid"}) == "mid"
    assert resolve_model("default", {"OPENAI_MODEL": "base"}) == "base"


# --- routing status (no secrets) -------------------------------------------


def test_routing_status_reports_models_and_bool_only():
    env = {
        "EXALTED_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-SECRET-NEVER-PRINT",
        "LLM_MODEL_STRATEGY": "gpt-5.4-mini",
        "LLM_MODEL_REVIEW": "gpt-5.4-nano",
        "LLM_MODEL": "gpt-5.4-mini",
    }
    status = routing_status(env)
    assert status["provider"] == "openai"
    assert status["strategy_model"] == "gpt-5.4-mini"
    assert status["review_model"] == "gpt-5.4-nano"
    assert status["api_key_configured"] is True
    # No key contents anywhere in the status payload.
    assert "sk-SECRET-NEVER-PRINT" not in repr(status)


def test_routing_status_key_configured_false_without_key():
    status = routing_status({"EXALTED_LLM_PROVIDER": "openai"})
    assert status["api_key_configured"] is False


def test_llm_routing_status_cli_no_secret(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET-NEVER-PRINT")
    monkeypatch.setenv("EXALTED_LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL_STRATEGY", "gpt-5.4-mini")
    monkeypatch.setenv("LLM_MODEL_REVIEW", "gpt-5.4-nano")
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", "llm-routing-status"])
    main.main()
    out = capsys.readouterr().out
    assert "Strategy model: gpt-5.4-mini" in out
    assert "Review model: gpt-5.4-nano" in out
    assert "API key configured: True" in out
    assert "sk-SECRET-NEVER-PRINT" not in out


# --- build_routed_provider uses the routed model ---------------------------


def test_build_routed_provider_uses_strategy_model():
    env = {"EXALTED_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k", "LLM_MODEL_STRATEGY": "gpt-5.4-mini"}
    provider = build_routed_provider("strategy", env=env)
    assert provider.config.openai_model == "gpt-5.4-mini"


def test_build_routed_provider_review_uses_cheaper_model():
    env = {"EXALTED_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k", "LLM_MODEL_REVIEW": "gpt-5.4-nano"}
    provider = build_routed_provider("review", env=env)
    assert provider.config.openai_model == "gpt-5.4-nano"


def test_build_routed_provider_missing_key_fails_safely():
    env = {"EXALTED_LLM_PROVIDER": "openai", "LLM_MODEL_STRATEGY": "gpt-5.4-mini"}  # no key
    with pytest.raises(LLMProviderError):
        build_routed_provider("strategy", env=env)


def test_anthropic_routing_sets_anthropic_model():
    env = {
        "EXALTED_LLM_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "k",
        "LLM_MODEL_STRATEGY": "claude-strong",
    }
    provider = build_routed_provider("strategy", env=env)
    assert provider.config.anthropic_model == "claude-strong"


def test_llm_provider_config_accepts_llm_provider_alias():
    cfg = LLMProviderConfig.from_env({"LLM_PROVIDER": "ollama"})
    assert cfg.provider == "ollama"


# --- cheap competition loop -------------------------------------------------


def _patch_loop_steps(monkeypatch, gate_decision):
    calls = []
    monkeypatch.setattr(main, "run_refresh_proposal_attribution", lambda *a, **k: calls.append("refresh"))
    monkeypatch.setattr(main, "run_week_competition_status", lambda *a, **k: calls.append("status"))
    monkeypatch.setattr(main, "run_export_team_scorecards", lambda *a, **k: calls.append("export"))
    monkeypatch.setattr(main, "_evaluate_team_cheap_gate", lambda team: (gate_decision, None))
    monkeypatch.setattr(main, "_cheap_loop_market_open", lambda: True)

    def fake_cycle(team, proposal_source, review_only=False):
        calls.append(("cycle", team, review_only))

    monkeypatch.setattr(main, "run_week_cycle_cli", fake_cycle)
    return calls


def test_cheap_loop_once_skips_full_cycle_when_gate_says_no(monkeypatch, capsys):
    decision = GateDecision(team_id="x", should_run_full_cycle=False, reason="too soon")
    calls = _patch_loop_steps(monkeypatch, decision)
    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=False)
    assert "refresh" in calls and "status" in calls and "export" in calls
    assert not any(isinstance(c, tuple) and c[0] == "cycle" for c in calls)


def test_cheap_loop_once_runs_full_cycle_when_gate_says_yes(monkeypatch):
    decision = GateDecision(team_id="x", should_run_full_cycle=True, reason="go")
    calls = _patch_loop_steps(monkeypatch, decision)
    main.run_cheap_competition_loop(once=True, team="team_beta", market_hours_only=False)
    assert ("cycle", "team_beta", False) in calls


def test_cheap_loop_review_only_when_skipped(monkeypatch):
    decision = GateDecision(team_id="x", should_run_full_cycle=False, reason="cheap")
    calls = _patch_loop_steps(monkeypatch, decision)
    main.run_cheap_competition_loop(
        once=True, team="team_alpha", market_hours_only=False, run_review_only_when_skipped=True
    )
    assert ("cycle", "team_alpha", True) in calls  # review-only, not full


def test_cheap_loop_dry_run_does_not_call_cycle_or_actions(monkeypatch, capsys):
    decision = GateDecision(team_id="x", should_run_full_cycle=True, reason="go")
    calls = _patch_loop_steps(monkeypatch, decision)
    main.run_cheap_competition_loop(once=True, team="both", market_hours_only=False, dry_run_loop=True)
    # Dry-run prints intentions; runs no refresh/cycle/export.
    assert calls == []
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "would run: run-week-cycle" in out


def test_cheap_loop_does_not_print_secrets(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET-NEVER-PRINT")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "ALPACA-SECRET-NEVER-PRINT")
    decision = GateDecision(team_id="x", should_run_full_cycle=False, reason="cheap")
    _patch_loop_steps(monkeypatch, decision)
    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=False)
    out = capsys.readouterr().out
    assert "sk-SECRET-NEVER-PRINT" not in out
    assert "ALPACA-SECRET-NEVER-PRINT" not in out


def test_cheap_loop_skips_full_cycle_when_market_closed(monkeypatch):
    decision = GateDecision(team_id="x", should_run_full_cycle=True, reason="go")
    calls = _patch_loop_steps(monkeypatch, decision)
    monkeypatch.setattr(main, "_cheap_loop_market_open", lambda: False)  # market closed
    main.run_cheap_competition_loop(once=True, team="team_alpha", market_hours_only=True)
    # Even though the gate says yes, a closed market blocks the full cycle.
    assert not any(isinstance(c, tuple) and c[0] == "cycle" for c in calls)


def test_cheap_loop_cli_dry_run_dispatches(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main, "run_cheap_competition_loop", lambda **kw: captured.update(kw)
    )
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", "run-cheap-competition-loop", "--once", "--dry-run-loop"])
    main.main()
    assert captured["once"] is True
    assert captured["dry_run_loop"] is True


# --- spreads still refuse safely -------------------------------------------


def test_spreads_still_refuse_safely():
    from src.brokers.options_adapter import OptionsExecutionAdapter

    adapter = OptionsExecutionAdapter(enabled=True, enable_spreads=False)
    assert adapter.single_leg_enabled is True
    assert adapter.spreads_enabled is False
