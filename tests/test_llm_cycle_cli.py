"""CLI wiring for --proposal-source (registration, resolution, fail-fast)."""

from __future__ import annotations

import pytest

import src.main as main
from src.agents.llm_provider import LLMProviderError
from src.competition.risk_engine import AccountContext


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    main.main()


# --- resolution ---


def test_resolve_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv("WEEK_COMPETITION_PROPOSAL_SOURCE", raising=False)
    assert main._resolve_proposal_source_name(None) == "default"


def test_resolve_cli_value_wins(monkeypatch):
    monkeypatch.setenv("WEEK_COMPETITION_PROPOSAL_SOURCE", "default")
    assert main._resolve_proposal_source_name("llm") == "llm"


def test_resolve_env_value_used_when_cli_absent(monkeypatch):
    monkeypatch.setenv("WEEK_COMPETITION_PROPOSAL_SOURCE", "llm")
    assert main._resolve_proposal_source_name(None) == "llm"


def test_resolve_invalid_source_exits(monkeypatch):
    with pytest.raises(SystemExit):
        main._resolve_proposal_source_name("telepathy")


# --- registration / dispatch ---


def test_proposal_source_flag_registered_and_dispatched(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main,
        "run_week_cycle_cli",
        lambda team, proposal_source, review_only: captured.update(
            team=team, src=proposal_source, review=review_only
        ),
    )
    _run_cli(monkeypatch, ["run-week-cycle", "--team", "team_alpha", "--proposal-source", "llm"])
    assert captured == {"team": "team_alpha", "src": "llm", "review": False}


def test_default_source_dispatches_none(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main,
        "run_week_cycle_cli",
        lambda team, proposal_source, review_only: captured.update(
            team=team, src=proposal_source, review=review_only
        ),
    )
    _run_cli(monkeypatch, ["run-week-cycle", "--team", "team_beta"])
    assert captured == {"team": "team_beta", "src": None, "review": False}


# --- missing key fails before broker execution ---


def test_llm_missing_key_fails_before_broker_execution(monkeypatch):
    tripwire = {"cycle_ran": False, "exec_client_built": False}

    monkeypatch.setattr(main, "_account_context_for_source", lambda team, settings: AccountContext(equity=1_000_000.0))
    monkeypatch.setattr(main, "_market_data_price_fn", lambda settings: None)
    monkeypatch.setattr(main, "_safe_read_client", lambda team, settings: None)
    monkeypatch.setattr(main, "load_competition_state", lambda *a, **k: type("S", (), {"starting_spy_price": None})())

    def boom_provider(*a, **k):
        raise LLMProviderError("OPENAI_API_KEY is missing.")

    def boom_cycle(*a, **k):
        tripwire["cycle_ran"] = True
        raise AssertionError("cycle must not run when key is missing")

    def boom_client(*a, **k):
        tripwire["exec_client_built"] = True
        raise AssertionError("broker client must not be built when key is missing")

    # run-week-cycle now routes the strategy model through build_routed_provider.
    monkeypatch.setattr(main, "build_routed_provider", boom_provider)
    monkeypatch.setattr(main, "run_week_cycle", boom_cycle)
    monkeypatch.setattr(main, "client_for_source", boom_client)
    monkeypatch.setattr(main, "read_kill_switch", lambda *a, **k: type("K", (), {"engaged": False})())

    with pytest.raises(SystemExit):
        main.run_week_cycle_cli("team_alpha", proposal_source="llm")

    assert tripwire["cycle_ran"] is False
    assert tripwire["exec_client_built"] is False
