"""CLI registration + dispatch for team-aware auth commands (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.main as main
from src.brokers.paper_auth import UNAUTHORIZED_401


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    main.main()


def test_paper_status_team_flag_dispatches(monkeypatch):
    calls = {}
    monkeypatch.setattr(main, "run_paper_status", lambda team: calls.setdefault("team", team))
    _run_cli(monkeypatch, ["paper-status", "--team", "team_alpha"])
    assert calls["team"] == "team_alpha"


def test_paper_status_defaults_to_global(monkeypatch):
    calls = {}
    monkeypatch.setattr(main, "run_paper_status", lambda team: calls.setdefault("team", team))
    _run_cli(monkeypatch, ["paper-status"])
    assert calls["team"] == "global"


def test_alpaca_auth_diagnose_registered(monkeypatch):
    calls = {}
    monkeypatch.setattr(main, "run_alpaca_auth_diagnose", lambda: calls.setdefault("ran", True))
    _run_cli(monkeypatch, ["alpaca-auth-diagnose"])
    assert calls.get("ran") is True


def test_competition_readiness_check_registered(monkeypatch):
    calls = {}
    monkeypatch.setattr(main, "run_competition_readiness_check", lambda: calls.setdefault("ran", True))
    _run_cli(monkeypatch, ["competition-readiness-check"])
    assert calls.get("ran") is True


def test_paper_status_rejects_unknown_team(monkeypatch):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", "paper-status", "--team", "nope"])
    with pytest.raises(SystemExit):
        main.main()


# --- readiness blocker logic is team-specific ---


def _diag(source, auth_ok, classification="ok"):
    return SimpleNamespace(source=source, auth_ok=auth_ok, classification=classification)


def test_readiness_blocks_team_with_failed_auth():
    can_submit, blockers = main._readiness_can_submit(
        _diag("team_alpha", False, UNAUTHORIZED_401), ks_engaged=False, is_paper=True
    )
    assert can_submit is False
    assert any("team_alpha" in b for b in blockers)


def test_readiness_allows_team_with_good_auth():
    can_submit, blockers = main._readiness_can_submit(
        _diag("team_beta", True), ks_engaged=False, is_paper=True
    )
    assert can_submit is True
    assert blockers == []


def test_readiness_blocks_on_kill_switch():
    can_submit, blockers = main._readiness_can_submit(
        _diag("team_alpha", True), ks_engaged=True, is_paper=True
    )
    assert can_submit is False
    assert any("kill switch" in b.lower() for b in blockers)
