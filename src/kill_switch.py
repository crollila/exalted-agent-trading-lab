"""Global kill switch: a persisted on/off flag that hard-blocks every broker
submission. It is checked immediately before any order is sent. Status and
report paths keep working while it is engaged.

State lives in an ignored runtime file so it survives restarts and is never
committed. An unreadable state file fails CLOSED (treated as engaged).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_KILL_SWITCH_PATH = Path("data/runtime/kill_switch.json")


class KillSwitchEngaged(RuntimeError):
    """Raised when an order is attempted while the kill switch is engaged."""


@dataclass(frozen=True)
class KillSwitchState:
    engaged: bool
    reason: str | None
    updated_at: str | None
    path: Path

    def describe(self) -> str:
        if self.engaged:
            reason = f" Reason: {self.reason}" if self.reason else ""
            when = f" (since {self.updated_at})" if self.updated_at else ""
            return f"KILL SWITCH ENGAGED{when}. All order submissions are blocked.{reason}"
        return "Kill switch disengaged. Orders follow normal risk gates."


def _resolve(path: Path | str | None) -> Path:
    return Path(path) if path is not None else DEFAULT_KILL_SWITCH_PATH


def read_kill_switch(path: Path | str | None = None) -> KillSwitchState:
    resolved = _resolve(path)
    if not resolved.exists():
        return KillSwitchState(engaged=False, reason=None, updated_at=None, path=resolved)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return KillSwitchState(
            engaged=True,
            reason="kill switch file unreadable; failing closed",
            updated_at=None,
            path=resolved,
        )
    return KillSwitchState(
        engaged=bool(data.get("engaged", False)),
        reason=data.get("reason"),
        updated_at=data.get("updated_at"),
        path=resolved,
    )


def is_engaged(path: Path | str | None = None) -> bool:
    return read_kill_switch(path).engaged


def _write(engaged: bool, reason: str | None, path: Path | str | None) -> KillSwitchState:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged": engaged,
        "reason": (reason.strip() if reason else None),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resolved.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return read_kill_switch(resolved)


def engage(reason: str | None = None, path: Path | str | None = None) -> KillSwitchState:
    return _write(True, reason, path)


def disengage(path: Path | str | None = None) -> KillSwitchState:
    return _write(False, None, path)


def assert_clear(path: Path | str | None = None) -> None:
    """Raise :class:`KillSwitchEngaged` if engaged. Called before every submission."""

    state = read_kill_switch(path)
    if state.engaged:
        raise KillSwitchEngaged(state.describe())
