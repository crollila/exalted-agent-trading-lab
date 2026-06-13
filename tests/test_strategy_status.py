import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.reporting.strategy_status import (
    read_strategy_status_registry,
    set_strategy_status,
)


def test_strategy_status_creates_new_registry(tmp_path):
    registry_path = tmp_path / "notes" / "strategy_status.md"

    result = set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Failed cross-fixture robustness sweep",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc),
    )

    assert result.saved
    assert result.registry_path == registry_path
    assert registry_path.exists()
    markdown = registry_path.read_text(encoding="utf-8")
    assert "# Strategy Status Registry" in markdown
    assert "Status timestamp: 2026-06-13T03:00:00+00:00" in markdown
    assert "Strategy ID: `momentum_v1`" in markdown
    assert "Status: `retest`" in markdown
    assert "Reason: Failed cross-fixture robustness sweep" in markdown


@pytest.mark.parametrize("status", ["active", "promoted", "retest", "modified", "retired"])
def test_strategy_status_accepts_valid_statuses(tmp_path, status):
    result = set_strategy_status(
        strategy_id="momentum_v1",
        status=status,
        reason="Human review complete",
        registry_path=tmp_path / f"{status}.md",
    )

    assert result.saved


def test_strategy_status_rejects_invalid_status(tmp_path):
    with pytest.raises(ValueError, match="status must be one of"):
        set_strategy_status(
            strategy_id="momentum_v1",
            status="live_trade",
            reason="Not allowed",
            registry_path=tmp_path / "strategy_status.md",
        )


def test_strategy_status_updates_same_strategy_and_shows_latest_clearly(tmp_path):
    registry_path = tmp_path / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc),
    )
    set_strategy_status(
        strategy_id="momentum_v1",
        status="modified",
        reason="Momentum logic revised",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 4, 0, tzinfo=timezone.utc),
    )

    result = read_strategy_status_registry(registry_path=registry_path)

    assert "Current statuses" in result.message
    assert "momentum_v1 | modified" in result.message
    assert "Momentum logic revised" in result.message


def test_strategy_status_preserves_history(tmp_path):
    registry_path = tmp_path / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc),
    )
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retired",
        reason="Failed revised sweep",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 4, 0, tzinfo=timezone.utc),
    )

    markdown = registry_path.read_text(encoding="utf-8")
    result = read_strategy_status_registry(registry_path=registry_path)

    assert markdown.count("## Status -") == 2
    assert "History entries: 2" in result.message
    assert "2026-06-13T03:00:00+00:00 | momentum_v1 | retest" in result.message
    assert "2026-06-13T04:00:00+00:00 | momentum_v1 | retired" in result.message


def test_strategy_status_includes_optional_source_note_path(tmp_path):
    registry_path = tmp_path / "strategy_status.md"
    source_note = tmp_path / "notes" / "sweep_analysis_note.md"

    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
        source_note=source_note,
    )

    markdown = registry_path.read_text(encoding="utf-8")
    assert f"Source note path: `{source_note}`" in markdown


def test_strategy_status_includes_optional_next_action(tmp_path):
    registry_path = tmp_path / "strategy_status.md"

    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
        next_action="Modify or replace momentum logic",
    )

    markdown = registry_path.read_text(encoding="utf-8")
    assert "Next action: Modify or replace momentum logic" in markdown


def test_strategy_status_includes_safety_reminder(tmp_path):
    registry_path = tmp_path / "strategy_status.md"

    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
    )

    markdown = registry_path.read_text(encoding="utf-8")
    assert "Safety reminder:" in markdown
    assert "Research status only." in markdown
    assert "Not live trading approval." in markdown
    assert "No broker/order behavior changed." in markdown


def test_strategy_status_read_missing_registry(tmp_path):
    registry_path = tmp_path / "missing.md"

    result = read_strategy_status_registry(registry_path=registry_path)

    assert f"No strategy status registry found at {registry_path}." == result.message


def test_strategy_status_read_existing_registry(tmp_path):
    registry_path = tmp_path / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
    )

    result = read_strategy_status_registry(registry_path=registry_path)

    assert result.registry_path == registry_path
    assert "Strategy Status Registry" in result.message
    assert "Current statuses" in result.message
    assert "momentum_v1" in result.message
    assert "Safety reminder: research status only; not live trading approval" in result.message


def test_strategy_status_cli_output_includes_saved_registry_path(tmp_path):
    registry_path = tmp_path / "notes" / "strategy_status.md"
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "set-strategy-status",
            "--strategy-id",
            "momentum_v1",
            "--status",
            "retest",
            "--reason",
            "Failed cross-fixture robustness sweep",
            "--next-action",
            "Modify or replace momentum logic",
            "--registry-path",
            str(registry_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert f"Saved strategy status registry: {registry_path}" in result.stdout
    assert registry_path.exists()
    assert "Traceback" not in result.stderr


def test_strategy_status_cli_rejects_invalid_status(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "set-strategy-status",
            "--strategy-id",
            "momentum_v1",
            "--status",
            "live_trade",
            "--reason",
            "Not allowed",
            "--registry-path",
            str(tmp_path / "strategy_status.md"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_strategy_status_cli_prints_existing_registry(tmp_path):
    registry_path = tmp_path / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs more fixtures",
        registry_path=registry_path,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "strategy-status",
            "--registry-path",
            str(registry_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy Status Registry" in result.stdout
    assert "momentum_v1" in result.stdout
    assert "Traceback" not in result.stderr
