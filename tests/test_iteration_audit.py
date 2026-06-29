"""Tests for the durable per-iteration audit log (Phase 7U)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.competition.iteration_audit import (
    IterationAuditRecord,
    append_iteration_record,
    latest_status_age_seconds,
    load_latest_status,
    new_iteration_id,
)


def _record(**overrides) -> IterationAuditRecord:
    base = dict(
        iteration_id="20260629T093000-0001",
        iteration=1,
        team_id="team_alpha",
        started_at="2026-06-29T13:30:00+00:00",
        finished_at="2026-06-29T13:30:05+00:00",
        market_state="open",
        cycle_action="full_cycle",
        proposals_count=3,
        approved_count=2,
        orders_submitted=2,
    )
    base.update(overrides)
    return IterationAuditRecord(**base)


def test_append_writes_jsonl_and_latest(tmp_path):
    audit_dir = tmp_path / "loop_audit"
    path = append_iteration_record(_record(), audit_dir=audit_dir)
    assert path is not None and path.exists()

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["team_id"] == "team_alpha"
    assert parsed["orders_submitted"] == 2

    latest = load_latest_status("team_alpha", audit_dir=audit_dir)
    assert latest is not None
    assert latest["cycle_action"] == "full_cycle"


def test_append_is_appendonly_across_iterations(tmp_path):
    audit_dir = tmp_path / "loop_audit"
    append_iteration_record(_record(iteration=1), audit_dir=audit_dir)
    append_iteration_record(_record(iteration=2, cycle_action="cheap_skip"), audit_dir=audit_dir)
    path = audit_dir / "iterations.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_exception_text_is_recorded(tmp_path):
    audit_dir = tmp_path / "loop_audit"
    append_iteration_record(
        _record(cycle_action="error", exception_text="RuntimeError: provider down"),
        audit_dir=audit_dir,
    )
    latest = load_latest_status("team_alpha", audit_dir=audit_dir)
    assert latest["cycle_action"] == "error"
    assert "provider down" in latest["exception_text"]


def test_secrets_are_redacted_in_audit(tmp_path):
    audit_dir = tmp_path / "loop_audit"
    # A stray secret leaking into a note / exception text must be masked.
    leaked = "ALPACA_SECRET_KEY=supersecretvalue123 boom"
    append_iteration_record(
        _record(cycle_action="error", exception_text=leaked, notes=[leaked]),
        audit_dir=audit_dir,
    )
    raw = (audit_dir / "iterations.jsonl").read_text(encoding="utf-8")
    assert "supersecretvalue123" not in raw
    latest = load_latest_status("team_alpha", audit_dir=audit_dir)
    assert "supersecretvalue123" not in json.dumps(latest)


def test_latest_status_age_seconds(tmp_path):
    audit_dir = tmp_path / "loop_audit"
    finished = datetime(2026, 6, 29, 13, 30, 0, tzinfo=timezone.utc)
    append_iteration_record(
        _record(finished_at=finished.isoformat()), audit_dir=audit_dir
    )
    now = finished + timedelta(seconds=120)
    stamp, age = latest_status_age_seconds("team_alpha", audit_dir=audit_dir, now=now)
    assert stamp == finished.isoformat()
    assert abs(age - 120.0) < 1.0


def test_missing_status_is_none(tmp_path):
    audit_dir = tmp_path / "loop_audit"
    assert load_latest_status("team_beta", audit_dir=audit_dir) is None
    stamp, age = latest_status_age_seconds("team_beta", audit_dir=audit_dir)
    assert stamp is None and age is None


def test_new_iteration_id_is_deterministic_for_fixed_now():
    now = datetime(2026, 6, 29, 13, 30, 0, tzinfo=timezone.utc)
    assert new_iteration_id(7, now=now) == "20260629T133000-0007"
