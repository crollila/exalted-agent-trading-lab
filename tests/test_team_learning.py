from src.learning.team_memory import CycleReview, TeamLearningLedger, update_team_learning


def review(**o):
    values = dict(
        cycle_id="c1",
        timestamp="2026-06-15T00:00:00Z",
        what_failed=["short disabled"],
        why_it_failed=["permission off"],
        changes_for_next_cycle=["enable shorting research"],
        risk_events=["broker skipped"],
        proposals=3,
        approved=1,
        rejected=0,
        simulation_only=2,
    )
    values.update(o)
    return CycleReview(**values)


def test_update_persists_and_accumulates_lessons(tmp_path):
    ledger = update_team_learning(
        "team_alpha", review(), hypothesis="mean reversion", learning_dir=tmp_path
    )
    assert ledger.current_hypothesis == "mean reversion"
    assert "permission off" in ledger.lessons_learned
    assert "enable shorting research" in ledger.strategy_changes
    assert "broker skipped" in ledger.risk_notes
    assert len(ledger.reviews) == 1


def test_ledger_roundtrip_load(tmp_path):
    update_team_learning("team_beta", review(), learning_dir=tmp_path)
    loaded = TeamLearningLedger.load("team_beta", tmp_path)
    assert loaded.team_id == "team_beta"
    assert len(loaded.reviews) == 1
    assert loaded.reviews[0].proposals == 3


def test_latest_lessons_limit(tmp_path):
    ledger = TeamLearningLedger(team_id="t")
    ledger.lessons_learned = [f"lesson {i}" for i in range(10)]
    assert ledger.latest_lessons(3) == ["lesson 7", "lesson 8", "lesson 9"]
