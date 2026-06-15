"""Local Discord-bot process control for the operator console.

Lets a local user start/stop/inspect the Discord bot without a terminal. Designed to be
testable: subprocess launch, process liveness checks, and termination are injectable, so
tests use temp paths and mocks and never start a real bot.

Safety:
- The bot is launched as ``python -m src.main discord-bot`` — no secrets are ever passed on
  the command line. The bot reads its own local ``.env`` as usual.
- PID and log files live under an ignored runtime path (``data/runtime``) and are never
  committed. Log output is redacted before display.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from src.ui.dashboard_state import redact_secret_like_text

DEFAULT_RUNTIME_DIR = Path("data/runtime")
BOT_COMMAND = ("-m", "src.main", "discord-bot")  # never includes secrets
# A command line is a bot process if it contains all of these (case-insensitive).
BOT_PROCESS_MARKERS = ("src.main", "discord-bot")


def bot_pid_path(runtime_dir: Path | str = DEFAULT_RUNTIME_DIR) -> Path:
    return Path(runtime_dir) / "discord_bot.pid"


def bot_log_path(runtime_dir: Path | str = DEFAULT_RUNTIME_DIR) -> Path:
    return Path(runtime_dir) / "discord_bot.log"


def read_pid(pid_path: Path | str) -> int | None:
    """Read a PID from the PID file; return None if missing or malformed."""

    path = Path(pid_path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def write_pid(pid_path: Path | str, pid: int) -> Path:
    path = Path(pid_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(pid)), encoding="utf-8")
    return path


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    commandline: str


def _match_bot_commandline(commandline: str) -> bool:
    lowered = commandline.lower()
    return all(marker in lowered for marker in BOT_PROCESS_MARKERS)


def parse_powershell_process_json(text: str) -> list[ProcessInfo]:
    """Parse ``Get-CimInstance Win32_Process | ... | ConvertTo-Json`` output."""

    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    processes: list[ProcessInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pid = item.get("ProcessId")
        commandline = item.get("CommandLine") or ""
        if isinstance(pid, int):
            processes.append(ProcessInfo(pid=pid, commandline=str(commandline)))
    return processes


def parse_ps_output(text: str) -> list[ProcessInfo]:
    """Parse ``ps -eo pid=,args=`` output into ProcessInfo records."""

    processes: list[ProcessInfo] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        try:
            pid = int(parts[0])
        except (ValueError, IndexError):
            continue
        commandline = parts[1] if len(parts) > 1 else ""
        processes.append(ProcessInfo(pid=pid, commandline=commandline))
    return processes


def scan_processes(*, runner: Callable[..., object] = subprocess.run) -> list[ProcessInfo]:
    """Return running processes with command lines, using a stdlib platform command.

    Windows uses PowerShell CIM; POSIX uses ``ps``. No third-party dependency. Failures
    return an empty list so the UI degrades gracefully.
    """

    try:
        if os.name == "nt":  # pragma: no cover - Windows-only path
            completed = runner(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return parse_powershell_process_json(getattr(completed, "stdout", "") or "")
        completed = runner(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return parse_ps_output(getattr(completed, "stdout", "") or "")
    except Exception:  # pragma: no cover - defensive
        return []


def detect_bot_processes(
    *,
    scanner: Callable[[], list[ProcessInfo]] = scan_processes,
    exclude_pids: Iterable[int] | None = None,
) -> list[int]:
    """Detect PIDs of any running ``src.main discord-bot`` process, however it was started.

    Excludes the current process so the dashboard never reports itself.
    """

    excluded = set(exclude_pids or ()) | {os.getpid()}
    found: set[int] = set()
    for process in scanner():
        if process.pid in excluded:
            continue
        if _match_bot_commandline(process.commandline):
            found.add(process.pid)
    return sorted(found)


def is_process_running(pid: int | None) -> bool:
    """Best-effort cross-platform check for whether a PID is an active process."""

    if pid is None or pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - exercised only on Windows at runtime
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _default_terminate(pid: int) -> None:  # pragma: no cover - real OS termination
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            check=False,
            capture_output=True,
        )
    else:
        os.kill(pid, signal.SIGTERM)


@dataclass(frozen=True)
class BotStatus:
    running: bool
    pid: int | None
    stale: bool


def bot_status(
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    *,
    process_checker: Callable[[int | None], bool] = is_process_running,
) -> BotStatus:
    """Report whether the bot appears running, including stale-PID detection."""

    pid = read_pid(bot_pid_path(runtime_dir))
    if pid is None:
        return BotStatus(running=False, pid=None, stale=False)
    alive = process_checker(pid)
    return BotStatus(running=alive, pid=pid, stale=not alive)


@dataclass(frozen=True)
class BotProcessReport:
    pid_file_pid: int | None
    pid_file_running: bool
    detected_pids: tuple[int, ...]

    @property
    def any_running(self) -> bool:
        return self.pid_file_running or bool(self.detected_pids)

    @property
    def untracked_running(self) -> bool:
        """True when no UI-tracked PID is alive but another bot process is detected."""

        return not self.pid_file_running and bool(self.detected_pids)


def build_bot_process_report(
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    *,
    process_checker: Callable[[int | None], bool] = is_process_running,
    detector: Callable[[], list[int]] = detect_bot_processes,
) -> BotProcessReport:
    """Combine PID-file status with a system-wide scan for bot processes."""

    pid = read_pid(bot_pid_path(runtime_dir))
    pid_running = process_checker(pid) if pid is not None else False
    detected = tuple(detector())
    return BotProcessReport(pid_file_pid=pid, pid_file_running=pid_running, detected_pids=detected)


@dataclass(frozen=True)
class BotActionResult:
    ok: bool
    message: str
    pid: int | None = None


def start_discord_bot(
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    python_executable: str | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    process_checker: Callable[[int | None], bool] = is_process_running,
    detector: Callable[[], list[int]] = detect_bot_processes,
) -> BotActionResult:
    """Start the Discord bot via subprocess, refusing if any bot already appears to run.

    Refuses when the saved PID is alive *or* a system scan detects an existing
    ``src.main discord-bot`` process (e.g. started from a terminal). The command is
    ``<python> -m src.main discord-bot`` — no secrets on the command line. stdout/stderr are
    appended to the local log file.
    """

    runtime = Path(runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    pid_path = bot_pid_path(runtime)

    existing = read_pid(pid_path)
    if existing is not None and process_checker(existing):
        return BotActionResult(False, f"Discord bot already appears to be running (PID {existing}).", existing)

    detected = detector()
    if detected:
        return BotActionResult(
            False,
            f"A Discord bot process already appears to be running (detected PID(s) {detected}). "
            "Stop it first.",
            detected[0],
        )

    command = [python_executable or sys.executable, *BOT_COMMAND]
    log_path = bot_log_path(runtime)
    log_handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - handed to child process
    try:
        process = popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
    except Exception as exc:  # pragma: no cover - launch failure path
        log_handle.close()
        return BotActionResult(False, f"Failed to start Discord bot: {exc}")
    write_pid(pid_path, process.pid)
    return BotActionResult(True, f"Started Discord bot (PID {process.pid}).", process.pid)


def stop_discord_bot(
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    process_checker: Callable[[int | None], bool] = is_process_running,
    terminator: Callable[[int], None] = _default_terminate,
) -> BotActionResult:
    """Stop the bot by PID if it is running; clear a stale PID file otherwise."""

    pid_path = bot_pid_path(runtime_dir)
    pid = read_pid(pid_path)
    if pid is None:
        return BotActionResult(False, "No PID file found; the bot does not appear to be running.")
    if not process_checker(pid):
        pid_path.unlink(missing_ok=True)
        return BotActionResult(False, f"Cleared stale PID {pid}; that process was not running.", pid)
    try:
        terminator(pid)
    except Exception as exc:  # pragma: no cover - termination failure path
        return BotActionResult(False, f"Failed to stop PID {pid}: {exc}", pid)
    pid_path.unlink(missing_ok=True)
    return BotActionResult(True, f"Stop signal sent to Discord bot (PID {pid}).", pid)


def stop_all_bot_processes(
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    detector: Callable[[], list[int]] = detect_bot_processes,
    terminator: Callable[[int], None] = _default_terminate,
) -> BotActionResult:
    """Stop every detected ``src.main discord-bot`` process, however it was started."""

    pids = detector()
    bot_pid_path(runtime_dir).unlink(missing_ok=True)
    if not pids:
        return BotActionResult(False, "No running Discord bot processes detected.")
    stopped: list[int] = []
    errors: list[str] = []
    for pid in pids:
        try:
            terminator(pid)
            stopped.append(pid)
        except Exception as exc:  # pragma: no cover - termination failure path
            errors.append(f"{pid}: {exc}")
    if errors:
        return BotActionResult(False, f"Stopped {stopped}; failed to stop {errors}.")
    return BotActionResult(True, f"Stopped {len(stopped)} detected Discord bot process(es): {stopped}.")


def restart_discord_bot(
    *,
    runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
    python_executable: str | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    process_checker: Callable[[int | None], bool] = is_process_running,
    detector: Callable[[], list[int]] = detect_bot_processes,
    terminator: Callable[[int], None] = _default_terminate,
) -> BotActionResult:
    """Stop all detected bot processes, then start exactly one fresh bot."""

    stop_all_bot_processes(runtime_dir=runtime_dir, detector=detector, terminator=terminator)
    # Bypass detection on the immediate restart: just-terminated PIDs may linger briefly.
    return start_discord_bot(
        runtime_dir=runtime_dir,
        python_executable=python_executable,
        popen=popen,
        process_checker=process_checker,
        detector=lambda: [],
    )


def read_tail(
    path: Path | str,
    *,
    lines: int = 200,
    max_chars: int = 20000,
    redact: bool = True,
) -> str | None:
    """Return the last ``lines`` of a log file, secret-redacted and truncated."""

    file_path = Path(path)
    if not file_path.is_file():
        return None
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    tail = "\n".join(text.splitlines()[-lines:])
    if redact:
        tail = redact_secret_like_text(tail)
    if len(tail) > max_chars:
        tail = "... (truncated)\n" + tail[-max_chars:]
    return tail
