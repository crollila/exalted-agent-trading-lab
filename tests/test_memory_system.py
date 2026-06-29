"""Tests for bounded retrieval + retention maintenance (Phase 7W)."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

from src.competition.memory_config import MemoryConfig, memory_dirs
from src.competition.memory_maintenance import inventory, plan_maintenance, run_maintenance
from src.competition.memory_retrieval import (
    EXCLUDED_FROM_PROMPT,
    build_bounded_context,
    rank_lessons,
)
from src.competition.playbook import TeamPlaybook

NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


# --- bounded retrieval --------------------------------------------------------


def _playbook_with(n: int) -> TeamPlaybook:
    pb = TeamPlaybook(team_id="t")
    for i in range(n):
        pb.upsert(category="strategy_observation", text=f"lesson {i}", confidence=0.5,
                  evidence_refs=[f"e{i}"], symbols=["NVDA"] if i == 0 else [], now=NOW)
    return pb


def test_bounded_context_caps_lessons_and_dailies():
    cfg = MemoryConfig(max_lessons_in_prompt=3, max_daily_summaries_in_prompt=2)
    pb = _playbook_with(10)
    daily = [{"trading_date": f"2026-06-2{i}"} for i in range(9)]
    ctx = build_bounded_context(
        "t", working_memory={"positions": []}, playbook=pb,
        recent_daily=daily, scorecard_snapshot=None, constraints={}, config=cfg, now=NOW,
    )
    assert len(ctx["playbook_lessons"]) == 3
    assert len(ctx["recent_daily_summaries"]) == 2


def test_bounded_context_excludes_raw_history():
    cfg = MemoryConfig()
    ctx = build_bounded_context(
        "t", working_memory={}, playbook=TeamPlaybook(team_id="t"),
        recent_daily=[], scorecard_snapshot=None, constraints={}, config=cfg, now=NOW,
    )
    # No raw audit / chat keys may appear anywhere in the context payload.
    blob = json.dumps(ctx).lower()
    for excluded in EXCLUDED_FROM_PROMPT:
        # the only place these strings may appear is the explicit "excludes" list
        assert blob.count(excluded) <= 1
    assert "iterations.jsonl" not in blob


def test_relevance_ranking_prefers_symbol_match():
    pb = _playbook_with(5)  # lesson 0 has symbol NVDA
    ranked = rank_lessons(pb.lessons, symbols=["NVDA"], now=NOW)
    assert ranked[0].symbols == ["NVDA"]


def test_retired_lessons_excluded_from_ranking():
    pb = _playbook_with(3)
    pb.retire(pb.lessons[0].lesson_id)
    ranked = rank_lessons(pb.lessons, now=NOW)
    assert all(l.active for l in ranked)
    assert len(ranked) == 2


# --- maintenance: dry-run, apply, retention, idempotency ----------------------


def _seed(root, *, category_dir, name, days_old, content="{}"):
    d = root / category_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


def _cfg():
    return MemoryConfig(daily_summary_retention_days=90, raw_audit_retention_days=30,
                        agent_response_retention_days=14, proposal_retention_days=30,
                        keep_weekly_archives=True)


def test_dry_run_does_not_delete_or_archive(tmp_path):
    prop = memory_dirs(tmp_path)["proposal"]
    prop.mkdir(parents=True, exist_ok=True)
    old = prop / "team_alpha_cycle_2026-01-01.json"
    old.write_text("{}", encoding="utf-8")
    report = run_maintenance("team_alpha", _cfg(), apply=False, root=tmp_path, now=NOW)
    assert report.archived == 0 and report.deleted == 0  # dry-run never acts
    assert report.skipped >= 1  # but it DID plan a would_archive action
    assert old.exists()


def test_apply_preserves_today_and_current(tmp_path):
    dirs = memory_dirs(tmp_path)
    eod = dirs["daily_summary"]
    eod.mkdir(parents=True, exist_ok=True)
    today = NOW.date().isoformat()
    (eod / f"team_alpha_{today}.json").write_text("{}", encoding="utf-8")  # today -> keep
    (eod / "team_alpha_2026-01-01.json").write_text("{}", encoding="utf-8")  # old, but...
    (eod / "team_alpha_latest.json").write_text("{}", encoding="utf-8")  # current -> keep
    report = run_maintenance("team_alpha", _cfg(), apply=True, root=tmp_path, now=NOW)
    assert (eod / f"team_alpha_{today}.json").exists()  # today never deleted
    assert (eod / "team_alpha_latest.json").exists()    # current never deleted


def test_apply_archives_then_deletes_old_proposal(tmp_path):
    dirs = memory_dirs(tmp_path)
    prop = dirs["proposal"]
    prop.mkdir(parents=True, exist_ok=True)
    old_name = "team_alpha_cycle_2026-01-01.json"
    (prop / old_name).write_text('{"x":1}', encoding="utf-8")
    report = run_maintenance("team_alpha", _cfg(), apply=True, root=tmp_path, now=NOW)
    assert report.archived >= 1 and report.deleted >= 1
    assert not (prop / old_name).exists()  # deleted after archive
    # archive exists and is gzip-readable
    archives = list((dirs["archive"]).rglob("*.gz"))
    assert archives, "expected a gzip archive"
    gzip.open(archives[0], "rb").read()  # not corrupt


def test_apply_is_idempotent(tmp_path):
    dirs = memory_dirs(tmp_path)
    prop = dirs["proposal"]
    prop.mkdir(parents=True, exist_ok=True)
    (prop / "team_alpha_cycle_2026-01-01.json").write_text("{}", encoding="utf-8")
    r1 = run_maintenance("team_alpha", _cfg(), apply=True, root=tmp_path, now=NOW)
    r2 = run_maintenance("team_alpha", _cfg(), apply=True, root=tmp_path, now=NOW)
    assert r1.deleted >= 1
    assert r2.deleted == 0  # nothing left to remove; safe to re-run


def test_raw_audit_jsonl_record_level_rotation(tmp_path):
    dirs = memory_dirs(tmp_path)
    audit = dirs["raw_audit"]
    audit.mkdir(parents=True, exist_ok=True)
    old_ts = (NOW - timedelta(days=60)).isoformat()
    new_ts = (NOW - timedelta(days=1)).isoformat()
    lines = [
        json.dumps({"team_id": "team_alpha", "finished_at": old_ts, "iteration": 1}),
        json.dumps({"team_id": "team_alpha", "finished_at": new_ts, "iteration": 2}),
    ]
    (audit / "iterations.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_maintenance("team_alpha", _cfg(), apply=True, root=tmp_path, now=NOW)
    kept = (audit / "iterations.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(kept) == 1  # only the recent record remains
    assert "iteration\": 2" in kept[0] or '"iteration": 2' in kept[0]


def test_report_has_no_secrets(tmp_path):
    report = run_maintenance("team_alpha", _cfg(), apply=False, root=tmp_path, now=NOW)
    blob = json.dumps(report.as_dict()).lower()
    for needle in ("secret", "api_key", "token", "password", "bearer"):
        assert needle not in blob


def test_inventory_reports_counts_without_secrets(tmp_path):
    dirs = memory_dirs(tmp_path)
    (dirs["daily_summary"]).mkdir(parents=True, exist_ok=True)
    (dirs["daily_summary"] / "team_alpha_2026-06-29.json").write_text("{}", encoding="utf-8")
    inv = inventory("team_alpha", _cfg(), root=tmp_path, now=NOW)
    blob = json.dumps(inv.as_dict()).lower()
    for needle in ("secret", "api_key", "token", "password"):
        assert needle not in blob
