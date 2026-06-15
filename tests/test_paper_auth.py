"""Team-aware credential resolution + auth diagnostics. All Alpaca calls mocked."""

from __future__ import annotations

from types import SimpleNamespace

from src.brokers.paper_auth import (
    OK,
    UNAUTHORIZED_401,
    MISSING_ENV,
    ENDPOINT_MISMATCH,
    client_for_source,
    diagnose_all,
    diagnose_source,
    resolve_credentials,
    settings_for_source,
)

PAPER_URL = "https://paper-api.alpaca.markets"


def base_env(**overrides):
    env = {
        "ALPACA_API_KEY": "GLOBAL_KEY",
        "ALPACA_SECRET_KEY": "GLOBAL_SECRET",
        "ALPACA_PAPER": "true",
        "ALPACA_BASE_URL": PAPER_URL,
        "TEAM_ALPHA_ALPACA_API_KEY": "ALPHA_KEY",
        "TEAM_ALPHA_ALPACA_SECRET_KEY": "ALPHA_SECRET",
        "TEAM_ALPHA_ALPACA_PAPER": "true",
        "TEAM_ALPHA_ALPACA_BASE_URL": PAPER_URL,
        "TEAM_BETA_ALPACA_API_KEY": "BETA_KEY",
        "TEAM_BETA_ALPACA_SECRET_KEY": "BETA_SECRET",
        "TEAM_BETA_ALPACA_PAPER": "true",
        "TEAM_BETA_ALPACA_BASE_URL": PAPER_URL,
    }
    env.update(overrides)
    return env


class FakeAPIError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"http {status_code}")


class FakeClient:
    def __init__(self, ok):
        self._ok = ok

    def get_account(self):
        if not self._ok:
            raise FakeAPIError(401)
        return SimpleNamespace(equity="1000000", cash="1000000", buying_power="4000000")


def factory_accepting(*valid_keys):
    def make(settings):
        return FakeClient(settings.alpaca_api_key in valid_keys)
    return make


# --- resolution uses the right env names per source ---


def test_global_source_uses_global_env_names():
    ref = resolve_credentials("global", base_env())
    assert ref.api_key == "GLOBAL_KEY"
    assert ref.env_names["api_key"] == "ALPACA_API_KEY"


def test_team_alpha_uses_team_alpha_env_names():
    ref = resolve_credentials("team_alpha", base_env())
    assert ref.api_key == "ALPHA_KEY"
    assert ref.env_names["api_key"] == "TEAM_ALPHA_ALPACA_API_KEY"


def test_team_beta_uses_team_beta_env_names():
    ref = resolve_credentials("team_beta", base_env())
    assert ref.api_key == "BETA_KEY"
    assert ref.env_names["api_key"] == "TEAM_BETA_ALPACA_API_KEY"


def test_settings_for_team_never_uses_global_keys():
    settings = settings_for_source("team_alpha", env=base_env())
    assert settings.alpaca_api_key == "ALPHA_KEY"
    assert settings.alpaca_api_key != "GLOBAL_KEY"


def test_client_for_team_binds_team_credentials_only():
    captured = {}

    def capture(settings):
        captured["api_key"] = settings.alpaca_api_key
        return FakeClient(True)

    client_for_source("team_beta", env=base_env(), client_factory=capture).get_account()
    assert captured["api_key"] == "BETA_KEY"


# --- invalid global must not block valid teams ---


def test_invalid_global_does_not_block_teams():
    diagnoses = diagnose_all(env=base_env(), client_factory=factory_accepting("ALPHA_KEY", "BETA_KEY"))
    assert diagnoses["global"].auth_ok is False
    assert diagnoses["global"].classification == UNAUTHORIZED_401
    assert diagnoses["team_alpha"].auth_ok is True
    assert diagnoses["team_beta"].auth_ok is True


def test_invalid_alpha_blocks_only_alpha():
    diagnoses = diagnose_all(env=base_env(), client_factory=factory_accepting("GLOBAL_KEY", "BETA_KEY"))
    assert diagnoses["team_alpha"].auth_ok is False
    assert diagnoses["global"].auth_ok is True
    assert diagnoses["team_beta"].auth_ok is True


def test_invalid_beta_blocks_only_beta():
    diagnoses = diagnose_all(env=base_env(), client_factory=factory_accepting("GLOBAL_KEY", "ALPHA_KEY"))
    assert diagnoses["team_beta"].auth_ok is False
    assert diagnoses["global"].auth_ok is True
    assert diagnoses["team_alpha"].auth_ok is True


# --- classifications ---


def test_missing_env_classification():
    env = base_env(TEAM_ALPHA_ALPACA_API_KEY="")
    d = diagnose_source("team_alpha", env=env, client_factory=factory_accepting("ALPHA_KEY"))
    assert d.classification == MISSING_ENV
    assert d.auth_ok is False


def test_endpoint_mismatch_classification():
    env = base_env(TEAM_ALPHA_ALPACA_BASE_URL="https://api.alpaca.markets")
    d = diagnose_source("team_alpha", env=env, client_factory=factory_accepting("ALPHA_KEY"))
    assert d.classification == ENDPOINT_MISMATCH


def test_ok_classification_and_account_snapshot():
    d = diagnose_source("team_alpha", env=base_env(), client_factory=factory_accepting("ALPHA_KEY"))
    assert d.classification == OK
    assert d.account["equity"] == "1000000"


# --- diagnostics never expose secrets ---


def test_diagnostics_never_print_secret_values():
    env = base_env(TEAM_ALPHA_ALPACA_SECRET_KEY="TOP_SECRET_VALUE")
    d = diagnose_source("team_alpha", env=env, client_factory=factory_accepting("ALPHA_KEY"))
    blob = str(d.as_dict())
    assert "TOP_SECRET_VALUE" not in blob
    assert "ALPHA_KEY" not in blob
    assert d.secret_length == len("TOP_SECRET_VALUE")
