"""Settings loading from environment."""

from __future__ import annotations

import pytest

from src.config import ROLE_STRATEGIST, Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "TEAM_ALPHA_ALPACA_API_KEY", "TEAM_ALPHA_ALPACA_SECRET_KEY",
        "TEAM_BETA_ALPACA_API_KEY", "TEAM_BETA_ALPACA_SECRET_KEY",
        "LLM_PROVIDER", "OPENAI_API_KEY", "LLM_MODEL", "LLM_MODEL_STRATEGIST",
        "CYCLE_MINUTES", "DRY_RUN", "MAX_POSITION_PCT", "ALLOW_SHORTS",
        "WATCHLIST", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID",
        "DISCORD_PAPER_TRADING_LOG_CHANNEL_ID", "MAX_ORDERS_PER_DAY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch):
    settings = Settings.from_env(dotenv=False)
    assert settings.llm_provider == "openai"
    assert settings.cycle_minutes == 30
    assert settings.risk.max_position_pct == 0.15
    assert settings.risk.allow_shorts is True
    assert not settings.dry_run
    assert len(settings.teams) == 2
    assert not settings.team("team_alpha").has_credentials


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_API_KEY", "ak")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_SECRET_KEY", "as")
    monkeypatch.setenv("LLM_MODEL", "base-model")
    monkeypatch.setenv("LLM_MODEL_STRATEGIST", "big-model")
    monkeypatch.setenv("CYCLE_MINUTES", "15")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("MAX_POSITION_PCT", "0.08")
    monkeypatch.setenv("ALLOW_SHORTS", "false")
    monkeypatch.setenv("WATCHLIST", "spy, qqq ,nvda")
    monkeypatch.setenv("DISCORD_PAPER_TRADING_LOG_CHANNEL_ID", "123")

    settings = Settings.from_env(dotenv=False)
    assert settings.team("team_alpha").has_credentials
    assert settings.model_for("researcher") == "base-model"
    assert settings.model_for(ROLE_STRATEGIST) == "big-model"
    assert settings.cycle_minutes == 15
    assert settings.dry_run is True
    assert settings.risk.max_position_pct == 0.08
    assert settings.risk.allow_shorts is False
    assert settings.watchlist == ("SPY", "QQQ", "NVDA")
    # legacy Discord channel var still works
    assert settings.discord_channel_id == "123"


def test_bad_numbers_fall_back(monkeypatch):
    monkeypatch.setenv("CYCLE_MINUTES", "soon")
    monkeypatch.setenv("MAX_POSITION_PCT", "big")
    settings = Settings.from_env(dotenv=False)
    assert settings.cycle_minutes == 30
    assert settings.risk.max_position_pct == 0.15


def test_unknown_team_raises():
    settings = Settings.from_env(dotenv=False)
    with pytest.raises(KeyError):
        settings.team("team_gamma")
