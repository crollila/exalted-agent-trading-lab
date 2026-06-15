"""Optional desktop-style wrapper for the Streamlit dashboard.

The product still runs Streamlit internally. When pywebview is installed, this module opens
the local dashboard in a desktop window; otherwise it falls back to the default browser.
No secrets are passed on the command line and the server binds to localhost only.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.ui.launcher import command_contains_secret


DEFAULT_DESKTOP_PORT = 8507
DEFAULT_WINDOW_TITLE = "ExaltedFable Command Center"
DEFAULT_DASHBOARD_PATH = Path("src/ui/dashboard.py")


@dataclass(frozen=True)
class DesktopLaunchPlan:
    command: list[str]
    url: str
    title: str
    pywebview_available: bool


def build_desktop_streamlit_command(
    dashboard_path: Path | str = DEFAULT_DASHBOARD_PATH,
    *,
    port: int = DEFAULT_DESKTOP_PORT,
) -> list[str]:
    """Build a local-only Streamlit command for the desktop wrapper."""

    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(Path(dashboard_path)),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(int(port)),
        "--browser.gatherUsageStats",
        "false",
    ]


def build_desktop_launch_plan(
    dashboard_path: Path | str = DEFAULT_DASHBOARD_PATH,
    *,
    port: int = DEFAULT_DESKTOP_PORT,
    title: str = DEFAULT_WINDOW_TITLE,
) -> DesktopLaunchPlan:
    """Return launch metadata without starting Streamlit."""

    command = build_desktop_streamlit_command(dashboard_path, port=port)
    return DesktopLaunchPlan(
        command=command,
        url=f"http://127.0.0.1:{int(port)}",
        title=title,
        pywebview_available=importlib.util.find_spec("webview") is not None,
    )


def launch_desktop_app(
    *,
    dashboard_path: Path | str = DEFAULT_DASHBOARD_PATH,
    port: int = DEFAULT_DESKTOP_PORT,
    title: str = DEFAULT_WINDOW_TITLE,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    open_browser: Callable[[str], bool] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Start Streamlit and open a desktop window or browser fallback."""

    plan = build_desktop_launch_plan(dashboard_path, port=port, title=title)
    if command_contains_secret(plan.command):
        print("Refusing to launch because the command contains secret-looking text.")
        return 1

    print("Starting ExaltedFable desktop dashboard (paper-only; local-only).")
    process = popen(plan.command)
    sleep(2.0)

    if plan.pywebview_available:
        import webview  # type: ignore[import-not-found]

        webview.create_window(plan.title, plan.url, width=1440, height=980)
        webview.start()
    else:
        print("Desktop wrapper optional. Install with pip install pywebview.")
        print(f"Opening dashboard in your browser: {plan.url}")
        open_browser(plan.url)

    return getattr(process, "returncode", None) or 0
