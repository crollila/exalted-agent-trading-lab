"""Tests for the durable playbook + deterministic learning promotion (Phase 7W)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.competition.learning_outcomes import (
    LearningCandidate,
    generate_candidates,
    promote_candidates,
)
from src.competition.playbook import TeamPlaybook, lesson_id_for
from src.competition.position_review import build_team_portfolio_review
from src.config.portfolio_limits import PortfolioLimits

NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


def _cand(**over):
    base = dict(category="risk_lesson", text="size down on concentration",
               confidence=0.6, evidence_refs=["pos:NVDA"], supporting_count=1, impact="normal")
    base.update(over)
    return LearningCandidate(**base)


def test_evidence_free_lesson_cannot_be_promoted():
    pb = TeamPlaybook(team_id="t")
    res = promote_candidates(pb, [_cand(evidence_refs=[])], now=NOW)
    assert res.promoted == []
    assert pb.lessons == []
    assert any("no supporting evidence" in r for _t, r in res.skipped)


def test_zero_confidence_cannot_be_promoted():
    pb = TeamPlaybook(team_id="t")
    res = promote_candidates(pb, [_cand(confidence=0.0)], now=NOW)
    assert res.promoted == []
    assert pb.lessons == []


def test_low_evidence_normal_impact_not_promoted():
    pb = TeamPlaybook(team_id="t")
    res = promote_candidates(pb, [_cand(supporting_count=1, impact="normal")], now=NOW)
    assert res.promoted == []  # needs >=2 supporting or high impact


def test_repeated_evidence_can_be_promoted():
    pb = TeamPlaybook(team_id="t")
    res = promote_candidates(pb, [_cand(supporting_count=2)], now=NOW)
    assert len(res.promoted) == 1
    assert len(pb.active_lessons()) == 1
    assert pb.active_lessons()[0].evidence_count == 1


def test_high_impact_single_evidence_can_be_promoted():
    pb = TeamPlaybook(team_id="t")
    res = promote_candidates(pb, [_cand(supporting_count=1, impact="high")], now=NOW)
    assert len(res.promoted) == 1


def test_repromotion_strengthens_same_lesson():
    pb = TeamPlaybook(team_id="t")
    promote_candidates(pb, [_cand(supporting_count=2)], now=NOW)
    promote_candidates(pb, [_cand(supporting_count=2, evidence_refs=["pos:AMD"])], now=NOW)
    lessons = pb.active_lessons()
    assert len(lessons) == 1  # same lesson id, strengthened not duplicated
    assert lessons[0].evidence_count == 2
    assert set(lessons[0].evidence_refs) >= {"pos:NVDA", "pos:AMD"}


def test_stale_lesson_superseded_not_deleted():
    pb = TeamPlaybook(team_id="t")
    promote_candidates(pb, [_cand(supporting_count=2)], now=NOW)
    lid = pb.lessons[0].lesson_id
    assert pb.supersede(lid, reason="contradicted") is True
    # Still on disk/object (audit), just not active.
    assert len(pb.lessons) == 1
    assert pb.active_lessons() == []
    assert pb.get(lid).superseded_by is not None


def test_cap_retires_weakest_without_deletion():
    pb = TeamPlaybook(team_id="t")
    for i in range(5):
        pb.upsert(category="strategy_observation", text=f"lesson {i}",
                 confidence=0.5 + i * 0.05, evidence_refs=[f"e{i}"], now=NOW)
    retired = pb.enforce_cap(3)
    assert len(retired) == 2
    assert len(pb.active_lessons()) == 3
    assert len(pb.lessons) == 5  # retired kept on disk, not deleted


def test_persistence_round_trip(tmp_path):
    pb = TeamPlaybook(team_id="team_alpha")
    pb.upsert(category="mistake", text="held loser too long", confidence=0.7, evidence_refs=["x"], now=NOW)
    pb.save(playbook_dir=tmp_path)
    loaded = TeamPlaybook.load("team_alpha", playbook_dir=tmp_path)
    assert len(loaded.lessons) == 1
    assert loaded.lessons[0].text == "held loser too long"


def test_generate_candidates_are_evidence_grounded():
    review = build_team_portfolio_review(
        "team_alpha", equity=80_000, cash=-150_000, buying_power=0.0,
        raw_positions=[
            {"symbol": "NVDA", "qty": 1000, "side": "long", "avg_entry_price": 200.0,
             "current_price": 160.0, "market_value": 160000.0, "cost_basis": 200000.0,
             "unrealized_pl": -40000.0, "unrealized_plpc": -0.20},
        ],
        attribution_entries=[],
        limits=PortfolioLimits(),
    )
    cands = generate_candidates(review, regime="risk_off")
    assert cands  # produced from real positions
    for c in cands:
        assert c.evidence_refs  # every candidate cites concrete evidence
