"""Tests for safe local .env management helpers.

All file I/O uses temp paths; no real .env, secrets, or network are touched. Secret values
must never appear in any return value, status report, or recommended-settings output.
"""

from __future__ import annotations

from src.ui.env_config import (
    EnvWriteResult,
    apply_env_updates,
    backup_env_file,
    build_env_updates,
    env_setup_status,
    read_env_file,
    recommended_first_test_env_text,
    recommended_first_test_settings,
    secret_field_status_label,
    write_env_updates,
)


def test_read_env_file_parses_keys_and_ignores_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\n\nDISCORD_TEAM_ALPHA_CHANNEL_ID=111\nHERMES_MODEL=hermes3\n",
        encoding="utf-8",
    )
    values = read_env_file(env_path)
    assert values == {"DISCORD_TEAM_ALPHA_CHANNEL_ID": "111", "HERMES_MODEL": "hermes3"}
    assert read_env_file(tmp_path / "missing.env") == {}


def test_apply_env_updates_preserves_comments_and_unrelated_values():
    original = "# header\nHERMES_MODEL=old\nUNRELATED=keepme\n"
    updated = apply_env_updates(original, {"HERMES_MODEL": "new", "HERMES_BASE_URL": "http://x"})
    assert "# header" in updated
    assert "UNRELATED=keepme" in updated
    assert "HERMES_MODEL=new" in updated
    assert "HERMES_MODEL=old" not in updated
    assert "HERMES_BASE_URL=http://x" in updated


def test_write_env_updates_preserves_unrelated_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("UNRELATED=keepme\nHERMES_MODEL=old\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    result = write_env_updates(
        {"HERMES_MODEL": "new", "DISCORD_TEAM_ALPHA_CHANNEL_ID": "111"},
        path=env_path,
        example_path=tmp_path / "missing.example",
        backup_dir=backup_dir,
    )

    assert isinstance(result, EnvWriteResult)
    saved = read_env_file(env_path)
    assert saved["UNRELATED"] == "keepme"
    assert saved["HERMES_MODEL"] == "new"
    assert saved["DISCORD_TEAM_ALPHA_CHANNEL_ID"] == "111"
    # Backup of the prior file must exist and contain the old value.
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    assert "HERMES_MODEL=old" in result.backup_path.read_text(encoding="utf-8")


def test_write_env_updates_creates_from_example_when_missing(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("ALPACA_PAPER=true\nHERMES_MODEL=\n", encoding="utf-8")
    env_path = tmp_path / ".env"

    result = write_env_updates(
        {"HERMES_MODEL": "hermes3"},
        path=env_path,
        example_path=example,
        backup_dir=tmp_path / "backups",
    )

    assert result.created_from_example is True
    assert result.backup_path is None  # nothing to back up on first creation
    saved = read_env_file(env_path)
    assert saved["ALPACA_PAPER"] == "true"  # preserved from example
    assert saved["HERMES_MODEL"] == "hermes3"


def test_write_env_result_never_contains_secret_values(tmp_path):
    env_path = tmp_path / ".env"
    result = write_env_updates(
        {"TEAM_ALPHA_ALPACA_SECRET_KEY": "supersecretvalue"},
        path=env_path,
        example_path=tmp_path / "missing.example",
        backup_dir=tmp_path / "backups",
    )
    # The result object reports key names only, never the secret value.
    assert result.updated_keys == ("TEAM_ALPHA_ALPACA_SECRET_KEY",)
    assert "supersecretvalue" not in repr(result)


def test_backup_env_file_returns_none_when_absent(tmp_path):
    assert backup_env_file(tmp_path / "nope.env", tmp_path / "backups") is None


def test_env_setup_status_reports_without_exposing_secrets(tmp_path):
    env = {
        "DISCORD_TEAM_ALPHA_CHANNEL_ID": "111",
        "TEAM_ALPHA_ALPACA_SECRET_KEY": "supersecretvalue",
        "TEAM_ALPHA_ALPACA_API_KEY": "your_team_alpha_paper_key_here",  # placeholder => missing
    }
    fields = {field.key: field for field in env_setup_status(env)}

    secret_field = fields["TEAM_ALPHA_ALPACA_SECRET_KEY"]
    assert secret_field.configured is True
    assert secret_field.is_secret is True
    assert secret_field.display_value == "********"
    assert "supersecretvalue" not in secret_field.display_value

    # Placeholder example value counts as missing.
    assert fields["TEAM_ALPHA_ALPACA_API_KEY"].configured is False

    # Non-secret configured value can be shown.
    channel_field = fields["DISCORD_TEAM_ALPHA_CHANNEL_ID"]
    assert channel_field.configured is True
    assert channel_field.is_secret is False
    assert channel_field.display_value == "111"

    # Missing key reported as not set, never raising.
    assert fields["DISCORD_BOT_TOKEN"].configured is False
    assert fields["DISCORD_BOT_TOKEN"].display_value == "(not set)"


def test_secret_field_status_label_reports_without_exposing_value():
    env = {
        "TEAM_ALPHA_ALPACA_SECRET_KEY": "supersecretvalue",
        "TEAM_ALPHA_ALPACA_API_KEY": "your_team_alpha_paper_key_here",  # placeholder => missing
    }
    configured_label = secret_field_status_label("TEAM_ALPHA_ALPACA_SECRET_KEY", env)
    assert configured_label == "Configured — leave blank to keep existing"
    assert "supersecretvalue" not in configured_label

    assert secret_field_status_label("TEAM_ALPHA_ALPACA_API_KEY", env) == "Missing — enter value"
    assert secret_field_status_label("DISCORD_BOT_TOKEN", env) == "Missing — enter value"


def test_build_env_updates_drops_blank_and_trims():
    submitted = {
        "TEAM_ALPHA_ALPACA_API_KEY": "",  # blank secret -> preserve (dropped)
        "TEAM_ALPHA_ALPACA_SECRET_KEY": "  newsecret  ",  # nonblank -> trimmed + included
        "TEAM_ALPHA_ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "HERMES_MODEL": None,  # treated as blank
    }
    updates = build_env_updates(submitted)
    assert "TEAM_ALPHA_ALPACA_API_KEY" not in updates
    assert "HERMES_MODEL" not in updates
    assert updates["TEAM_ALPHA_ALPACA_SECRET_KEY"] == "newsecret"
    assert updates["TEAM_ALPHA_ALPACA_BASE_URL"] == "https://paper-api.alpaca.markets"


def test_blank_secret_input_preserves_existing_env_secret(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TEAM_ALPHA_ALPACA_API_KEY=existing-key\n"
        "TEAM_ALPHA_ALPACA_SECRET_KEY=existing-secret\n"
        "TEAM_ALPHA_ALPACA_BASE_URL=https://paper-api.alpaca.markets\n",
        encoding="utf-8",
    )
    # User left secrets blank, only changed the (non-secret) base URL.
    submitted = {
        "TEAM_ALPHA_ALPACA_API_KEY": "",
        "TEAM_ALPHA_ALPACA_SECRET_KEY": "",
        "TEAM_ALPHA_ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "TEAM_ALPHA_ALPACA_PAPER": "true",
    }
    result = write_env_updates(
        build_env_updates(submitted),
        path=env_path,
        example_path=tmp_path / "missing.example",
        backup_dir=tmp_path / "backups",
    )

    saved = read_env_file(env_path)
    assert saved["TEAM_ALPHA_ALPACA_API_KEY"] == "existing-key"  # preserved
    assert saved["TEAM_ALPHA_ALPACA_SECRET_KEY"] == "existing-secret"  # preserved
    assert saved["TEAM_ALPHA_ALPACA_PAPER"] == "true"
    # Backup of the prior .env must have been written before overwrite.
    assert result.backup_path is not None and result.backup_path.is_file()
    assert "TEAM_ALPHA_ALPACA_API_KEY" not in result.updated_keys


def test_nonblank_secret_input_updates_existing_env_secret(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("TEAM_ALPHA_ALPACA_SECRET_KEY=old-secret\n", encoding="utf-8")
    submitted = {"TEAM_ALPHA_ALPACA_SECRET_KEY": "new-secret"}

    result = write_env_updates(
        build_env_updates(submitted),
        path=env_path,
        example_path=tmp_path / "missing.example",
        backup_dir=tmp_path / "backups",
    )

    saved = read_env_file(env_path)
    assert saved["TEAM_ALPHA_ALPACA_SECRET_KEY"] == "new-secret"
    assert result.backup_path is not None and result.backup_path.is_file()
    assert "old-secret" in result.backup_path.read_text(encoding="utf-8")
    # The result reports only the key name, never the secret value.
    assert "new-secret" not in repr(result)


def test_nonsecret_fields_still_save_and_update(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_TEAM_ALPHA_CHANNEL_ID=111\n", encoding="utf-8")
    submitted = {
        "DISCORD_TEAM_ALPHA_CHANNEL_ID": "222",  # update existing non-secret
        "DISCORD_TEAM_BETA_CHANNEL_ID": "333",  # add new non-secret
    }
    write_env_updates(
        build_env_updates(submitted),
        path=env_path,
        example_path=tmp_path / "missing.example",
        backup_dir=tmp_path / "backups",
    )
    saved = read_env_file(env_path)
    assert saved["DISCORD_TEAM_ALPHA_CHANNEL_ID"] == "222"
    assert saved["DISCORD_TEAM_BETA_CHANNEL_ID"] == "333"


def test_recommended_first_test_settings_are_conservative():
    settings = recommended_first_test_settings()
    assert settings["TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY"] == "1"
    assert settings["TEAM_ALPHA_MAX_DAILY_NOTIONAL"] == "250000"
    assert settings["TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY"] == "0"
    assert settings["TEAM_BETA_MAX_DAILY_NOTIONAL"] == "0"
    assert settings["TEAM_ALPHA_AUTONOMY_ENABLED"] == "false"
    assert settings["TEAM_BETA_AUTONOMY_ENABLED"] == "false"

    text = recommended_first_test_env_text()
    assert "TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY=1" in text
    assert "TEAM_BETA_MAX_DAILY_NOTIONAL=0" in text
