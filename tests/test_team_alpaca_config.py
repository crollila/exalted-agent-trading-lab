import pytest

from src.brokers.alpaca_client import PAPER_BASE_URL
from src.brokers.team_alpaca_config import TeamAlpacaConfigError, load_team_alpaca_paper_config


def test_missing_team_credentials_return_clear_safe_status():
    config = load_team_alpaca_paper_config("team_alpha", env={})

    assert not config.configured
    assert "team_alpha Alpaca paper credentials are not configured" in config.safe_status()
    assert "TEAM_ALPHA_ALPACA_API_KEY" in config.safe_status()


def test_team_credentials_enforce_paper_mode():
    config = load_team_alpaca_paper_config(
        "team_alpha",
        env={
            "TEAM_ALPHA_ALPACA_API_KEY": "paper-key",
            "TEAM_ALPHA_ALPACA_SECRET_KEY": "paper-secret",
            "TEAM_ALPHA_ALPACA_PAPER": "false",
            "TEAM_ALPHA_ALPACA_BASE_URL": PAPER_BASE_URL,
        },
    )

    with pytest.raises(TeamAlpacaConfigError, match="TEAM_ALPHA_ALPACA_PAPER=true"):
        config.validate_ready()


def test_team_credentials_enforce_exact_paper_base_url():
    config = load_team_alpaca_paper_config(
        "team_beta",
        env={
            "TEAM_BETA_ALPACA_API_KEY": "paper-key",
            "TEAM_BETA_ALPACA_SECRET_KEY": "paper-secret",
            "TEAM_BETA_ALPACA_PAPER": "true",
            "TEAM_BETA_ALPACA_BASE_URL": "https://api.alpaca.markets",
        },
    )

    with pytest.raises(TeamAlpacaConfigError, match=PAPER_BASE_URL):
        config.validate_ready()


def test_team_credentials_configured_status_does_not_expose_secrets():
    config = load_team_alpaca_paper_config(
        "team_beta",
        env={
            "TEAM_BETA_ALPACA_API_KEY": "paper-key",
            "TEAM_BETA_ALPACA_SECRET_KEY": "paper-secret",
            "TEAM_BETA_ALPACA_PAPER": "true",
            "TEAM_BETA_ALPACA_BASE_URL": PAPER_BASE_URL,
        },
    )

    status = config.safe_status()

    assert status == "team_beta Alpaca paper credentials configured."
    assert "paper-key" not in status
    assert "paper-secret" not in status


def test_unknown_team_id_is_rejected():
    with pytest.raises(TeamAlpacaConfigError, match="Unknown team_id"):
        load_team_alpaca_paper_config("team_gamma", env={})
