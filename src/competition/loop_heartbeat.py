"""Process-level heartbeat for the competition loop (Phase 7W).

The loop writes a heartbeat every iteration so liveness can be judged by *both*
the PID and a fresh timestamp — a stale PID file alone never counts as running.
On a graceful exit the loop sets a flag so the watchdog does not "restart" an
intentional shutdown.

Read-only consumers: ``loop-health`` and ``loop-watchdog``. Deterministic; no
secrets; never trades.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HEARTBEAT_PATH = Path("data/runtime/loop_heartbeat.json")
HEARTBEAT_PATH_ENV = "LOOP_HEARTBEAT_PATH"


def heartbeat_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.getenv(HEARTBEAT_PATH_ENV)
    return Path(env) if env else DEFAULT_HEARTBEAT_PATH


def write_heartbeat(
    *,
    pid: int,
    iteration: int,
    market_state: str,
    started_at: str | None = None,
    graceful_shutdown: bool = False,
    path: Path | str | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Write/refresh the heartbeat. Best-effort: never raises into the loop."""

    now = now or datetime.now(timezone.utc)
    target = heartbeat_path(path)
    payload = {
        "pid": int(pid),
        "iteration": int(iteration),
        "market_state": market_state,
        "updated_at": now.isoformat(),
        "started_at": started_at or now.isoformat(),
        "graceful_shutdown": bool(graceful_shutdown),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return target
    except Exception as exc:  # noqa: BLE001 - heartbeat must never crash the loop
        print(f"(heartbeat write failed: {exc}; continuing loop)")
        return None


def read_heartbeat(path: Path | str | None = None) -> dict[str, Any] | None:
    target = heartbeat_path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - a corrupt heartbeat is treated as absent
        return None


def heartbeat_age_seconds(heartbeat: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not heartbeat:
        return None
    stamp = heartbeat.get("updated_at")
    if not stamp:
        return None
    try:
        ts = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds()


def mark_graceful_shutdown(*, path: Path | str | None = None, now: datetime | None = None) -> None:
    """Flag an intentional shutdown so the watchdog won't restart it."""

    hb = read_heartbeat(path) or {}
    now = now or datetime.now(timezone.utc)
    hb["graceful_shutdown"] = True
    hb["updated_at"] = now.isoformat()
    target = heartbeat_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(hb, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - best-effort
        print(f"(graceful-shutdown marker write failed: {exc})")


def is_graceful_shutdown(heartbeat: dict[str, Any] | None) -> bool:
    return bool(heartbeat and heartbeat.get("graceful_shutdown"))


__all__ = [
    "DEFAULT_HEARTBEAT_PATH", "HEARTBEAT_PATH_ENV", "heartbeat_path",
    "write_heartbeat", "read_heartbeat", "heartbeat_age_seconds",
    "mark_graceful_shutdown", "is_graceful_shutdown",
]
