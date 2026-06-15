"""Tests for local Discord-bot process control.

Subprocess launch, liveness checks, and termination are injected, so no real Discord bot is
ever started and no real process is signalled. All file I/O uses temp paths.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from src.ui.process_control import (
    BOT_COMMAND,
    ProcessInfo,
    bot_log_path,
    bot_pid_path,
    bot_status,
    build_bot_process_report,
    detect_bot_processes,
    parse_powershell_process_json,
    parse_ps_output,
    read_pid,
    read_tail,
    restart_discord_bot,
    start_discord_bot,
    stop_all_bot_processes,
    stop_discord_bot,
    write_pid,
)


def test_pid_round_trip_and_malformed(tmp_path):
    pid_path = bot_pid_path(tmp_path)
    assert read_pid(pid_path) is None  # missing file
    write_pid(pid_path, 4321)
    assert read_pid(pid_path) == 4321

    pid_path.write_text("not-a-number", encoding="utf-8")
    assert read_pid(pid_path) is None

    pid_path.write_text("-5", encoding="utf-8")
    assert read_pid(pid_path) is None


def test_bot_status_reports_stopped_running_and_stale(tmp_path):
    # No PID file -> stopped.
    status = bot_status(tmp_path, process_checker=lambda pid: False)
    assert status == status.__class__(running=False, pid=None, stale=False)

    write_pid(bot_pid_path(tmp_path), 999)
    # PID present and alive -> running.
    running = bot_status(tmp_path, process_checker=lambda pid: True)
    assert running.running is True and running.pid == 999 and running.stale is False

    # PID present but dead -> stale.
    stale = bot_status(tmp_path, process_checker=lambda pid: False)
    assert stale.running is False and stale.pid == 999 and stale.stale is True


def test_start_discord_bot_launches_without_secrets(tmp_path):
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=24680)

    result = start_discord_bot(
        runtime_dir=tmp_path,
        popen=fake_popen,
        process_checker=lambda pid: False,
    )

    assert result.ok is True
    assert result.pid == 24680
    # Command is exactly the module invocation; no secrets, tokens, or keys present.
    assert captured["command"] == [sys.executable, *BOT_COMMAND]
    assert captured["command"] == [sys.executable, "-m", "src.main", "discord-bot"]
    joined = " ".join(captured["command"]).upper()
    for forbidden_fragment in ("TOKEN", "SECRET", "KEY", "PASSWORD"):
        assert forbidden_fragment not in joined
    # PID file written for later status/stop.
    assert read_pid(bot_pid_path(tmp_path)) == 24680


def test_start_discord_bot_refuses_double_start(tmp_path):
    write_pid(bot_pid_path(tmp_path), 111)

    def fake_popen(command, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("must not launch a second bot when one is running")

    result = start_discord_bot(
        runtime_dir=tmp_path,
        popen=fake_popen,
        process_checker=lambda pid: True,  # existing process is alive
    )
    assert result.ok is False
    assert "already" in result.message.lower()
    assert result.pid == 111


def test_stop_discord_bot_terminates_by_pid(tmp_path):
    write_pid(bot_pid_path(tmp_path), 555)
    terminated = []

    result = stop_discord_bot(
        runtime_dir=tmp_path,
        process_checker=lambda pid: True,
        terminator=terminated.append,
    )

    assert result.ok is True
    assert terminated == [555]
    assert read_pid(bot_pid_path(tmp_path)) is None  # PID file cleared


def test_stop_discord_bot_clears_stale_pid(tmp_path):
    write_pid(bot_pid_path(tmp_path), 777)

    def terminator(pid):  # pragma: no cover - must not be called for a dead process
        raise AssertionError("must not terminate a process that is not running")

    result = stop_discord_bot(
        runtime_dir=tmp_path,
        process_checker=lambda pid: False,  # not actually running
        terminator=terminator,
    )
    assert result.ok is False
    assert "stale" in result.message.lower()
    assert read_pid(bot_pid_path(tmp_path)) is None


def test_stop_discord_bot_without_pid_file(tmp_path):
    result = stop_discord_bot(
        runtime_dir=tmp_path,
        process_checker=lambda pid: True,
        terminator=lambda pid: None,
    )
    assert result.ok is False
    assert "no pid file" in result.message.lower()


def test_read_tail_returns_last_lines_and_redacts(tmp_path):
    log_path = bot_log_path(tmp_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"line {i}" for i in range(50)]
    lines.append("DISCORD_BOT_TOKEN=should-not-render")
    log_path.write_text("\n".join(lines), encoding="utf-8")

    tail = read_tail(log_path, lines=5)
    assert tail is not None
    assert "line 0" not in tail  # older lines dropped
    assert "line 49" in tail
    assert "should-not-render" not in tail  # secret redacted
    assert "********" in tail


def test_read_tail_missing_file(tmp_path):
    assert read_tail(tmp_path / "nope.log") is None


# ---------------------------------------------------------------------------
# Process scanning / detection
# ---------------------------------------------------------------------------
def test_parse_powershell_process_json_handles_array_and_single():
    array_text = (
        '[{"ProcessId": 10, "CommandLine": "python -m src.main discord-bot"},'
        ' {"ProcessId": 11, "CommandLine": "python -m src.main dashboard"}]'
    )
    procs = parse_powershell_process_json(array_text)
    assert [(p.pid, p.commandline) for p in procs] == [
        (10, "python -m src.main discord-bot"),
        (11, "python -m src.main dashboard"),
    ]
    # ConvertTo-Json emits a bare object for a single match.
    single = parse_powershell_process_json('{"ProcessId": 12, "CommandLine": "python -m src.main discord-bot"}')
    assert single[0].pid == 12
    # Null command lines and malformed input degrade gracefully.
    assert parse_powershell_process_json("") == []
    assert parse_powershell_process_json("not json") == []


def test_parse_ps_output_extracts_pid_and_commandline():
    text = "  100 python -m src.main discord-bot\n  200 /usr/bin/python -m src.main dashboard\nbad line\n"
    procs = parse_ps_output(text)
    assert (procs[0].pid, procs[0].commandline) == (100, "python -m src.main discord-bot")
    assert procs[1].pid == 200


def test_detect_bot_processes_matches_marker_and_excludes_self():
    def fake_scanner():
        return [
            ProcessInfo(101, "python -m src.main discord-bot"),
            ProcessInfo(102, "python -m src.main dashboard"),  # not the bot
            ProcessInfo(103, "C:\\python.exe -m src.main discord-bot --extra"),
            ProcessInfo(os.getpid(), "python -m src.main discord-bot"),  # self -> excluded
        ]

    detected = detect_bot_processes(scanner=fake_scanner)
    assert 101 in detected
    assert 103 in detected
    assert 102 not in detected
    assert os.getpid() not in detected


def test_build_bot_process_report_flags_untracked_running(tmp_path):
    # No PID file, but a bot is detected externally.
    report = build_bot_process_report(
        tmp_path,
        process_checker=lambda pid: False,
        detector=lambda: [4242],
    )
    assert report.pid_file_pid is None
    assert report.pid_file_running is False
    assert report.detected_pids == (4242,)
    assert report.any_running is True
    assert report.untracked_running is True


def test_start_refuses_when_detection_finds_existing_bot(tmp_path):
    def fake_popen(command, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("must not start a bot when one is detected")

    result = start_discord_bot(
        runtime_dir=tmp_path,
        popen=fake_popen,
        process_checker=lambda pid: False,  # no PID file
        detector=lambda: [9090],  # but a bot is detected
    )
    assert result.ok is False
    assert "9090" in result.message


def test_stop_all_bot_processes_terminates_each_detected(tmp_path):
    write_pid(bot_pid_path(tmp_path), 1)
    terminated = []
    result = stop_all_bot_processes(
        runtime_dir=tmp_path,
        detector=lambda: [11, 22, 33],
        terminator=terminated.append,
    )
    assert result.ok is True
    assert terminated == [11, 22, 33]
    # PID file cleared regardless.
    assert read_pid(bot_pid_path(tmp_path)) is None


def test_stop_all_bot_processes_when_none_detected(tmp_path):
    result = stop_all_bot_processes(
        runtime_dir=tmp_path,
        detector=lambda: [],
        terminator=lambda pid: None,
    )
    assert result.ok is False
    assert "no running" in result.message.lower()


def test_restart_stops_detected_then_starts_one(tmp_path):
    terminated = []
    started = {}

    def fake_popen(command, **kwargs):
        started["command"] = command
        return SimpleNamespace(pid=5151)

    result = restart_discord_bot(
        runtime_dir=tmp_path,
        popen=fake_popen,
        process_checker=lambda pid: False,
        detector=lambda: [70, 71],
        terminator=terminated.append,
    )
    assert terminated == [70, 71]  # stopped first
    assert result.ok is True
    assert result.pid == 5151
    assert started["command"][-3:] == ["-m", "src.main", "discord-bot"]
