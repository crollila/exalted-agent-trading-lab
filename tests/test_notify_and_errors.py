"""Error reporting: file log always written, Discord optional, never raises."""

from __future__ import annotations

from src.notify import discord_configured, errors_log_path, post_discord, recent_errors, report_error


def test_report_error_writes_log_without_discord(settings):
    assert not discord_configured(settings)  # test settings have no token
    report_error(settings, "team_alpha cycle", "something broke")
    report_error(settings, "loop", "another thing")

    lines = recent_errors(settings, count=5)
    assert len(lines) == 2
    assert "team_alpha cycle" in lines[0] and "something broke" in lines[0]
    assert "loop" in lines[1]


def test_recent_errors_returns_tail(settings):
    for i in range(10):
        report_error(settings, "loop", f"error {i}")
    lines = recent_errors(settings, count=3)
    assert len(lines) == 3
    assert lines[-1].endswith("error 9")


def test_recent_errors_empty_when_no_log(settings):
    assert recent_errors(settings) == []


def test_post_discord_noop_when_unconfigured(settings):
    # Must return False and make no network call (requests import happens after guard).
    assert post_discord(settings, "hello") is False


def test_post_discord_posts_when_configured(settings, monkeypatch):
    from dataclasses import replace

    configured = replace(settings, discord_bot_token="tok", discord_channel_id="123")

    calls = {}

    class FakeResponse:
        status_code = 200

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["auth"] = headers.get("Authorization")
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    assert post_discord(configured, "x" * 3000) is True
    assert calls["url"].endswith("/channels/123/messages")
    assert calls["auth"] == "Bot tok"
    assert len(calls["json"]["content"]) <= 1900  # truncated to Discord limit


def test_report_error_never_raises_on_unwritable_log(settings, monkeypatch):
    # Point the log at a path whose parent is a FILE -> mkdir fails.
    blocker = settings.data_dir
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("i am a file", encoding="utf-8")
    report_error(settings, "loop", "should not raise")  # no exception = pass
    assert errors_log_path(settings).parent == blocker / "runtime"
