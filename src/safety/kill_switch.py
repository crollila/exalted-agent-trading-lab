"""Global kill switch.

A persisted, deterministic on/off flag that hard-blocks all new broker
submissions and autonomous cycles. Status/report paths continue to work while it
is engaged. The switch is checked immediately before any broker submission.

State is stored under an ignored runtime path (``data/runtime/``) so it survives
process restarts and is never committed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_KILL_SWITCH_PATH = Path("data/runtime/kill_switch.json")


class KillSwitchEngaged(RuntimeError):
    """Raised when an action is attempted while the kill switch is engaged."""


@dataclass(frozen=True)
class KillSwitchState:
    engaged: bool
    reason: str | None
    updated_at: str | None
    path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "engaged": self.engaged,
            "reason": self.reason,
            "updated_at": self.updated_at,
            "path": str(self.path),
        }

    def describe(self) -> str:
        if self.engaged:
            reason = f" Reason: {self.reason}" if self.reason else ""
            when = f" (since {self.updated_at})" if self.updated_at else ""
            return f"KILL SWITCH ENGAGED{when}. New broker submissions are blocked.{reason}"
        return "Kill switch disengaged. Broker submissions follow normal gates."


def _resolve(path: Path | str | None) -> Path:
    return Path(path) if path is not None else DEFAULT_KILL_SWITCH_PATH


def read_kill_switch(path: Path | str | None = None) -> KillSwitchState:
    resolved = _resolve(path)
    if not resolved.exists():
        return KillSwitchState(engaged=False, reason=None, updated_at=None, path=resolved)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Fail safe: an unreadable kill-switch file is treated as engaged.
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


def engage(reason: str | None = None, path: Path | str | None = None) -> KillSwitchState:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged": True,
        "reason": (reason.strip() if reason else None),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resolved.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return read_kill_switch(resolved)


def disengage(path: Path | str | None = None) -> KillSwitchState:
    resolved = _resolve(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged": False,
        "reason": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    resolved.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return read_kill_switch(resolved)


def assert_clear(path: Path | str | None = None) -> None:
    """Raise :class:`KillSwitchEngaged` if the switch is engaged.

    This is the single guard that must be called immediately before any broker
    submission.
    """

    state = read_kill_switch(path)
    if state.engaged:
        raise KillSwitchEngaged(state.describe())
