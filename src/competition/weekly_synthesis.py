"""Weekly, non-trading synthesis per team (Phase 7W).

Once a week, summarize the week's daily reports + scorecard changes, identify
recurring successes/failures, and update the durable playbook **only** through the
deterministic evidence gate (promote on repeated/high-impact evidence; supersede
stale/contradicted lessons; enforce the per-team cap). Produces a saved weekly
report and an optional short Discord summary.

Never trades, never submits orders, never changes settings or source. The LLM is
not required; lessons are derived from local artifacts.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.learning_outcomes import (
    LearningCandidate,
    PromotionResult,
    generate_candidates,
    promote_candidates,
)
from src.competition.market_time import to_ny
from src.competition.memory_config import MemoryConfig
from src.competition.playbook import TeamPlaybook
from src.competition.position_review import TeamPortfolioReview

DEFAULT_WEEKLY_DIR = Path("data/runtime/weekly_reviews")


def iso_week_tag(now: datetime | None = None) -> str:
    ny = to_ny(now)
    iso = ny.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


@dataclass
class WeeklyReview:
    team_id: str
    week_tag: str
    generated_at: str
    days_covered: int
    equity_start: float | None
    equity_end: float | None
    week_pl: float | None
    recurring_successes: list[str] = field(default_factory=list)
    recurring_failures: list[str] = field(default_factory=list)
    promoted_lessons: list[str] = field(default_factory=list)
    skipped_candidates: list[dict[str, str]] = field(default_factory=list)
    superseded_lessons: list[str] = field(default_factory=list)
    retired_for_cap: list[str] = field(default_factory=list)
    playbook_active_after: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _recurring_themes(recent_learnings: list[dict[str, Any]]) -> dict[str, int]:
    """Count how many days each observation/mistake theme appeared this week."""

    counter: Counter[str] = Counter()
    for day in recent_learnings:
        seen: set[str] = set()
        for key in ("mistakes_or_missed", "strategy_risk_observations"):
            for item in (day.get(key) or []):
                theme = item.split(";")[0].strip()[:120]
                if theme and theme not in seen:
                    counter[theme] += 1
                    seen.add(theme)
    return dict(counter)


def build_weekly_review(
    team_id: str,
    *,
    review: TeamPortfolioReview,
    recent_daily: list[dict[str, Any]],
    recent_learnings: list[dict[str, Any]],
    playbook: TeamPlaybook,
    config: MemoryConfig,
    attribution_entries: list[Any] | None = None,
    regime: str | None = None,
    now: datetime | None = None,
) -> WeeklyReview:
    """Build the weekly review and update the playbook through the evidence gate."""

    now = now or datetime.now(timezone.utc)

    # Equity change across the week's daily summaries (best-effort).
    equities = [d.get("daily_pl") for d in recent_daily if d.get("daily_pl") is not None]
    week_pl = sum(equities) if equities else None
    equity_end = review.equity

    # Recurring themes -> candidates whose supporting_count is the day-count.
    themes = _recurring_themes(recent_learnings)
    candidates: list[LearningCandidate] = generate_candidates(
        review, attribution_entries=attribution_entries, regime=regime,
    )
    for theme, days in themes.items():
        candidates.append(LearningCandidate(
            category="mistake" if "mistake" in theme.lower() or "drawdown" in theme.lower() else "strategy_observation",
            text=theme,
            confidence=min(0.9, 0.4 + 0.1 * days),
            evidence_refs=[f"week:{iso_week_tag(now)}:days={days}"],
            impact="high" if days >= 3 else "normal",
            supporting_count=days,
            regime=regime,
        ))

    before_active = {l.lesson_id for l in playbook.active_lessons()}
    promotion: PromotionResult = promote_candidates(playbook, candidates, now=now)

    # Supersede lessons clearly contradicted by this week's evidence: a "mistake"
    # lesson about a symbol that this week is an intact winner has lost support.
    superseded: list[str] = []
    winners = {p.symbol for p in review.positions
               if p.side == "long" and p.thesis_status == "intact"
               and (p.unrealized_pl_pct or 0) > 0}
    for lesson in playbook.active_lessons():
        if lesson.category == "mistake" and set(lesson.symbols) & winners:
            if playbook.supersede(lesson.lesson_id, reason="symbol now an intact winner"):
                superseded.append(lesson.text)

    retired = playbook.enforce_cap(config.max_playbook_lessons_per_team)

    successes = [c.text for c in candidates if c.category in ("strength", "preferred_regime")]
    failures = [c.text for c in candidates if c.category in ("mistake", "failure_mode")]

    return WeeklyReview(
        team_id=team_id, week_tag=iso_week_tag(now), generated_at=now.isoformat(),
        days_covered=len(recent_daily),
        equity_start=(equity_end - week_pl) if (equity_end is not None and week_pl is not None) else None,
        equity_end=equity_end, week_pl=week_pl,
        recurring_successes=successes[:5], recurring_failures=failures[:5],
        promoted_lessons=promotion.promoted,
        skipped_candidates=[{"text": t, "reason": r} for t, r in promotion.skipped][:10],
        superseded_lessons=superseded,
        retired_for_cap=retired,
        playbook_active_after=len(playbook.active_lessons()),
        notes=["Weekly synthesis is research feedback only; it never trades or changes settings/limits/code."],
    )


def render_weekly_discord(review: WeeklyReview) -> str:
    lines = [
        f"**Weekly review {review.team_id}** - {review.week_tag} ({review.days_covered} day(s))",
        f"Week P&L: {('n/a' if review.week_pl is None else f'${review.week_pl:,.0f}')}",
    ]
    if review.promoted_lessons:
        lines.append("Promoted: " + "; ".join(review.promoted_lessons[:2]))
    if review.superseded_lessons:
        lines.append(f"Superseded {len(review.superseded_lessons)} stale lesson(s).")
    lines.append(f"Active playbook lessons: {review.playbook_active_after}")
    lines.append("_Paper-only research summary. No live trading._")
    return "\n".join(lines)


def render_weekly_markdown(review: WeeklyReview) -> str:
    out = [f"# Weekly review - {review.team_id} - {review.week_tag}", "",
           f"_Generated: {review.generated_at}. Research feedback only; no trading or settings changes._", "",
           f"- Days covered: {review.days_covered}",
           f"- Week P&L: {('n/a' if review.week_pl is None else f'${review.week_pl:,.0f}')}",
           f"- Active playbook lessons after synthesis: {review.playbook_active_after}", ""]
    out.append("## Recurring successes")
    out += [f"- {s}" for s in (review.recurring_successes or ["(none)"])]
    out.append("")
    out.append("## Recurring failures")
    out += [f"- {f}" for f in (review.recurring_failures or ["(none)"])]
    out.append("")
    out.append("## Playbook changes")
    out += [f"- promoted: {p}" for p in review.promoted_lessons] or ["- promoted: (none)"]
    out += [f"- superseded: {s}" for s in review.superseded_lessons]
    out += [f"- retired (cap): {r}" for r in review.retired_for_cap]
    if review.skipped_candidates:
        out.append("")
        out.append("## Candidates not promoted (insufficient evidence)")
        out += [f"- {c['text']} - {c['reason']}" for c in review.skipped_candidates]
    return "\n".join(out)


def save_weekly_review(review: WeeklyReview, *, weekly_dir: Path | str = DEFAULT_WEEKLY_DIR) -> dict[str, Path]:
    directory = Path(weekly_dir)
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f"{review.team_id}_{review.week_tag}"
    js = base.with_suffix(".json")
    md = base.with_suffix(".md")
    js.write_text(json.dumps(review.as_dict(), indent=2), encoding="utf-8")
    md.write_text(render_weekly_markdown(review), encoding="utf-8")
    return {"json": js, "markdown": md}


__all__ = [
    "DEFAULT_WEEKLY_DIR", "WeeklyReview", "iso_week_tag",
    "build_weekly_review", "render_weekly_discord", "render_weekly_markdown",
    "save_weekly_review",
]
