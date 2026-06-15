"""Team self-improvement / learning loop (Part 8).

Self-improvement here means *runtime learning*: persisted memory, scorecards,
post-cycle evaluation, and prompt feedback. It does NOT mean the model weights
self-train. Each team keeps a JSON ledger under ``data/agent_learning/`` that
accumulates hypotheses, lessons, and per-cycle reviews so the next cycle's prompt
context reflects what worked and what failed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LEARNING_DIR = Path("data/agent_learning")


@dataclass
class CycleReview:
    cycle_id: str
    timestamp: str
    what_worked: list[str] = field(default_factory=list)
    what_failed: list[str] = field(default_factory=list)
    why_it_failed: list[str] = field(default_factory=list)
    changes_for_next_cycle: list[str] = field(default_factory=list)
    risk_events: list[str] = field(default_factory=list)
    post_trade_reviews: list[str] = field(default_factory=list)
    spy_comparison: dict[str, float] | None = None
    proposals: int = 0
    approved: int = 0
    rejected: int = 0
    simulation_only: int = 0
    orders_submitted: int = 0


@dataclass
class TeamLearningLedger:
    team_id: str
    current_hypothesis: str = ""
    active_strategy: str = ""
    watchlist: list[str] = field(default_factory=list)
    rejected_ideas: list[str] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    strategy_changes: list[str] = field(default_factory=list)
    alpha_vs_beta_comparison: str = ""
    # Phase 7M strategy-memory feedback (compact, no secrets).
    mode: str = ""  # exploration | conservation
    avoid_next_cycle: list[str] = field(default_factory=list)
    # Phase 7N: timestamp of the last FULL (non-review-only) cycle, for the cheap gate.
    last_full_cycle_at: str = ""
    reviews: list[CycleReview] = field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def path_for(cls, team_id: str, learning_dir: Path | str = DEFAULT_LEARNING_DIR) -> Path:
        return Path(learning_dir) / f"{team_id}_learning.json"

    @classmethod
    def load(cls, team_id: str, learning_dir: Path | str = DEFAULT_LEARNING_DIR) -> "TeamLearningLedger":
        path = cls.path_for(team_id, learning_dir)
        if not path.exists():
            return cls(team_id=team_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        reviews = [CycleReview(**review) for review in data.pop("reviews", [])]
        return cls(reviews=reviews, **data)

    def save(self, learning_dir: Path | str = DEFAULT_LEARNING_DIR) -> Path:
        path = self.path_for(self.team_id, learning_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        payload = asdict(self)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def record_cycle(self, review: CycleReview) -> None:
        self.reviews.append(review)
        for lesson in review.what_failed + review.why_it_failed:
            if lesson and lesson not in self.lessons_learned:
                self.lessons_learned.append(lesson)
        for change in review.changes_for_next_cycle:
            if change and change not in self.strategy_changes:
                self.strategy_changes.append(change)
        for event in review.risk_events:
            if event and event not in self.risk_notes:
                self.risk_notes.append(event)

    def latest_lessons(self, limit: int = 5) -> list[str]:
        return self.lessons_learned[-limit:]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def update_team_learning(
    team_id: str,
    review: CycleReview,
    *,
    hypothesis: str | None = None,
    active_strategy: str | None = None,
    watchlist: list[str] | None = None,
    rejected_ideas: list[str] | None = None,
    alpha_vs_beta: str | None = None,
    mode: str | None = None,
    avoid_next_cycle: list[str] | None = None,
    mark_full_cycle: bool = False,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
) -> TeamLearningLedger:
    """Load, update, and persist a team's learning ledger after a cycle."""

    ledger = TeamLearningLedger.load(team_id, learning_dir)
    if hypothesis is not None:
        ledger.current_hypothesis = hypothesis
    if active_strategy is not None:
        ledger.active_strategy = active_strategy
    if watchlist is not None:
        ledger.watchlist = watchlist
    if rejected_ideas:
        for idea in rejected_ideas:
            if idea and idea not in ledger.rejected_ideas:
                ledger.rejected_ideas.append(idea)
    if alpha_vs_beta is not None:
        ledger.alpha_vs_beta_comparison = alpha_vs_beta
    if mode is not None:
        ledger.mode = mode
    if avoid_next_cycle:
        # Keep a compact, de-duplicated tail (cost control).
        for item in avoid_next_cycle:
            if item and item not in ledger.avoid_next_cycle:
                ledger.avoid_next_cycle.append(item)
        ledger.avoid_next_cycle = ledger.avoid_next_cycle[-10:]
    if mark_full_cycle:
        ledger.last_full_cycle_at = datetime.now(timezone.utc).isoformat()
    ledger.record_cycle(review)
    ledger.save(learning_dir)
    return ledger
