"""Daily research loop and learning ledger helpers.

The ledger is runtime memory only. It records operator lessons and can be included in
future prompts as context, but it does not train a model, modify code, or change trading
permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Sequence

from src.ui.dashboard_state import TeamStatus, redact_secret_like_text


DEFAULT_LEARNING_LEDGER_PATH = Path("data/notes/learning_ledger.md")
DEFAULT_AGENT_GOALS_DIR = Path("data/notes/agent_goals")
ALLOWED_LEARNING_DECISIONS = ("promote", "modify", "retest", "retire", "no_decision")


@dataclass(frozen=True)
class LearningLedgerEntry:
    timestamp: datetime
    team: str
    agent_or_strategy: str
    what_happened: str
    result: str
    lesson: str
    next_action: str
    decision: str
    evidence_path: str = ""


@dataclass(frozen=True)
class ImprovementScore:
    proposals_generated: int
    risk_approved: int
    review_approved: int
    deterministic_risk_accepted: int
    deterministic_risk_rejected: int
    paper_order_submitted: int
    paper_order_blocked: int
    pnl_available: bool


@dataclass(frozen=True)
class AgentGoal:
    team: str
    current_team_goal: str = ""
    current_agent_focus: str = ""
    current_constraints: str = "Paper-only. No live trading, short execution, margin execution, or options execution."
    next_action: str = ""
    open_questions: str = ""
    hypothesis: str = ""


@dataclass(frozen=True)
class StrategyScorecard:
    team: str
    strategy: str
    proposals_generated: int
    execution_eligible: int
    risk_approved: bool
    review_approved: bool
    deterministic_risk_approved: bool
    deterministic_risk_rejected: bool
    paper_orders_submitted: int
    paper_orders_blocked: int
    pnl_available: bool
    rejection_notes: str


def _clean_field(value: str) -> str:
    return redact_secret_like_text(str(value or "").strip()).replace("\r\n", "\n")


def append_learning_ledger_entry(
    entry: LearningLedgerEntry,
    *,
    path: Path | str = DEFAULT_LEARNING_LEDGER_PATH,
) -> Path:
    """Append one lesson to the local ignored ledger, redacting secret-like text."""

    if entry.decision not in ALLOWED_LEARNING_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(ALLOWED_LEARNING_DECISIONS)}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("# Learning Ledger\n\n_Runtime memory, not model training._\n\n", encoding="utf-8")
    block = [
        f"## {entry.timestamp.astimezone(timezone.utc).isoformat()} - {entry.team}",
        "",
        f"- team: {_clean_field(entry.team)}",
        f"- agent/strategy: {_clean_field(entry.agent_or_strategy)}",
        f"- what happened: {_clean_field(entry.what_happened)}",
        f"- evidence path: {_clean_field(entry.evidence_path)}",
        f"- result: {_clean_field(entry.result)}",
        f"- lesson: {_clean_field(entry.lesson)}",
        f"- next action: {_clean_field(entry.next_action)}",
        f"- decision: {_clean_field(entry.decision)}",
        "",
    ]
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(block))
    return target


def read_learning_ledger(
    path: Path | str = DEFAULT_LEARNING_LEDGER_PATH,
    *,
    max_chars: int = 20000,
) -> str:
    """Read local ledger text, redacted and truncated."""

    file_path = Path(path)
    if not file_path.is_file():
        return "No learning ledger entries yet."
    text = redact_secret_like_text(file_path.read_text(encoding="utf-8"))
    if len(text) > max_chars:
        return text[:max_chars] + "\n... (truncated)"
    return text


def latest_learning_entries(
    path: Path | str = DEFAULT_LEARNING_LEDGER_PATH,
    *,
    limit: int = 5,
) -> list[str]:
    """Return latest raw Markdown entry blocks."""

    text = read_learning_ledger(path)
    entries = [("## " + part).strip() for part in text.split("## ") if part.strip() and not part.startswith("#")]
    return entries[-limit:]


def learning_memory_context(
    path: Path | str = DEFAULT_LEARNING_LEDGER_PATH,
    *,
    limit: int = 5,
) -> str:
    """Build prompt context from local ledger only."""

    entries = latest_learning_entries(path, limit=limit)
    if not entries:
        return "No local learning ledger entries yet. Runtime memory only; no model training."
    return "\n\n".join(
        ["Runtime memory from local learning_ledger.md only. This is not model training.", *entries]
    )


def _goal_path(team: str, goals_dir: Path | str = DEFAULT_AGENT_GOALS_DIR) -> Path:
    safe_team = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in team.strip()) or "team"
    return Path(goals_dir) / f"{safe_team}.json"


def default_agent_goal(team: str) -> AgentGoal:
    return AgentGoal(
        team=team,
        current_team_goal="Find one evidence-backed, paper-only stock_long hypothesis.",
        current_agent_focus="No runtime focus saved yet.",
        next_action="Run a disabled-autonomy cycle, then record a lesson.",
        open_questions="What evidence is missing before a paper test?",
        hypothesis="No active hypothesis saved yet.",
    )


def read_agent_goal(team: str, *, goals_dir: Path | str = DEFAULT_AGENT_GOALS_DIR) -> AgentGoal:
    """Read a team's runtime goal JSON, returning safe defaults when absent/bad."""

    path = _goal_path(team, goals_dir)
    if not path.is_file():
        return default_agent_goal(team)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_agent_goal(team)
    if not isinstance(payload, dict):
        return default_agent_goal(team)
    return AgentGoal(
        team=_clean_field(str(payload.get("team") or team)),
        current_team_goal=_clean_field(str(payload.get("current_team_goal") or "")),
        current_agent_focus=_clean_field(str(payload.get("current_agent_focus") or "")),
        current_constraints=_clean_field(str(payload.get("current_constraints") or "")),
        next_action=_clean_field(str(payload.get("next_action") or "")),
        open_questions=_clean_field(str(payload.get("open_questions") or "")),
        hypothesis=_clean_field(str(payload.get("hypothesis") or "")),
    )


def write_agent_goal(goal: AgentGoal, *, goals_dir: Path | str = DEFAULT_AGENT_GOALS_DIR) -> Path:
    """Persist a team's human-readable runtime goal JSON with secret redaction."""

    path = _goal_path(goal.team, goals_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "team": _clean_field(goal.team),
        "current_team_goal": _clean_field(goal.current_team_goal),
        "current_agent_focus": _clean_field(goal.current_agent_focus),
        "current_constraints": _clean_field(goal.current_constraints),
        "next_action": _clean_field(goal.next_action),
        "open_questions": _clean_field(goal.open_questions),
        "hypothesis": _clean_field(goal.hypothesis),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def goals_memory_context(
    teams: Iterable[str],
    *,
    goals_dir: Path | str = DEFAULT_AGENT_GOALS_DIR,
) -> str:
    """Build prompt-safe goal memory from local runtime goal files."""

    lines = [
        "Runtime goals from local agent_goals files only. They are operator notes, not model training."
    ]
    for team in teams:
        goal = read_agent_goal(team, goals_dir=goals_dir)
        lines.extend(
            [
                f"{goal.team}: goal={goal.current_team_goal or 'none'}",
                f"{goal.team}: focus={goal.current_agent_focus or 'none'}",
                f"{goal.team}: hypothesis={goal.hypothesis or 'none'}",
                f"{goal.team}: next_action={goal.next_action or 'none'}",
            ]
        )
    return "\n".join(lines)


def build_improvement_score(
    statuses: Sequence[TeamStatus],
    *,
    pnl_available: bool = False,
) -> ImprovementScore:
    """Derive basic improvement counters from latest runtime status."""

    proposals_generated = sum(
        status.execution_eligible_count + status.simulation_only_count + status.rejected_count
        for status in statuses
    )
    risk_approved = sum(1 for status in statuses if status.risk_approved)
    review_approved = sum(1 for status in statuses if status.review_approved)
    deterministic_accepted = sum(1 for status in statuses if status.stock_long_eligible)
    deterministic_rejected = sum(
        1
        for status in statuses
        if status.execution_eligible_count > 0 and not status.stock_long_eligible
    )
    submitted = sum(
        1
        for status in statuses
        if "submitted" in status.paper_order_status.lower()
        and "not submitted" not in status.paper_order_status.lower()
    )
    blocked = sum(
        1
        for status in statuses
        if any(marker in status.paper_order_status.lower() for marker in ("blocked", "rejected", "not submitted"))
    )
    return ImprovementScore(
        proposals_generated=proposals_generated,
        risk_approved=risk_approved,
        review_approved=review_approved,
        deterministic_risk_accepted=deterministic_accepted,
        deterministic_risk_rejected=deterministic_rejected,
        paper_order_submitted=submitted,
        paper_order_blocked=blocked,
        pnl_available=pnl_available,
    )


def build_strategy_scorecards(
    statuses: Sequence[TeamStatus],
    *,
    pnl_available: bool = False,
) -> list[StrategyScorecard]:
    """Derive latest-cycle strategy scorecards from runtime status only."""

    cards: list[StrategyScorecard] = []
    for status in statuses:
        order_text = status.paper_order_status.lower()
        submitted = 1 if "submitted" in order_text and "not submitted" not in order_text else 0
        blocked = 1 if any(marker in order_text for marker in ("blocked", "rejected", "not submitted")) else 0
        deterministic_rejected = status.execution_eligible_count > 0 and not status.stock_long_eligible
        notes: list[str] = []
        if status.execution_eligible_count == 0:
            notes.append("No execution-eligible stock_long proposal in latest evidence.")
        if not status.risk_approved:
            notes.append("Risk approval missing or false.")
        if not status.review_approved:
            notes.append("Review approval missing or false.")
        if blocked:
            notes.append(f"Paper order status: {status.paper_order_status}.")
        cards.append(
            StrategyScorecard(
                team=status.team_id,
                strategy=f"{status.team_id}_latest_runtime",
                proposals_generated=(
                    status.execution_eligible_count
                    + status.simulation_only_count
                    + status.rejected_count
                ),
                execution_eligible=status.execution_eligible_count,
                risk_approved=status.risk_approved,
                review_approved=status.review_approved,
                deterministic_risk_approved=status.stock_long_eligible,
                deterministic_risk_rejected=deterministic_rejected,
                paper_orders_submitted=submitted,
                paper_orders_blocked=blocked,
                pnl_available=pnl_available,
                rejection_notes=" ".join(notes) if notes else "No rejection in latest runtime status.",
            )
        )
    return cards


def latest_lesson_summary(
    path: Path | str = DEFAULT_LEARNING_LEDGER_PATH,
) -> str:
    entries = latest_learning_entries(path, limit=1)
    return entries[0] if entries else "No learning ledger entries yet."


def working_on_summary(
    status: TeamStatus,
    goal: AgentGoal,
    *,
    latest_lesson: str = "No learning ledger entries yet.",
) -> list[dict[str, str]]:
    """Return an evidence-grounded 'working on' summary for one team."""

    return [
        {"label": "Latest proposal", "value": str(status.latest_proposal_path or "none")},
        {"label": "Latest risk note", "value": str(status.latest_risk_note_path or "none")},
        {"label": "Latest review note", "value": str(status.latest_review_note_path or "none")},
        {"label": "Active goal", "value": goal.current_team_goal or "none"},
        {"label": "Current hypothesis", "value": goal.hypothesis or "none"},
        {"label": "Latest lesson", "value": latest_lesson},
        {"label": "Next action", "value": goal.next_action or "none"},
    ]


def morning_checklist_lines(statuses: Iterable[TeamStatus]) -> list[str]:
    """Daily Lab checklist lines derived from current status."""

    lines = [
        "Confirm market status from Portfolio Cockpit.",
        "Confirm both teams are disabled before the first disabled-autonomy run.",
        "Review account status, risk caps, and latest positions.",
        "Run Alpha disabled first; only enable Alpha for one controlled market-hours run if clean.",
        "Keep Beta disabled until Alpha is stable.",
    ]
    for status in statuses:
        autonomy = "enabled" if status.autonomy_enabled else "disabled"
        lines.append(
            f"{status.team_id}: autonomy {autonomy}, max orders/day {status.max_paper_orders_per_day}, "
            f"max notional {status.max_daily_notional:g}."
        )
    return lines


def no_automatic_changes_notice() -> str:
    """Explicit scope statement for tests and UI."""

    return (
        "Learning ledger entries are runtime memory only. They do not train the model, "
        "modify code, change prompts automatically, or change trading permissions."
    )
