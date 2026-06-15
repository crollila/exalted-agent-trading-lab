import sys

from src.ui.desktop_app import (
    DEFAULT_WINDOW_TITLE,
    build_desktop_launch_plan,
    build_desktop_streamlit_command,
    launch_desktop_app,
)
from src.ui.launcher import build_streamlit_command, command_contains_secret


def test_launcher_helper_builds_streamlit_command_without_secrets():
    command = build_streamlit_command("src/ui/dashboard.py", port=8502)

    assert command[:3] == [sys.executable, "-m", "streamlit"]
    assert "run" in command
    assert "src\\ui\\dashboard.py" in command or "src/ui/dashboard.py" in command
    assert "--server.port" in command
    assert command_contains_secret(command) is False


def test_launcher_secret_detector_catches_bad_command():
    assert command_contains_secret(["python", "-m", "streamlit", "TOKEN=abc"]) is True
    assert command_contains_secret(["python", "-m", "streamlit", "run", "dashboard.py"]) is False


def test_desktop_streamlit_command_is_local_only_and_secret_free():
    command = build_desktop_streamlit_command("src/ui/dashboard.py", port=8507)

    assert command[:3] == [sys.executable, "-m", "streamlit"]
    assert "--server.address" in command
    assert "127.0.0.1" in command
    assert "--server.port" in command
    assert "8507" in command
    assert command_contains_secret(command) is False


def test_desktop_launch_plan_has_title_and_local_url():
    plan = build_desktop_launch_plan("src/ui/dashboard.py", port=8510)

    assert plan.title == DEFAULT_WINDOW_TITLE
    assert plan.url == "http://127.0.0.1:8510"
    assert command_contains_secret(plan.command) is False


def test_desktop_launch_falls_back_to_browser_without_real_streamlit(monkeypatch):
    calls = {"popen": [], "browser": [], "sleep": []}

    class FakeProcess:
        returncode = None

    def fake_popen(command):
        calls["popen"].append(command)
        return FakeProcess()

    def fake_open(url):
        calls["browser"].append(url)
        return True

    monkeypatch.setattr("src.ui.desktop_app.importlib.util.find_spec", lambda name: None)

    result = launch_desktop_app(
        dashboard_path="src/ui/dashboard.py",
        port=8511,
        popen=fake_popen,
        open_browser=fake_open,
        sleep=lambda seconds: calls["sleep"].append(seconds),
    )

    assert result == 0
    assert calls["popen"]
    assert calls["browser"] == ["http://127.0.0.1:8511"]
    assert calls["sleep"] == [2.0]
