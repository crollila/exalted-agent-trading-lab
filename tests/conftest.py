"""Shared pytest fixtures for the trading-lab test suite."""

from __future__ import annotations

import pytest

import src.discord_bot.bot as discord_bot


@pytest.fixture(autouse=True)
def isolate_runtime_team_autonomy_config(tmp_path, monkeypatch):
    """Keep unit tests hermetic from any developer runtime autonomy override file.

    ``DiscordBotConfig.from_env`` overlays the persisted runtime autonomy config at
    ``DEFAULT_AUTONOMY_CONFIG_PATH`` (``data/notes/team_autonomy_config.json``) on top of
    env-derived config. When a real runtime file exists (e.g. after a manual Discord
    autonomy toggle), it silently overrides env-based expectations in tests. Point the
    default at a non-existent temp path so tests never read live ``data/`` state; any test
    that genuinely needs an override still passes ``DISCORD_AUTONOMY_CONFIG_PATH`` explicitly.
    """

    hermetic_path = tmp_path / "no_runtime_autonomy_override.json"
    monkeypatch.setattr(discord_bot, "DEFAULT_AUTONOMY_CONFIG_PATH", hermetic_path)
    yield


@pytest.fixture(autouse=True)
def isolate_loop_audit_dir(tmp_path, monkeypatch):
    """Keep the per-iteration loop audit log (Phase 7U) out of the real runtime dir.

    Any test that exercises ``run_cheap_competition_loop`` writes one audit record
    per team per iteration; without this fixture those land in the real
    ``data/runtime/loop_audit`` path. Redirect the writer via ``LOOP_AUDIT_DIR`` so
    tests never pollute developer runtime state.
    """

    monkeypatch.setenv("LOOP_AUDIT_DIR", str(tmp_path / "loop_audit"))
    yield


@pytest.fixture(autouse=True)
def isolate_loop_heartbeat(tmp_path, monkeypatch):
    """Keep the loop heartbeat (Phase 7W) out of the real runtime dir during tests."""

    monkeypatch.setenv("LOOP_HEARTBEAT_PATH", str(tmp_path / "loop_heartbeat.json"))
    yield
