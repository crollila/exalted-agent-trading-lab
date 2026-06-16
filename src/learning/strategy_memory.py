"""Multi-day strategy memory hardening (Phase 7P).

Rolls the per-day ``DailyTeamReview`` artifacts (``data/reviews/``) into a compact
multi-day memory persisted under the ignored runtime path ``data/team_memory/``.
This memory feeds future LLM strategy context compactly and is, by default,
compressed with the cheap ``LLM_MODEL_SUMMARY`` model — but always degrades to a
deterministic compact summary when the summary agent is disabled or the provider
fails.

Everything here is local + deterministic by default. No broker, no network, no
secrets. Missing data degrades to empty fields rather than raising.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.competition.daily_review import (
    DEFAULT_REVIEWS_DIR,
    DailyTeamReview,
    bucket_for,
)

DEFAULT_TEAM_MEMORY_DIR = Path("data/team_memory")


@dataclass
class StrategyMemory:
    team_id: str
    date: str = ""
    current_day_lessons: list[str] = field(default_factory=list)
    trailing_3_day_lessons: list[str] = field(default_factory=list)
    trailing_5_day_lessons: list[str] = field(default_factory=list)
    week_to_date_lessons: list[str] = field(default_factory=list)
    recurring_winning_patterns: list[str] = field(default_factory=list)
    recurring_losing_patterns: list[str] = field(default_factory=list)
    symbols_to_favor: list[str] = field(default_factory=list)
    symbols_to_avoid: list[str] = field(default_factory=list)
    sectors_to_favor: list[str] = field(default_factory=list)
    sectors_to_avoid: list[str] = field(default_factory=list)
    strategy_adjustments_for_next_cycle: list[str] = field(default_factory=list)
    strategy_adjustments_for_tomorrow: list[str] = field(default_factory=list)
    confidence_in_current_strategy: float = 0.5
    recommended_mode: str = "conservation"  # exploration | conservation | reset
    last_summary_model_used: str = ""
    compact_summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def path_for(cls, team_id: str, team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR) -> Path:
        return Path(team_memory_dir) / f"{team_id}_strategy_memory.json"

    @classmethod
    def load(
        cls, team_id: str, team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR
    ) -> "StrategyMemory | None":
        path = cls.path_for(team_id, team_memory_dir)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {k: v for k, v in data.items() if k in {f.name for f in fields(cls)}}
            return cls(**known)
        except (ValueError, TypeError, OSError):
            return None

    def save(self, team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR) -> Path:
        path = self.path_for(self.team_id, team_memory_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, json.dumps(self.as_dict(), indent=2, default=str))
        return path


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_recent_daily_reviews(
    team_id: str,
    *,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
    limit: int = 5,
) -> list[DailyTeamReview]:
    """Load the most recent dated ``DailyTeamReview`` artifacts (newest last)."""

    directory = Path(reviews_dir)
    if not directory.exists():
        return []
    known = {f.name for f in fields(DailyTeamReview)}
    reviews: list[DailyTeamReview] = []
    # Dated artifacts are ``{team}_{YYYY-MM-DD}.json``; skip the ``{team}_latest.json`` alias.
    for path in sorted(directory.glob(f"{team_id}_*.json")):
        if path.name == f"{team_id}_latest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            reviews.append(DailyTeamReview(**{k: v for k, v in data.items() if k in known}))
        except (ValueError, TypeError, OSError):
            continue
    reviews.sort(key=lambda r: r.date)
    return reviews[-limit:] if limit else reviews


def _lessons_from(reviews: list[DailyTeamReview]) -> list[str]:
    lessons: list[str] = []
    for review in reviews:
        lessons.extend(review.stop_doing)
        lessons.extend(review.test_next)
    return list(dict.fromkeys(l for l in lessons if l))[:8]


def _recurring(counter: Counter, minimum: int = 2, limit: int = 5) -> list[str]:
    return [item for item, count in counter.most_common(limit) if count >= minimum]


def build_strategy_memory(
    team_id: str,
    *,
    today_review: DailyTeamReview | None = None,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
) -> StrategyMemory:
    """Aggregate recent daily reviews into a deterministic multi-day memory."""

    history = load_recent_daily_reviews(team_id, reviews_dir=reviews_dir, limit=5)
    if today_review is not None:
        # Ensure today's review is present + last even if not yet on disk.
        history = [r for r in history if r.date != today_review.date] + [today_review]

    today = history[-1] if history else None
    last3 = history[-3:]
    last5 = history[-5:]

    keep_counter: Counter = Counter()
    stop_counter: Counter = Counter()
    favor_counter: Counter = Counter()
    avoid_counter: Counter = Counter()
    sector_favor: Counter = Counter()
    sector_avoid: Counter = Counter()
    beats = 0
    trails = 0
    for review in last5:
        keep_counter.update(review.keep_doing)
        stop_counter.update(review.stop_doing)
        for entry in review.helped:
            sym = entry.split(" ")[0].strip().upper()
            if sym:
                favor_counter.update([sym])
                sector_favor.update([bucket_for(sym)])
        for entry in review.hurt:
            sym = entry.split(" ")[0].strip().upper()
            if sym:
                avoid_counter.update([sym])
                sector_avoid.update([bucket_for(sym)])
        result = (review.spy_relative_result or "").lower()
        if result.startswith("beat"):
            beats += 1
        elif result.startswith("trailed"):
            trails += 1

    scored = beats + trails
    confidence = round((beats / scored), 3) if scored else 0.5

    # Mode: reset on a sustained losing streak with churn, else most-recent recommendation.
    if scored >= 3 and trails >= beats and trails >= 3:
        recommended_mode = "reset"
    elif today is not None and today.recommended_mode:
        recommended_mode = today.recommended_mode
    else:
        recommended_mode = "exploration" if team_id == "team_alpha" else "conservation"

    favor = [s for s, _ in favor_counter.most_common(6)]
    avoid = [s for s, _ in avoid_counter.most_common(6)]
    sectors_favor = [s for s, _ in sector_favor.most_common(4) if s != "unknown"]
    sectors_avoid = [s for s, _ in sector_avoid.most_common(4) if s != "unknown"]

    adjustments_tomorrow = list(dict.fromkeys(today.test_next)) if today else []
    adjustments_next = list(
        dict.fromkeys([c for r in last3 for c in (r.stop_doing + r.test_next)])
    )[:6]

    return StrategyMemory(
        team_id=team_id,
        date=(today.date if today else datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        current_day_lessons=_lessons_from([today]) if today else [],
        trailing_3_day_lessons=_lessons_from(last3),
        trailing_5_day_lessons=_lessons_from(last5),
        week_to_date_lessons=_lessons_from(history),
        recurring_winning_patterns=_recurring(keep_counter),
        recurring_losing_patterns=_recurring(stop_counter),
        symbols_to_favor=favor,
        symbols_to_avoid=avoid,
        sectors_to_favor=sectors_favor,
        sectors_to_avoid=sectors_avoid,
        strategy_adjustments_for_next_cycle=adjustments_next,
        strategy_adjustments_for_tomorrow=adjustments_tomorrow,
        confidence_in_current_strategy=confidence,
        recommended_mode=recommended_mode,
    )


def update_strategy_memory(
    team_id: str,
    *,
    today_review: DailyTeamReview | None = None,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
    team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR,
    summary_enabled: bool | None = None,
    provider: Any | None = None,
    env: Mapping[str, str] | None = None,
) -> StrategyMemory:
    """Build, optionally LLM-compress, persist, and return the multi-day memory.

    When the summary agent is enabled (and a provider is available) the cheap
    summary model compresses the memory and records ``last_summary_model_used``;
    otherwise a deterministic compact summary is used.
    """

    memory = build_strategy_memory(team_id, today_review=today_review, reviews_dir=reviews_dir)

    # Optional LLM compression (cheap summary model). Import locally to avoid a
    # circular import at module load.
    from src.agents.llm_review_agents import LLMReviewFlags, summarize_strategy_memory

    if summary_enabled is None:
        summary_enabled = LLMReviewFlags.from_env(env).summary_agent

    summary = summarize_strategy_memory(
        team_id=team_id,
        memory=memory.as_dict(),
        enabled=summary_enabled,
        provider=provider,
        env=env,
    )
    memory.compact_summary = str(summary.get("compact_summary", ""))
    memory.last_summary_model_used = str(summary.get("model_used", ""))
    memory.save(team_memory_dir)
    return memory


def strategy_memory_context(
    team_id: str,
    *,
    team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR,
) -> dict[str, Any]:
    """Compact multi-day memory for the next LLM strategy cycle (cost-controlled).

    Research feedback only — never authorizes bypassing risk/credentials/kill
    switch. Returns ``{"available": False}`` when no memory exists yet.
    """

    memory = StrategyMemory.load(team_id, team_memory_dir)
    if memory is None:
        return {
            "available": False,
            "note": "No multi-day strategy memory yet. Research feedback only; never bypass risk.",
        }
    return {
        "available": True,
        "note": "Multi-day strategy memory (research feedback only; never bypass risk/credentials/kill switch).",
        "current_day_lessons": memory.current_day_lessons[:3],
        "trailing_3_day_lessons": memory.trailing_3_day_lessons[:3],
        "trailing_5_day_lessons": memory.trailing_5_day_lessons[:3],
        "recurring_winning_patterns": memory.recurring_winning_patterns[:3],
        "recurring_losing_patterns": memory.recurring_losing_patterns[:3],
        "symbols_to_favor": memory.symbols_to_favor[:6],
        "symbols_to_avoid": memory.symbols_to_avoid[:6],
        "sectors_to_favor": memory.sectors_to_favor[:4],
        "sectors_to_avoid": memory.sectors_to_avoid[:4],
        "strategy_adjustments_for_tomorrow": memory.strategy_adjustments_for_tomorrow[:4],
        "confidence_in_current_strategy": memory.confidence_in_current_strategy,
        "recommended_mode": memory.recommended_mode,
        "compact_summary": memory.compact_summary,
    }


def format_strategy_memory(memory: StrategyMemory) -> str:
    lines = [f"=== Multi-day strategy memory: {memory.team_id} ({memory.date}) ==="]
    lines.append(f"Recommended mode: {memory.recommended_mode} | confidence: {memory.confidence_in_current_strategy}")
    lines.append(f"Current-day lessons: {', '.join(memory.current_day_lessons) or '(none)'}")
    lines.append(f"Trailing-3-day lessons: {', '.join(memory.trailing_3_day_lessons) or '(none)'}")
    lines.append(f"Trailing-5-day lessons: {', '.join(memory.trailing_5_day_lessons) or '(none)'}")
    lines.append(f"Recurring winners: {', '.join(memory.recurring_winning_patterns) or '(none)'}")
    lines.append(f"Recurring losers: {', '.join(memory.recurring_losing_patterns) or '(none)'}")
    lines.append(f"Favor symbols: {', '.join(memory.symbols_to_favor) or '(none)'} | "
                 f"sectors: {', '.join(memory.sectors_to_favor) or '(none)'}")
    lines.append(f"Avoid symbols: {', '.join(memory.symbols_to_avoid) or '(none)'} | "
                 f"sectors: {', '.join(memory.sectors_to_avoid) or '(none)'}")
    lines.append(f"Adjust tomorrow: {', '.join(memory.strategy_adjustments_for_tomorrow) or '(none)'}")
    lines.append(f"Adjust next cycle: {', '.join(memory.strategy_adjustments_for_next_cycle) or '(none)'}")
    lines.append(f"Summary model used: {memory.last_summary_model_used or '(deterministic)'}")
    lines.append(f"Compact summary: {memory.compact_summary or '(none)'}")
    return "\n".join(lines)
