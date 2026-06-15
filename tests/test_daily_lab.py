from datetime import datetime, timezone

from src.ui.daily_lab import (
    ALLOWED_LEARNING_DECISIONS,
    AgentGoal,
    LearningLedgerEntry,
    append_learning_ledger_entry,
    build_improvement_score,
    build_strategy_scorecards,
    goals_memory_context,
    learning_memory_context,
    morning_checklist_lines,
    no_automatic_changes_notice,
    read_agent_goal,
    read_learning_ledger,
    working_on_summary,
    write_agent_goal,
)
from src.ui.dashboard_state import TeamStatus


def _status(**overrides):
    base = dict(
        team_id="team_alpha",
        autonomy_enabled=False,
        mode="paper",
        max_paper_orders_per_day=1,
        max_daily_notional=250000.0,
        natural_chat_channel_id=None,
        latest_proposal_path=None,
        latest_risk_note_path=None,
        latest_review_note_path=None,
        execution_eligible_count=1,
        simulation_only_count=1,
        rejected_count=1,
        risk_approved=True,
        review_approved=False,
        stock_long_eligible=False,
        paper_order_status="not submitted",
    )
    base.update(overrides)
    return TeamStatus(**base)


def test_learning_ledger_append_read_redacts_secrets(tmp_path):
    path = tmp_path / "learning_ledger.md"
    entry = LearningLedgerEntry(
        timestamp=datetime(2026, 6, 15, tzinfo=timezone.utc),
        team="team_alpha",
        agent_or_strategy="alpha_research_01",
        what_happened="Generated one proposal. DISCORD_BOT_TOKEN=do-not-show",
        evidence_path="data/agent_runs/example.json",
        result="blocked by review",
        lesson="Need clearer risk rationale",
        next_action="Retest disabled",
        decision="retest",
    )

    append_learning_ledger_entry(entry, path=path)
    text = read_learning_ledger(path)

    assert "Learning Ledger" in text
    assert "do-not-show" not in text
    assert "********" in text
    assert "Runtime memory, not model training" in text
    assert "data/agent_runs/example.json" in text


def test_learning_memory_context_uses_local_ledger_only(tmp_path):
    path = tmp_path / "learning_ledger.md"
    append_learning_ledger_entry(
        LearningLedgerEntry(
            timestamp=datetime(2026, 6, 15, tzinfo=timezone.utc),
            team="team_alpha",
            agent_or_strategy="momentum_v1",
            what_happened="Alpha cycle completed",
            result="No order submitted",
            lesson="Keep Beta disabled",
            next_action="Retest Alpha only",
            decision="no_decision",
        ),
        path=path,
    )

    context = learning_memory_context(path)
    assert "local learning_ledger.md only" in context
    assert "not model training" in context
    assert "Keep Beta disabled" in context


def test_improvement_score_from_fake_statuses():
    score = build_improvement_score(
        [
            _status(),
            _status(
                team_id="team_beta",
                review_approved=True,
                stock_long_eligible=True,
                paper_order_status="submitted paper order",
            ),
        ]
    )

    assert score.proposals_generated == 6
    assert score.risk_approved == 2
    assert score.review_approved == 1
    assert score.deterministic_risk_accepted == 1
    assert score.deterministic_risk_rejected == 1
    assert score.paper_order_submitted == 1
    assert score.paper_order_blocked == 1


def test_no_automatic_code_or_trading_changes_notice():
    notice = no_automatic_changes_notice()
    assert "do not train the model" in notice
    assert "modify code" in notice
    assert "change trading permissions" in notice
    assert set(ALLOWED_LEARNING_DECISIONS) == {"promote", "modify", "retest", "retire", "no_decision"}


def test_morning_checklist_includes_team_safety_state():
    lines = morning_checklist_lines([_status()])
    assert any("disabled-autonomy" in line for line in lines)
    assert any("team_alpha: autonomy disabled" in line for line in lines)


def test_agent_goal_read_write_redacts_and_builds_memory_context(tmp_path):
    goals_dir = tmp_path / "goals"
    write_agent_goal(
        AgentGoal(
            team="team_alpha",
            current_team_goal="Beat SPY safely",
            current_agent_focus="Research MSFT. DISCORD_BOT_TOKEN=hide-me",
            current_constraints="Paper-only",
            next_action="Run disabled test",
            open_questions="Need market status?",
            hypothesis="Quality names hold up",
        ),
        goals_dir=goals_dir,
    )

    goal = read_agent_goal("team_alpha", goals_dir=goals_dir)
    assert goal.current_team_goal == "Beat SPY safely"
    assert "hide-me" not in goal.current_agent_focus
    assert "********" in goal.current_agent_focus

    context = goals_memory_context(["team_alpha"], goals_dir=goals_dir)
    assert "operator notes, not model training" in context
    assert "Quality names hold up" in context


def test_strategy_scorecards_are_runtime_derived():
    cards = build_strategy_scorecards(
        [
            _status(
                risk_approved=False,
                review_approved=False,
                stock_long_eligible=False,
                paper_order_status="blocked by deterministic risk",
            )
        ]
    )

    card = cards[0]
    assert card.team == "team_alpha"
    assert card.proposals_generated == 3
    assert card.execution_eligible == 1
    assert card.deterministic_risk_rejected is True
    assert card.paper_orders_blocked == 1
    assert "Risk approval missing" in card.rejection_notes


def test_working_on_summary_uses_goal_and_runtime_evidence_only():
    status = _status()
    goal = AgentGoal(team="team_alpha", current_team_goal="Test one idea", hypothesis="Momentum persists", next_action="Retest")
    rows = working_on_summary(status, goal, latest_lesson="Latest lesson text")
    values = {row["label"]: row["value"] for row in rows}

    assert values["Latest proposal"] == "none"
    assert values["Active goal"] == "Test one idea"
    assert values["Current hypothesis"] == "Momentum persists"
    assert values["Latest lesson"] == "Latest lesson text"
