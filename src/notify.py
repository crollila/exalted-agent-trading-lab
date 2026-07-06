"""Notifications and error reporting.

* Discord posts via the REST API (no discord.py dependency) — safe no-op when
  unconfigured; a failed post logs one line and never breaks a cycle.
* ``report_error`` is the single funnel for anything going wrong: it appends
  to ``data/runtime/errors.log`` AND posts to Discord, so problems are never
  only visible in a terminal nobody is watching.
"""

from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.market_time import now_utc

DISCORD_API = "https://discord.com/api/v10"
MAX_LEN = 1900  # Discord hard limit is 2000


def discord_configured(settings: Settings) -> bool:
    return bool(settings.discord_bot_token and settings.discord_channel_id)


def post_discord(settings: Settings, content: str) -> bool:
    """Post ``content`` to the configured channel. Returns True on success."""

    if not discord_configured(settings) or not content.strip():
        return False
    import requests

    try:
        response = requests.post(
            f"{DISCORD_API}/channels/{settings.discord_channel_id}/messages",
            headers={
                "Authorization": f"Bot {settings.discord_bot_token}",
                "Content-Type": "application/json",
            },
            json={"content": content[:MAX_LEN]},
            timeout=10,
        )
        if 200 <= response.status_code < 300:
            return True
        print(f"(Discord post failed: HTTP {response.status_code}; continuing.)")
    except Exception as exc:  # noqa: BLE001 - notifications must never break trading
        print(f"(Discord post failed: {exc}; continuing.)")
    return False


def errors_log_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "runtime" / "errors.log"


def report_error(settings: Settings, where: str, message: str) -> None:
    """Record an error to the error log and Discord. Never raises.

    ``where`` is a short location tag like "team_alpha cycle" or "loop".
    """

    message = str(message).strip()
    line = f"{now_utc().isoformat()} | {where} | {message}"
    print(f"!!! {line}")
    try:
        path = errors_log_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print(f"(error log unwritable: {exc})")
    post_discord(settings, f":rotating_light: **ERROR** [{where}] {message[:1500]}")


def recent_errors(settings: Settings, count: int = 5) -> list[str]:
    """Last ``count`` lines of the error log (for the status command)."""

    path = errors_log_path(settings)
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines[-count:]
