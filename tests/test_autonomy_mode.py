import pytest

from src.discord_bot.bot import (
    ALLOWED_AUTONOMY_MODES,
    AUTONOMY_MODE_PAPER_ADVANCED,
    AUTONOMY_MODE_PAPER_STOCKS_ONLY,
    DiscordBotConfig,
    TOKEN_ENV,
    parse_autonomy_mode,
    run_discord_bot,
)


def test_allowed_modes_centralized():
    assert AUTONOMY_MODE_PAPER_STOCKS_ONLY in ALLOWED_AUTONOMY_MODES
    assert AUTONOMY_MODE_PAPER_ADVANCED in ALLOWED_AUTONOMY_MODES


def test_parse_accepts_paper_stocks_only():
    assert parse_autonomy_mode("paper_stocks_only") == AUTONOMY_MODE_PAPER_STOCKS_ONLY


def test_parse_accepts_paper_advanced():
    assert parse_autonomy_mode("paper_advanced") == AUTONOMY_MODE_PAPER_ADVANCED


def test_parse_defaults_to_stocks_only_when_blank():
    assert parse_autonomy_mode(None) == AUTONOMY_MODE_PAPER_STOCKS_ONLY
    assert parse_autonomy_mode("   ") == AUTONOMY_MODE_PAPER_STOCKS_ONLY


def test_parse_rejects_invalid_mode():
    with pytest.raises(ValueError, match="Autonomy mode must be one of"):
        parse_autonomy_mode("yolo_live")


def test_from_env_accepts_paper_advanced():
    config = DiscordBotConfig.from_env(
        {"TEAM_ALPHA_AUTONOMY_MODE": "paper_advanced", "TEAM_BETA_AUTONOMY_MODE": "paper_advanced"}
    )
    alpha = config.autonomy_for("team_alpha")
    assert alpha.mode == AUTONOMY_MODE_PAPER_ADVANCED
    # paper_advanced is NOT the legacy stock-only execution gate.
    assert alpha.stock_paper_only is False


def test_from_env_still_rejects_invalid_mode():
    with pytest.raises(ValueError, match="Autonomy mode must be one of"):
        DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_MODE": "not_a_mode"})


def test_missing_token_reported_even_with_paper_advanced_env(monkeypatch, capsys):
    # Reproduces the reported failure: paper_advanced present, token missing.
    monkeypatch.setenv("TEAM_ALPHA_AUTONOMY_MODE", "paper_advanced")
    monkeypatch.setenv("TEAM_BETA_AUTONOMY_MODE", "paper_advanced")
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    with pytest.raises(SystemExit) as excinfo:
        run_discord_bot()

    assert excinfo.value.code == 1
    assert "DISCORD_BOT_TOKEN is required" in capsys.readouterr().err
