"""Loop health assessment + watchdog (Phase 7W).

Deterministic, Windows-compatible liveness logic with injectable seams so process
/ PID / spawn behavior can be fully mocked in tests. The watchdog:

* judges "alive" by PID **and** a fresh heartbeat (a stale PID alone is not alive),
* never launches a duplicate loop (defers to the tracked-PID + process scan),
* never restarts during a known graceful shutdown,
* never starts while the kill switch is engaged,
* never submits a paper order itself (it only (re)spawns the gated loop process).

The actual process spawn is delegated to ``start_cheap_loop`` (which uses the same
project Python and refuses duplicates); the watchdog only decides *whether* to
call it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_STALE_THRESHOLD_SECONDS = 1800  # 2x a 900s loop interval


@dataclass
class TeamLoopStatus:
    team_id: str
    last_iteration_at: str | None = None
    last_iteration_age_seconds: float | None = None
    last_cycle_action: str | None = None
    last_exception: str | None = None


@dataclass
class LoopHealth:
    pid: int | None
    process_alive: bool
    last_heartbeat_at: str | None
    heartbeat_age_seconds: float | None
    graceful_shutdown: bool
    market_state: str | None
    teams: list[TeamLoopStatus] = field(default_factory=list)
    restart_recommended: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def assess_loop_health(
    *,
    pid: int | None,
    process_alive: bool,
    heartbeat: dict[str, Any] | None,
    heartbeat_age_seconds: float | None,
    per_team: list[TeamLoopStatus] | None = None,
    stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
) -> LoopHealth:
    """Pure health assessment. ``restart_recommended`` excludes graceful shutdown."""

    graceful = bool(heartbeat and heartbeat.get("graceful_shutdown"))
    last_hb = heartbeat.get("updated_at") if heartbeat else None
    market = heartbeat.get("market_state") if heartbeat else None

    if graceful:
        reason = "Graceful shutdown flagged; no restart."
        restart = False
    elif pid is None:
        reason = "No tracked loop PID; loop not running."
        restart = True
    elif not process_alive:
        reason = f"Tracked PID {pid} is not alive (stale PID); restart recommended."
        restart = True
    elif heartbeat is None:
        reason = "No heartbeat file; cannot confirm liveness; restart recommended."
        restart = True
    elif heartbeat_age_seconds is None:
        reason = "Heartbeat has no timestamp; treating as stale; restart recommended."
        restart = True
    elif heartbeat_age_seconds > stale_threshold_seconds:
        reason = (f"Heartbeat is stale ({heartbeat_age_seconds:.0f}s > "
                  f"{stale_threshold_seconds}s); loop likely hung; restart recommended.")
        restart = True
    else:
        reason = f"Loop healthy (PID {pid} alive, heartbeat {heartbeat_age_seconds:.0f}s old)."
        restart = False

    return LoopHealth(
        pid=pid,
        process_alive=process_alive,
        last_heartbeat_at=last_hb,
        heartbeat_age_seconds=heartbeat_age_seconds,
        graceful_shutdown=graceful,
        market_state=market,
        teams=list(per_team or []),
        restart_recommended=restart,
        reason=reason,
    )


@dataclass
class WatchdogResult:
    checked_at: str
    restart_recommended: bool
    restarted: bool
    action: str
    detail: str
    health: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_watchdog_once(
    *,
    health: LoopHealth,
    kill_switch_engaged: bool,
    detected_duplicates: list[int],
    starter: Callable[[], Any],
    now: datetime | None = None,
) -> WatchdogResult:
    """Decide and (if needed) trigger a single restart. Never submits orders.

    ``starter`` is the only side-effecting seam (defaults to ``start_cheap_loop``
    at the call site) and it itself refuses duplicates. The watchdog adds the
    kill-switch, graceful-shutdown, and duplicate-detection guards on top.
    """

    now = now or datetime.now(timezone.utc)
    iso = now.isoformat()

    if not health.restart_recommended:
        return WatchdogResult(iso, False, False, "noop", health.reason, health.as_dict())

    if health.graceful_shutdown:
        return WatchdogResult(iso, True, False, "skip_graceful",
                              "Graceful shutdown flagged; not restarting.", health.as_dict())

    if kill_switch_engaged:
        return WatchdogResult(iso, True, False, "skip_kill_switch",
                              "Kill switch engaged; refusing to start a loop.", health.as_dict())

    if detected_duplicates:
        return WatchdogResult(iso, True, False, "skip_duplicate",
                              f"Live loop process(es) detected {detected_duplicates}; not launching another.",
                              health.as_dict())

    try:
        result = starter()
        ok = bool(getattr(result, "success", False))
        msg = getattr(result, "message", str(result))
        return WatchdogResult(iso, True, ok, "restart" if ok else "restart_failed", msg, health.as_dict())
    except Exception as exc:  # noqa: BLE001 - a spawn failure is logged, never fatal
        return WatchdogResult(iso, True, False, "restart_error", f"Restart failed: {exc}", health.as_dict())


__all__ = [
    "DEFAULT_STALE_THRESHOLD_SECONDS",
    "TeamLoopStatus", "LoopHealth", "WatchdogResult",
    "assess_loop_health", "run_watchdog_once",
]
