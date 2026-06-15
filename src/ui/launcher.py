"""Command Center launcher helpers.

The launcher only starts the Streamlit dashboard. It does not embed secrets, submit
orders, or replace the trading engine.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DASHBOARD_PATH = Path("src/ui/dashboard.py")


@dataclass(frozen=True)
class LauncherCheck:
    ok: bool
    message: str


def build_streamlit_command(
    dashboard_path: Path | str = DEFAULT_DASHBOARD_PATH,
    *,
    port: int | None = None,
) -> list[str]:
    """Build the Streamlit command without secrets."""

    command = [sys.executable, "-m", "streamlit", "run", str(Path(dashboard_path))]
    if port is not None:
        command.extend(["--server.port", str(int(port))])
    return command


def command_contains_secret(command: list[str]) -> bool:
    """Detect whether a command accidentally includes secret-looking flags/values."""

    joined = " ".join(command).upper()
    return any(marker in joined for marker in ("SECRET", "TOKEN", "API_KEY", "PASSWORD", "PASSWD"))


def check_launcher_dependencies() -> list[LauncherCheck]:
    """Check local Python dependencies needed by the launcher."""

    checks = []
    checks.append(
        LauncherCheck(
            importlib.util.find_spec("streamlit") is not None,
            "Streamlit installed" if importlib.util.find_spec("streamlit") is not None else "Install Streamlit with pip install -r requirements.txt",
        )
    )
    checks.append(
        LauncherCheck(
            DEFAULT_DASHBOARD_PATH.is_file(),
            f"Dashboard found at {DEFAULT_DASHBOARD_PATH}" if DEFAULT_DASHBOARD_PATH.is_file() else f"Missing {DEFAULT_DASHBOARD_PATH}",
        )
    )
    return checks


def launch_command_center(
    *,
    dashboard_path: Path | str = DEFAULT_DASHBOARD_PATH,
    port: int | None = None,
    open_browser: bool = True,
) -> int:
    """Launch Streamlit and return the process exit code."""

    checks = check_launcher_dependencies()
    failed = [check.message for check in checks if not check.ok]
    if failed:
        for message in failed:
            print(message)
        return 1

    command = build_streamlit_command(dashboard_path, port=port)
    if command_contains_secret(command):
        print("Refusing to launch because the command contains secret-looking text.")
        return 1
    if open_browser and port is not None:
        webbrowser.open(f"http://localhost:{int(port)}")
    print("Starting ExaltedFable Command Center (paper-only; no live trading).")
    print("Command: " + " ".join(command))
    return subprocess.call(command)
