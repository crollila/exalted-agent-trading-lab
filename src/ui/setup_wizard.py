"""Pure first-run setup wizard helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.brokers.alpaca_client import PAPER_BASE_URL
from src.ui.env_config import env_setup_status, recommended_first_test_settings


@dataclass(frozen=True)
class SetupCheck:
    key: str
    label: str
    ok: bool
    message: str


def _present(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "")).strip())


def _paper_team_ok(env: Mapping[str, str], prefix: str) -> bool:
    return (
        _present(env, f"{prefix}_ALPACA_API_KEY")
        and _present(env, f"{prefix}_ALPACA_SECRET_KEY")
        and env.get(f"{prefix}_ALPACA_PAPER", "").strip().lower() == "true"
        and env.get(f"{prefix}_ALPACA_BASE_URL", "").strip() == PAPER_BASE_URL
    )


def build_setup_checks(env: Mapping[str, str], *, env_path: Path | str = Path(".env")) -> list[SetupCheck]:
    """Return value-free setup checks for the wizard validation step."""

    env_file = Path(env_path)
    alpha_ok = _paper_team_ok(env, "TEAM_ALPHA")
    beta_safe = (
        env.get("TEAM_BETA_MAX_PAPER_ORDERS_PER_DAY", "").strip() in {"", "0"}
        and env.get("TEAM_BETA_MAX_DAILY_NOTIONAL", "").strip() in {"", "0", "0.0"}
    )
    hermes_ok = _present(env, "HERMES_BASE_URL") and _present(env, "HERMES_MODEL")
    discord_ok = _present(env, "DISCORD_BOT_TOKEN")
    return [
        SetupCheck("env_exists", ".env file", env_file.is_file(), ".env exists" if env_file.is_file() else ".env missing"),
        SetupCheck(
            "alpaca_alpha_paper",
            "Alpha Alpaca paper account",
            alpha_ok,
            "Alpha paper credentials and paper endpoint configured" if alpha_ok else "Alpha paper credentials or endpoint missing",
        ),
        SetupCheck(
            "alpaca_beta_safe",
            "Beta first-test caps",
            beta_safe,
            "Beta is capped at zero for first tests" if beta_safe else "Set Beta first-test caps to zero",
        ),
        SetupCheck(
            "autonomy_off",
            "Autonomy disabled",
            env.get("TEAM_ALPHA_AUTONOMY_ENABLED", "false").lower() != "true"
            and env.get("TEAM_BETA_AUTONOMY_ENABLED", "false").lower() != "true",
            "Both teams start with autonomy off",
        ),
        SetupCheck(
            "hermes",
            "Hermes/Ollama",
            hermes_ok,
            "Hermes base URL and model configured" if hermes_ok else "Hermes/Ollama optional config missing",
        ),
        SetupCheck(
            "discord",
            "Discord bot",
            discord_ok,
            "Discord token configured" if discord_ok else "Discord is optional and currently missing",
        ),
    ]


def setup_progress_percent(checks: list[SetupCheck]) -> int:
    """Return simple percent completion for setup checks."""

    if not checks:
        return 0
    return round((sum(1 for check in checks if check.ok) / len(checks)) * 100)


def setup_secret_status_rows(env: Mapping[str, str]) -> list[dict[str, object]]:
    """Rows for configured/missing setup status, never exposing secret values."""

    rows: list[dict[str, object]] = []
    for field in env_setup_status(env):
        rows.append(
            {
                "key": field.key,
                "configured": field.configured,
                "secret": field.is_secret,
                "display_value": field.display_value,
            }
        )
    return rows


def first_run_step_labels() -> list[str]:
    """Human-friendly setup wizard steps."""

    return [
        "Welcome / paper-only warning",
        "Local requirements",
        ".env setup",
        "Safety caps",
        "Validation",
        "Finish",
    ]


def recommended_safe_updates() -> dict[str, str]:
    """Conservative first-run environment updates."""

    return recommended_first_test_settings()
