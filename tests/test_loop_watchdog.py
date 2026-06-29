"""Tests for heartbeat + loop health + watchdog (Phase 7W)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.competition import loop_heartbeat as hb
from src.competition.loop_watchdog import (
    DEFAULT_STALE_THRESHOLD_SECONDS,
    assess_loop_health,
    run_watchdog_once,
)

NOW = datetime(2026, 6, 29, 15, 0, tzinfo=timezone.utc)


# --- heartbeat ----------------------------------------------------------------


def test_heartbeat_write_read_age(tmp_path):
    path = tmp_path / "hb.json"
    hb.write_heartbeat(pid=123, iteration=5, market_state="open", path=path, now=NOW)
    data = hb.read_heartbeat(path)
    assert data["pid"] == 123 and data["iteration"] == 5
    age = hb.heartbeat_age_seconds(data, now=NOW + timedelta(seconds=90))
    assert abs(age - 90) < 1


def test_graceful_shutdown_marker(tmp_path):
    path = tmp_path / "hb.json"
    hb.write_heartbeat(pid=1, iteration=1, market_state="open", path=path, now=NOW)
    hb.mark_graceful_shutdown(path=path, now=NOW)
    assert hb.is_graceful_shutdown(hb.read_heartbeat(path)) is True


# --- health assessment --------------------------------------------------------


def test_stale_pid_dead_process_recommends_restart():
    health = assess_loop_health(
        pid=999, process_alive=False, heartbeat=None, heartbeat_age_seconds=None,
    )
    assert health.restart_recommended is True
    assert "not alive" in health.reason


def test_live_process_fresh_heartbeat_is_healthy():
    heartbeat = {"updated_at": NOW.isoformat(), "graceful_shutdown": False, "market_state": "open"}
    health = assess_loop_health(
        pid=123, process_alive=True, heartbeat=heartbeat, heartbeat_age_seconds=30,
    )
    assert health.restart_recommended is False
    assert "healthy" in health.reason.lower()


def test_stale_heartbeat_even_if_process_alive_recommends_restart():
    heartbeat = {"updated_at": (NOW - timedelta(hours=2)).isoformat(), "graceful_shutdown": False}
    health = assess_loop_health(
        pid=123, process_alive=True, heartbeat=heartbeat,
        heartbeat_age_seconds=7200, stale_threshold_seconds=DEFAULT_STALE_THRESHOLD_SECONDS,
    )
    assert health.restart_recommended is True
    assert "stale" in health.reason


def test_graceful_shutdown_never_restarts():
    heartbeat = {"updated_at": NOW.isoformat(), "graceful_shutdown": True}
    health = assess_loop_health(
        pid=None, process_alive=False, heartbeat=heartbeat, heartbeat_age_seconds=1,
    )
    assert health.restart_recommended is False


# --- watchdog decision (never trades; only spawns via the starter seam) --------


def _dead_health():
    return assess_loop_health(pid=None, process_alive=False, heartbeat=None, heartbeat_age_seconds=None)


def test_watchdog_restarts_when_dead_and_clear():
    calls = {"n": 0}

    def starter():
        calls["n"] += 1
        return SimpleNamespace(success=True, message="started")

    result = run_watchdog_once(
        health=_dead_health(), kill_switch_engaged=False, detected_duplicates=[],
        starter=starter, now=NOW,
    )
    assert result.restarted is True and result.action == "restart"
    assert calls["n"] == 1


def test_watchdog_respects_kill_switch():
    calls = {"n": 0}

    def starter():
        calls["n"] += 1  # pragma: no cover - must not be called
        return SimpleNamespace(success=True, message="started")

    result = run_watchdog_once(
        health=_dead_health(), kill_switch_engaged=True, detected_duplicates=[],
        starter=starter, now=NOW,
    )
    assert result.restarted is False and result.action == "skip_kill_switch"
    assert calls["n"] == 0  # never started while kill switch engaged


def test_watchdog_avoids_duplicate_launch():
    calls = {"n": 0}

    def starter():
        calls["n"] += 1  # pragma: no cover
        return SimpleNamespace(success=True, message="started")

    result = run_watchdog_once(
        health=_dead_health(), kill_switch_engaged=False, detected_duplicates=[4321],
        starter=starter, now=NOW,
    )
    assert result.restarted is False and result.action == "skip_duplicate"
    assert calls["n"] == 0


def test_watchdog_noop_when_healthy():
    healthy = assess_loop_health(
        pid=1, process_alive=True,
        heartbeat={"updated_at": NOW.isoformat()}, heartbeat_age_seconds=10,
    )
    result = run_watchdog_once(
        health=healthy, kill_switch_engaged=False, detected_duplicates=[],
        starter=lambda: SimpleNamespace(success=True, message="x"), now=NOW,
    )
    assert result.action == "noop" and result.restarted is False


def test_watchdog_starter_is_only_side_effect():
    # The watchdog never calls a broker; its only seam is the loop starter. Prove
    # that a starter raising does not crash the watchdog (logged, not fatal).
    def boom():
        raise RuntimeError("spawn failed")

    result = run_watchdog_once(
        health=_dead_health(), kill_switch_engaged=False, detected_duplicates=[],
        starter=boom, now=NOW,
    )
    assert result.restarted is False and result.action == "restart_error"


def test_no_secrets_in_health_or_watchdog_result():
    import json
    health = _dead_health()
    result = run_watchdog_once(
        health=health, kill_switch_engaged=False, detected_duplicates=[1],
        starter=lambda: SimpleNamespace(success=False, message="x"), now=NOW,
    )
    for blob in (json.dumps(health.as_dict()).lower(), json.dumps(result.as_dict()).lower()):
        for needle in ("secret", "api_key", "token", "password", "bearer"):
            assert needle not in blob
