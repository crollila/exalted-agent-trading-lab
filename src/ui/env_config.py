"""Pure, testable helpers for safe local ``.env`` management from the operator console.

No Streamlit imports, no network, no secrets in output. These helpers let the Setup/Secrets
and Settings pages save credentials and runtime knobs to the local (git-ignored) ``.env``
file while:

- never returning or logging secret values (only ``configured`` / ``missing`` status),
- preserving unrelated existing ``.env`` values and comments,
- creating ``.env`` from ``.env.example`` when missing,
- writing a timestamped backup under an ignored runtime path before overwriting.

All write APIs operate on caller-supplied paths so tests use temp files only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from src.ui.dashboard_state import is_secret_key, mask_secret

DEFAULT_ENV_PATH = Path(".env")
DEFAULT_ENV_EXAMPLE_PATH = Path(".env.example")
DEFAULT_ENV_BACKUP_DIR = Path("data/backups/env")

# Keys the Setup/Secrets wizard manages. Secrets are detected via ``is_secret_key``.
SETUP_DISCORD_KEYS: tuple[str, ...] = (
    "DISCORD_BOT_TOKEN",
    "DISCORD_ALLOWED_CHANNEL_IDS",
    "DISCORD_TEAM_ALPHA_CHANNEL_ID",
    "DISCORD_TEAM_BETA_CHANNEL_ID",
    "DISCORD_TOURNAMENT_RESULTS_CHANNEL_ID",
    "DISCORD_STRATEGY_LAB_CHANNEL_ID",
    "DISCORD_PAPER_TRADING_LOG_CHANNEL_ID",
)
SETUP_HERMES_KEYS: tuple[str, ...] = (
    "HERMES_ENABLED",
    "HERMES_BASE_URL",
    "HERMES_MODEL",
)
SETUP_ALPACA_KEYS: tuple[str, ...] = (
    "TEAM_ALPHA_ALPACA_API_KEY",
    "TEAM_ALPHA_ALPACA_SECRET_KEY",
    "TEAM_ALPHA_ALPACA_BASE_URL",
    "TEAM_ALPHA_ALPACA_PAPER",
    "TEAM_BETA_ALPACA_API_KEY",
    "TEAM_BETA_ALPACA_SECRET_KEY",
    "TEAM_BETA_ALPACA_BASE_URL",
    "TEAM_BETA_ALPACA_PAPER",
)
SETUP_ALL_KEYS: tuple[str, ...] = SETUP_DISCORD_KEYS + SETUP_HERMES_KEYS + SETUP_ALPACA_KEYS

# Placeholder values shipped in .env.example that should count as "missing".
_PLACEHOLDER_PREFIXES = ("your_", "changeme", "<", "replace_me")


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return True
    return any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines into a dict. Comments/blank lines are ignored."""

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def read_env_file(path: Path | str = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Read a ``.env`` file into a dict; return empty dict when absent/unreadable."""

    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        return parse_env_text(file_path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def apply_env_updates(text: str, updates: Mapping[str, str]) -> str:
    """Return ``text`` with ``updates`` applied, preserving comments and unrelated keys."""

    remaining = {key: str(value) for key, value in updates.items()}
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                out_lines.append(f"{key}={remaining.pop(key)}")
                continue
        out_lines.append(line)
    if remaining:
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        for key in sorted(remaining):
            out_lines.append(f"{key}={remaining[key]}")
    return "\n".join(out_lines).rstrip("\n") + "\n"


def backup_env_file(
    path: Path | str = DEFAULT_ENV_PATH,
    backup_dir: Path | str = DEFAULT_ENV_BACKUP_DIR,
) -> Path | None:
    """Write a timestamped copy of ``.env`` under ``backup_dir``; None if nothing to back up.

    Backups live under an ignored runtime path (``data/backups/env``) and are never committed.
    """

    file_path = Path(path)
    if not file_path.is_file():
        return None
    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = target_dir / f"env_backup_{timestamp}.bak"
    backup_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


@dataclass(frozen=True)
class EnvWriteResult:
    path: Path
    created_from_example: bool
    backup_path: Path | None
    updated_keys: tuple[str, ...]  # key names only — never values


def write_env_updates(
    updates: Mapping[str, str],
    *,
    path: Path | str = DEFAULT_ENV_PATH,
    example_path: Path | str = DEFAULT_ENV_EXAMPLE_PATH,
    backup_dir: Path | str = DEFAULT_ENV_BACKUP_DIR,
) -> EnvWriteResult:
    """Save ``updates`` to a local ``.env`` safely.

    - Creates ``.env`` from ``.env.example`` when missing (else starts empty).
    - Backs up an existing ``.env`` before overwriting.
    - Preserves unrelated existing values and comments.
    - Returns only key *names* that changed; never secret values.
    """

    env_path = Path(path)
    created_from_example = False
    if env_path.is_file():
        base_text = env_path.read_text(encoding="utf-8")
    else:
        example = Path(example_path)
        if example.is_file():
            base_text = example.read_text(encoding="utf-8")
            created_from_example = True
        else:
            base_text = ""

    backup_path = backup_env_file(env_path, backup_dir) if env_path.is_file() else None
    new_text = apply_env_updates(base_text, updates)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(new_text, encoding="utf-8")
    return EnvWriteResult(
        path=env_path,
        created_from_example=created_from_example,
        backup_path=backup_path,
        updated_keys=tuple(sorted(updates.keys())),
    )


@dataclass(frozen=True)
class SetupField:
    key: str
    configured: bool
    is_secret: bool
    display_value: str  # masked for secrets; safe value or "(not set)" otherwise


def env_setup_status(
    env: Mapping[str, str],
    keys: tuple[str, ...] = SETUP_ALL_KEYS,
) -> list[SetupField]:
    """Report configured/missing status per key without ever exposing secret values."""

    fields: list[SetupField] = []
    for key in keys:
        raw = env.get(key)
        configured = raw is not None and not _is_placeholder(str(raw))
        secret = is_secret_key(key)
        if secret:
            display = mask_secret(raw) if configured else "(not set)"
        else:
            display = str(raw).strip() if configured else "(not set)"
        fields.append(
            SetupField(key=key, configured=configured, is_secret=secret, display_value=display)
        )
    return fields


def secret_field_status_label(key: str, env: Mapping[str, str]) -> str:
    """Return a clear, value-free label for a secret input based on saved state.

    Never includes the secret value — only whether one is already saved.
    """

    raw = env.get(key)
    configured = raw is not None and not _is_placeholder(str(raw))
    if configured:
        return "Configured — leave blank to keep existing"
    return "Missing — enter value"


def build_env_updates(submitted: Mapping[str, str]) -> dict[str, str]:
    """Build the ``.env`` update set from submitted UI values.

    Blank inputs are dropped so the existing saved value is preserved. Because secret inputs
    are never pre-filled, submitting a secret section with a blank secret keeps the current
    saved secret, while a non-blank entry replaces it. Non-blank values (secret or not) are
    trimmed and included.
    """

    updates: dict[str, str] = {}
    for key, value in submitted.items():
        text = "" if value is None else str(value).strip()
        if text == "":
            continue  # preserve existing value; never overwrite with blank
        updates[key] = text
    return updates


def recommended_first_test_settings() -> dict[str, str]:
    """Conservative settings recommended before any market-hours paper test."""

    return {
        "TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY": "1",
        "TEAM_ALPHA_MAX_DAILY_NOTIONAL": "250000",
        "TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY": "0",
        "TEAM_BETA_MAX_DAILY_NOTIONAL": "0",
        "TEAM_ALPHA_AUTONOMY_ENABLED": "false",
        "TEAM_BETA_AUTONOMY_ENABLED": "false",
    }


def recommended_first_test_env_text() -> str:
    """Render the recommended first-test settings as copy-pasteable ``.env`` lines."""

    return "\n".join(f"{key}={value}" for key, value in recommended_first_test_settings().items())
