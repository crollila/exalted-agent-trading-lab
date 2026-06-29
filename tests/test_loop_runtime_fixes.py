"""Regression tests for the loop-watchdog runtime fixes (Phase 7W follow-up).

Pins two production failures so they cannot return, without touching any trading,
risk, broker, or LLM-authority behaviour:

1. ``run_loop_watchdog`` raised ``NameError: name 'sys' is not defined`` on the
   non-dry-run restart path (``sys`` was unimported at module scope). The watchdog
   caught it, logged ``action=restart_error``, and never relaunched the loop.
2. A background child crashed with ``UnicodeEncodeError`` writing ``≈`` to
   ``data/runtime/cheap_loop.log`` because its redirected stdout fell back to the
   Windows cp1252 code page instead of UTF-8.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import src.main as main_mod
from src.competition.loop_watchdog import LoopHealth, assess_loop_health
from src.ui import operator_controls as ops


def _dead_health() -> LoopHealth:
    """Health that recommends a restart (no tracked PID, no heartbeat)."""

    return assess_loop_health(
        pid=None, process_alive=False, heartbeat=None, heartbeat_age_seconds=None,
    )


def _wire_watchdog(monkeypatch, *, health, kill_switch_engaged, duplicates, starter):
    """Patch run_loop_watchdog's seams so it runs hermetically (no real spawn/IO)."""

    monkeypatch.setattr(main_mod, "_gather_loop_health", lambda *_a, **_k: health)
    monkeypatch.setattr(
        main_mod, "read_kill_switch", lambda: SimpleNamespace(engaged=kill_switch_engaged)
    )
    monkeypatch.setattr(main_mod, "_watchdog_log", lambda *_a, **_k: None)
    monkeypatch.setattr(ops, "detect_cheap_loop_processes", lambda: list(duplicates))
    monkeypatch.setattr(ops, "start_cheap_loop", starter)


# --- Fix A: watchdog restart path no longer raises NameError ------------------


def test_watchdog_restart_non_dry_run_uses_current_python(monkeypatch, capsys):
    captured: dict = {}

    def fake_start_cheap_loop(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(success=True, message="started (test)")

    _wire_watchdog(
        monkeypatch, health=_dead_health(), kill_switch_engaged=False,
        duplicates=[], starter=fake_start_cheap_loop,
    )

    main_mod.run_loop_watchdog(team="both", sleep_seconds=900, once=True, dry_run=False)

    # The restart ran end-to-end (no NameError swallowed into restart_error) and
    # handed the spawner the *current* interpreter.
    assert captured["python_executable"] == sys.executable
    assert captured["team"] == "both"
    assert captured["sleep_seconds"] == 900
    out = capsys.readouterr().out
    assert "action=restart " in out and "restarted=True" in out
    assert "restart_error" not in out
    assert "name 'sys' is not defined" not in out


def test_watchdog_dry_run_does_not_spawn(monkeypatch, capsys):
    def must_not_start(**_kwargs):  # pragma: no cover - must never run in dry-run
        raise AssertionError("dry-run must not spawn a loop")

    _wire_watchdog(
        monkeypatch, health=_dead_health(), kill_switch_engaged=False,
        duplicates=[], starter=must_not_start,
    )

    main_mod.run_loop_watchdog(team="both", sleep_seconds=900, once=True, dry_run=True)

    out = capsys.readouterr().out
    assert "dry-run: restart suppressed" in out
    assert "restarted=False" in out


# --- Watchdog guards still hold after the fix ---------------------------------


def test_watchdog_skips_duplicate_launch(monkeypatch, capsys):
    calls: list = []

    def starter(**kwargs):  # pragma: no cover - must not be called
        calls.append(kwargs)
        return SimpleNamespace(success=True, message="started")

    _wire_watchdog(
        monkeypatch, health=_dead_health(), kill_switch_engaged=False,
        duplicates=[4321], starter=starter,
    )

    main_mod.run_loop_watchdog(once=True, dry_run=False)

    out = capsys.readouterr().out
    assert "action=skip_duplicate" in out and "restarted=False" in out
    assert calls == []  # never launched a second loop


def test_watchdog_honors_kill_switch(monkeypatch, capsys):
    calls: list = []

    def starter(**kwargs):  # pragma: no cover - must not be called
        calls.append(kwargs)
        return SimpleNamespace(success=True, message="started")

    _wire_watchdog(
        monkeypatch, health=_dead_health(), kill_switch_engaged=True,
        duplicates=[], starter=starter,
    )

    main_mod.run_loop_watchdog(once=True, dry_run=False)

    out = capsys.readouterr().out
    assert "action=skip_kill_switch" in out and "restarted=False" in out
    assert calls == []  # never started while the kill switch is engaged


def test_watchdog_honors_graceful_shutdown(monkeypatch, capsys):
    calls: list = []

    def starter(**kwargs):  # pragma: no cover - must not be called
        calls.append(kwargs)
        return SimpleNamespace(success=True, message="started")

    # Force the (otherwise mutually exclusive) combination so the graceful guard,
    # not the restart-recommended gate, is what suppresses the launch.
    graceful = LoopHealth(
        pid=None, process_alive=False, last_heartbeat_at=None,
        heartbeat_age_seconds=None, graceful_shutdown=True, market_state=None,
        teams=[], restart_recommended=True,
        reason="graceful shutdown flagged (forced for test)",
    )
    _wire_watchdog(
        monkeypatch, health=graceful, kill_switch_engaged=False,
        duplicates=[], starter=starter,
    )

    main_mod.run_loop_watchdog(once=True, dry_run=False)

    out = capsys.readouterr().out
    assert "action=skip_graceful" in out and "restarted=False" in out
    assert calls == []  # never restarts during a graceful shutdown


# --- Fix B: Windows-safe UTF-8 output -----------------------------------------


def test_start_cheap_loop_passes_utf8_env_to_child(tmp_path):
    seen: dict = {}

    class _FakePopen:
        pid = 4321

    def fake_popen(command, **kwargs):
        seen["env"] = kwargs.get("env")
        return _FakePopen()

    result = ops.start_cheap_loop(
        runtime_dir=tmp_path, popen=fake_popen,
        process_checker=lambda pid: False, detector=lambda: [],
    )

    assert result.ok is True
    env = seen["env"]
    assert env is not None
    assert env.get("PYTHONUTF8") == "1"
    assert env.get("PYTHONIOENCODING") == "utf-8"


def test_redirected_child_prints_unicode_without_crashing(tmp_path):
    """A background-style child with redirected stdout prints ``≈`` safely.

    Reproduces the original failure: a child whose stdout is redirected to a file
    previously fell back to cp1252 on Windows and crashed on U+2248. With the
    UTF-8 child environment it writes the symbol intact and exits cleanly. The
    parent opens the log as UTF-8 exactly as ``start_cheap_loop`` does.
    """

    log = tmp_path / "child.log"
    code = "print('SPY ≈ +1.0% (approx)')"
    with open(log, "w", encoding="utf-8") as handle:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            stdout=handle, stderr=subprocess.STDOUT,
            env=ops._utf8_child_env(),
        )

    assert completed.returncode == 0
    assert "≈" in log.read_text(encoding="utf-8")


def test_configure_utf8_runtime_output_reconfigures_streams(monkeypatch):
    class _FakeStream:
        def __init__(self):
            self.kwargs = None

        def reconfigure(self, **kwargs):
            self.kwargs = kwargs

    out, err = _FakeStream(), _FakeStream()
    monkeypatch.setattr(main_mod.sys, "stdout", out)
    monkeypatch.setattr(main_mod.sys, "stderr", err)

    main_mod._configure_utf8_runtime_output()

    assert out.kwargs == {"encoding": "utf-8", "errors": "backslashreplace"}
    assert err.kwargs == {"encoding": "utf-8", "errors": "backslashreplace"}


def test_configure_utf8_runtime_output_is_defensive(monkeypatch):
    """Streams without ``reconfigure`` (or that raise) must never crash the CLI."""

    class _NoReconfigure:
        pass

    class _Raises:
        def reconfigure(self, **kwargs):
            raise ValueError("detached stream")

    monkeypatch.setattr(main_mod.sys, "stdout", _NoReconfigure())
    monkeypatch.setattr(main_mod.sys, "stderr", _Raises())

    main_mod._configure_utf8_runtime_output()  # must not raise
