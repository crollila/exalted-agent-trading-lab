from src.brokers.alpaca_client import PAPER_BASE_URL
from src.ui.setup_wizard import (
    build_setup_checks,
    first_run_step_labels,
    recommended_safe_updates,
    setup_progress_percent,
    setup_secret_status_rows,
)


def test_setup_checks_validate_env_without_revealing_secrets(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("exists", encoding="utf-8")
    env = {
        "TEAM_ALPHA_ALPACA_API_KEY": "alpha-key",
        "TEAM_ALPHA_ALPACA_SECRET_KEY": "alpha-secret",
        "TEAM_ALPHA_ALPACA_PAPER": "true",
        "TEAM_ALPHA_ALPACA_BASE_URL": PAPER_BASE_URL,
        "TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY": "0",
        "TEAM_BETA_MAX_DAILY_NOTIONAL": "0",
        "TEAM_ALPHA_AUTONOMY_ENABLED": "false",
        "TEAM_BETA_AUTONOMY_ENABLED": "false",
        "HERMES_BASE_URL": "http://localhost:11434",
        "HERMES_MODEL": "hermes",
    }

    checks = build_setup_checks(env, env_path=env_path)
    assert setup_progress_percent(checks) >= 80
    rendered = repr(checks)
    assert "alpha-key" not in rendered
    assert "alpha-secret" not in rendered
    assert {check.key for check in checks} >= {"env_exists", "alpaca_alpha_paper", "autonomy_off"}


def test_setup_secret_status_rows_mask_secrets():
    rows = setup_secret_status_rows({"DISCORD_BOT_TOKEN": "super-secret-token"})
    token_row = next(row for row in rows if row["key"] == "DISCORD_BOT_TOKEN")

    assert token_row["configured"] is True
    assert token_row["secret"] is True
    assert token_row["display_value"] == "********"
    assert "super-secret-token" not in repr(rows)


def test_first_run_steps_and_safe_updates_are_conservative():
    assert first_run_step_labels()[0] == "Welcome / paper-only warning"

    updates = recommended_safe_updates()
    assert updates["TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY"] == "1"
    assert updates["TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY"] == "0"
    assert updates["TEAM_ALPHA_AUTONOMY_ENABLED"] == "false"
    assert updates["TEAM_BETA_AUTONOMY_ENABLED"] == "false"
