"""Operator controls for the Arena Command Center (Phase 7Q).

Safe process control + command builders for the cheap competition loop and the
advisory operator actions, reusing the existing :mod:`src.ui.process_control`
primitives rather than duplicating any logic.

Hard safety properties:

* The cheap loop is launched as ``python -m src.main run-cheap-competition-loop ...``
  — the same gated CLI path. No broker function is ever called from the UI, no
  secrets are ever placed on the command line, and PID/log files live under the
  ignored ``data/runtime`` path.
* The loop itself never submits orders unless ``run-week-cycle`` is invoked inside
  it, which keeps every deterministic risk / approval / kill-switch gate.
* Operator actions (refresh attribution, LLM daily review, dry-run) shell out to the
  existing CLI; the daily review submits no orders by design.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.ui.process_control import (
    BotActionResult,
    BotStatus,
    DEFAULT_RUNTIME_DIR,
    ProcessInfo,
    _default_terminate,
    is_process_running,
    read_pid,
    read_tail,
    scan_processes,
    write_pid,
)

# Command markers (never include secrets).
CHEAP_LOOP_COMMAND_TAIL = ("-m", "src.main", "run-cheap-competition-loop")
CHEAP_LOOP_PROCESS_MARKERS = ("src.main", "run-cheap-competition-loop")


def cheap_loop_pid_path(runtime_dir: Path | str = DEFAULT_RUNTIME_DIR) -> Path:
    return Path(runtime_dir) / "cheap_loop.pid"


def cheap_loop_log_path(runtime_dir: Path | str = DEFAULT_RUNTIME_DIR) -> Path:
    return Path(runtime_dir) / "cheap_loop.log"


# ---------------------------------------------------------------------------
# Safe command builders (pure; no secrets)
# ---------------------------------------------------------------------------
def build_cheap_loop_command(
    *,
    sleep_seconds: int = 900,
    team: str = "both",
    llm_review_when_skipped: bool = True,
    python_executable: str | None = None,
) -> list[str]:
    """Build the background cheap-loop command. No secrets on the command line."""

    command = [python_executable or sys.executable, *CHEAP_LOOP_COMMAND_TAIL,
               "--sleep-seconds", str(int(sleep_seconds)), "--team", team]
    if llm_review_when_skipped:
        command.append("--llm-review-when-skipped")
    return command


def build_cheap_loop_dry_run_command(
    *,
    llm_review_when_skipped: bool = True,
    python_executable: str | None = None,
) -> list[str]:
    """Build the single-iteration dry-run command (prints intentions; no orders)."""

    command = [python_executable or sys.executable, *CHEAP_LOOP_COMMAND_TAIL,
               "--once", "--dry-run-loop"]
    if llm_review_when_skipped:
        command.append("--llm-review-when-skipped")
    return command


def build_refresh_attribution_command(*, python_executable: str | None = None) -> list[str]:
    return [python_executable or sys.executable, "-m", "src.main", "refresh-proposal-attribution"]


def build_llm_daily_review_command(team: str = "both", *, python_executable: str | None = None) -> list[str]:
    """Build the advisory LLM daily review command (submits no orders)."""

    command = [python_executable or sys.executable, "-m", "src.main", "run-llm-daily-review"]
    if team in ("team_alpha", "team_beta"):
        command.extend(["--team", team])
    return command


def command_has_secret(command: list[str]) -> bool:
    joined = " ".join(command).upper()
    return any(marker in joined for marker in ("SECRET", "TOKEN", "API_KEY", "PASSWORD", "PASSWD"))


def _utf8_child_env() -> dict[str, str]:
    """Environment for background children so their redirected output stays UTF-8.

    Mirrors the parent environment but forces Python UTF-8 mode and UTF-8 stdio so
    the child's writes to the UTF-8 ``cheap_loop.log`` never fall back to the
    Windows locale code page (cp1252) and crash on symbols like ``≈``. Set from
    interpreter startup, this protects even output emitted before ``main()`` runs.
    """

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------------------
# Cheap-loop process management (reuses process_control primitives)
# ---------------------------------------------------------------------------
def _match_cheap_loop(commandline: str) -> bool:
    lowered = commandline.lower()
    return all(marker in lowered for marker in CHEAP_LOOP_PROCESS_MARKERS)


def detect_cheap_loop_processes(
    *,
    scanner: Callable[[], list[ProcessInfo]] = scan_processes,
) -> list[int]:
    """Detect PIDs of any running cheap-loop process, however it was started."""

    import os

    excluded = {os.getpid()}
    found: set[int] = set()
    for process in scanner():
        if process.pid in excluded:
            continue
        if _match_cheap_loop(process.commandline):
            found.add(process.pid)
    return sorted(found)


def cheap_loop_status(
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    *,
    process_checker: Callable[[int | None], bool] = is_process_running,
) -> BotStatus:
    """Report whether the cheap loop appears running (with stale-PID detection)."""

    pid = read_pid(cheap_loop_pid_path(runtime_dir))
    if pid is None:
        return BotStatus(running=False, pid=None, stale=False)
    alive = process_checker(pid)
    return BotStatus(running=alive, pid=pid, stale=not alive)


def start_cheap_loop(
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    sleep_seconds: int = 900,
    team: str = "both",
    llm_review_when_skipped: bool = True,
    python_executable: str | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    process_checker: Callable[[int | None], bool] = is_process_running,
    detector: Callable[[], list[int]] = detect_cheap_loop_processes,
) -> BotActionResult:
    """Start the cheap competition loop as a background process.

    Refuses if a cheap loop already appears to run (tracked PID alive or detected by
    a system scan). The command is the gated CLI — no secrets, no broker calls here.
    """

    runtime = Path(runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    pid_path = cheap_loop_pid_path(runtime)

    existing = read_pid(pid_path)
    if existing is not None and process_checker(existing):
        return BotActionResult(False, f"Cheap loop already appears to be running (PID {existing}).", existing)
    detected = detector()
    if detected:
        return BotActionResult(
            False,
            f"A cheap-loop process already appears to be running (detected PID(s) {detected}). Stop it first.",
            detected[0],
        )

    command = build_cheap_loop_command(
        sleep_seconds=sleep_seconds,
        team=team,
        llm_review_when_skipped=llm_review_when_skipped,
        python_executable=python_executable,
    )
    if command_has_secret(command):  # pragma: no cover - defensive; command never has secrets
        return BotActionResult(False, "Refusing to start: command contains secret-looking text.")
    log_path = cheap_loop_log_path(runtime)
    log_handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - handed to child process
    try:
        process = popen(command, stdout=log_handle, stderr=subprocess.STDOUT, env=_utf8_child_env())
    except Exception as exc:  # pragma: no cover - launch failure path
        log_handle.close()
        return BotActionResult(False, f"Failed to start cheap loop: {exc}")
    write_pid(pid_path, process.pid)
    return BotActionResult(True, f"Started cheap loop (PID {process.pid}).", process.pid)


def stop_cheap_loop(
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    process_checker: Callable[[int | None], bool] = is_process_running,
    terminator: Callable[[int], None] = _default_terminate,
) -> BotActionResult:
    """Stop the tracked cheap loop; clear a stale PID file safely if not running."""

    pid_path = cheap_loop_pid_path(runtime_dir)
    pid = read_pid(pid_path)
    if pid is None:
        return BotActionResult(False, "No cheap-loop PID file found; it does not appear to be running.")
    if not process_checker(pid):
        pid_path.unlink(missing_ok=True)
        return BotActionResult(False, f"Cleared stale PID {pid}; that process was not running.", pid)
    try:
        terminator(pid)
    except Exception as exc:  # pragma: no cover - termination failure path
        return BotActionResult(False, f"Failed to stop PID {pid}: {exc}", pid)
    pid_path.unlink(missing_ok=True)
    return BotActionResult(True, f"Stop signal sent to cheap loop (PID {pid}).", pid)


# ---------------------------------------------------------------------------
# Synchronous CLI runner (dry-run, refresh, daily review) — never submits orders
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CliRunResult:
    ok: bool
    output: str


def run_cli_command(
    command: list[str],
    *,
    runner: Callable[..., object] = subprocess.run,
    timeout: int = 600,
) -> CliRunResult:
    """Run a safe CLI command synchronously and return redacted, truncated output.

    Used for dry-run / refresh / advisory daily review. No broker calls happen here;
    the invoked CLI keeps all of its own gates.
    """

    from src.ui.dashboard_state import redact_secret_like_text

    if command_has_secret(command):  # pragma: no cover - defensive
        return CliRunResult(False, "Refusing to run: command contains secret-looking text.")
    try:
        completed = runner(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - surface failure without crashing the UI
        return CliRunResult(False, f"Command failed to run: {exc}")
    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    code = getattr(completed, "returncode", 0)
    text = redact_secret_like_text((stdout + ("\n" + stderr if stderr else "")).strip())
    if len(text) > 16000:
        text = text[-16000:]
    return CliRunResult(ok=(code == 0), output=text or "(no output)")


def run_dry_run_cheap_loop(
    *,
    runner: Callable[..., object] = subprocess.run,
    python_executable: str | None = None,
) -> CliRunResult:
    """Run a single dry-run cheap-loop iteration (prints intentions; no orders)."""

    return run_cli_command(
        build_cheap_loop_dry_run_command(python_executable=python_executable),
        runner=runner,
    )


def cheap_loop_log_tail(runtime_dir: Path | str = DEFAULT_RUNTIME_DIR, *, lines: int = 120) -> str | None:
    """Read the cheap-loop log tail (redacted + truncated). None when no log yet."""

    return read_tail(cheap_loop_log_path(runtime_dir), lines=lines)


# ---------------------------------------------------------------------------
# Streamlit render (thin) — gated by Operator/Expert mode at the call site
# ---------------------------------------------------------------------------
def render_operator_bot_controls(st, *, is_operator: bool, is_expert: bool) -> None:
    """Render the operator bot/loop controls + safe advisory actions.

    Only call this from the Operator page when Operator Mode is active. Kill-switch
    OFF is additionally guarded behind Expert Operator Mode with a strong warning.
    """

    st.subheader("Cheap competition loop (recommended over the old 15-min full loop)")
    status = cheap_loop_status()
    if status.running:
        st.success(f"Cheap loop is RUNNING (PID {status.pid}).")
    elif status.stale:
        st.warning(f"Cheap loop PID {status.pid} is stale (process not found).")
    else:
        st.info("Cheap loop is stopped.")

    if not is_operator:
        st.caption("Switch to Operator Mode to start/stop the cheap loop. Demo Mode is read-only.")
        return

    cols = st.columns(3)
    if cols[0].button("▶ Start cheap loop (background)"):
        result = start_cheap_loop()
        (st.success if result.ok else st.warning)(result.message)
    if cols[1].button("■ Stop cheap loop"):
        result = stop_cheap_loop()
        (st.success if result.ok else st.warning)(result.message)
    if cols[2].button("Dry-run one loop (no orders)"):
        result = run_dry_run_cheap_loop()
        (st.success if result.ok else st.warning)("Dry-run complete.")
        with st.expander("Dry-run output", expanded=True):
            st.code(result.output)

    st.divider()
    st.subheader("Advisory actions (submit no orders)")
    acols = st.columns(2)
    if acols[0].button("Refresh attribution"):
        result = run_cli_command(build_refresh_attribution_command())
        (st.success if result.ok else st.warning)("Attribution refresh complete.")
        if is_expert:
            with st.expander("Output", expanded=False):
                st.code(result.output)
    review_team = acols[1].selectbox("LLM daily review team", ("both", "team_alpha", "team_beta"))
    if acols[1].button("Run LLM daily review (no orders)"):
        result = run_cli_command(build_llm_daily_review_command(review_team))
        (st.success if result.ok else st.warning)("LLM daily review complete (no orders submitted).")
        if is_expert:
            with st.expander("Output", expanded=False):
                st.code(result.output)

    st.divider()
    _render_discord_iteration_updates_status(st)

    if is_expert:
        tail = cheap_loop_log_tail()
        if tail:
            with st.expander("Cheap loop log (tail, redacted)", expanded=False):
                st.code(tail)


def _render_discord_iteration_updates_status(st) -> None:
    """Show Phase 7S Discord iteration-update status (no channel IDs, redacted)."""

    st.subheader("Discord team-thought updates (Phase 7S)")
    try:
        from src.discord_bot.competition_updates import iteration_updates_status

        status = iteration_updates_status()
    except Exception as exc:  # noqa: BLE001 - status panel must never crash the UI
        st.caption(f"Discord iteration-update status unavailable: {exc}")
        return

    if status["enabled"]:
        st.success("Iteration updates ENABLED (posts per loop iteration).")
    else:
        st.info("Iteration updates disabled (set ENABLE_DISCORD_ITERATION_UPDATES=true to enable).")
    st.caption(
        f"Bot token configured: {status['token_configured']} | "
        f"min interval: {status['min_interval_seconds']}s | "
        f"competition summary: {status['post_competition_summary']} "
        f"(channel configured: {status['summary_channel_configured']})"
    )
    for team_id, info in status["teams"].items():
        last = info.get("last_update_at") or "never"
        err = info.get("last_error")
        line = (
            f"- {team_id}: channel configured {info['channel_configured']} | "
            f"last update {last}"
        )
        st.caption(line + (f" | last error: {err}" if err else ""))
