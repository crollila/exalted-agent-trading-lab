"""Bounded memory retrieval for research/review prompts (Phase 7W).

Supplies a *small, capped* context to future cycles instead of dumping raw
history into prompts. The context is exactly:

* current working memory (account, positions + active theses, session, watchlist,
  constraints, daily usage),
* the most recent N daily summaries (compact),
* the top K relevant, non-retired playbook lessons (deterministically ranked),
* the most recent scorecard snapshot,
* current risk / portfolio constraints.

It NEVER injects raw audit logs, old chats, or unbounded historical reports.
Caps come from :class:`MemoryConfig`. Deterministic only; no LLM, no trading.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.memory_config import MemoryConfig, memory_dirs
from src.competition.playbook import PlaybookLesson, TeamPlaybook

# Keys an LLM prompt must never receive in bulk (guards against accidental floods).
EXCLUDED_FROM_PROMPT = ("raw_audit", "iteration_jsonl", "chat_history", "agent_response_history")


def _recency_score(iso: str | None, now: datetime) -> float:
    if not iso:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    # 1.0 today, decaying ~half-life of 30 days, floored at 0.
    return max(0.0, 1.0 - (age_days / 60.0))


def rank_lessons(
    lessons: list[PlaybookLesson],
    *,
    symbols: list[str] | None = None,
    action_type: str | None = None,
    regime: str | None = None,
    now: datetime | None = None,
) -> list[PlaybookLesson]:
    """Deterministically rank active lessons by relevance.

    Score = symbol/sector match + action match + regime match + recency +
    confidence + evidence weight. Retired/superseded lessons are excluded.
    """

    now = now or datetime.now(timezone.utc)
    want_syms = {s.upper() for s in (symbols or [])}

    def score(l: PlaybookLesson) -> tuple:
        sym_match = 1 if (want_syms and want_syms.intersection(l.symbols)) else 0
        act_match = 1 if (action_type and l.action_type == action_type) else 0
        regime_match = 1 if (regime and l.regime == regime) else 0
        recency = _recency_score(l.last_validated, now)
        # Higher tuple sorts first (we reverse). Deterministic tie-breaks last.
        return (
            sym_match + act_match + regime_match,
            round(l.confidence, 4),
            l.evidence_count,
            round(recency, 4),
            l.lesson_id,  # stable final tie-break
        )

    active = [l for l in lessons if l.active]
    return sorted(active, key=score, reverse=True)


def load_recent_daily_summaries(
    team_id: str,
    *,
    max_n: int,
    eod_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Load the newest ``max_n`` compact daily summaries for a team (no raw logs)."""

    directory = Path(eod_dir) if eod_dir is not None else memory_dirs()["daily_summary"]
    if not directory.exists() or max_n <= 0:
        return []
    files = sorted(
        directory.glob(f"{team_id}_*.json"),
        key=lambda p: p.name, reverse=True,
    )[:max_n]
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip a corrupt summary
            continue
        out.append({
            "trading_date": data.get("trading_date"),
            "daily_pl": data.get("daily_pl"),
            "daily_return_pct": data.get("daily_return_pct"),
            "excess_vs_spy_pct": data.get("excess_vs_spy_pct"),
            "learnings": (data.get("learnings") or [])[:3],
            "next_day_plan": (data.get("next_day_plan") or [])[:3],
        })
    return out


def build_bounded_context(
    team_id: str,
    *,
    working_memory: dict[str, Any],
    playbook: TeamPlaybook,
    recent_daily: list[dict[str, Any]],
    scorecard_snapshot: dict[str, Any] | None,
    constraints: dict[str, Any],
    config: MemoryConfig,
    relevance_symbols: list[str] | None = None,
    relevance_action: str | None = None,
    relevance_regime: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the capped retrieval context. Pure; excludes raw history."""

    ranked = rank_lessons(
        playbook.lessons, symbols=relevance_symbols,
        action_type=relevance_action, regime=relevance_regime, now=now,
    )[: config.max_lessons_in_prompt]
    daily = list(recent_daily)[: config.max_daily_summaries_in_prompt]

    return {
        "team_id": team_id,
        "working_memory": working_memory,
        "recent_daily_summaries": daily,
        "playbook_lessons": [
            {
                "category": l.category, "text": l.text, "confidence": l.confidence,
                "evidence_count": l.evidence_count, "last_validated": l.last_validated,
                "symbols": l.symbols, "action_type": l.action_type, "regime": l.regime,
            }
            for l in ranked
        ],
        "scorecard_snapshot": scorecard_snapshot,
        "constraints": constraints,
        "_bounds": {
            "max_daily_summaries_in_prompt": config.max_daily_summaries_in_prompt,
            "max_lessons_in_prompt": config.max_lessons_in_prompt,
            "excludes": list(EXCLUDED_FROM_PROMPT),
            "note": "Bounded context: raw audit logs, chats, and unbounded reports are excluded by design.",
        },
    }


__all__ = [
    "rank_lessons",
    "load_recent_daily_summaries",
    "build_bounded_context",
    "EXCLUDED_FROM_PROMPT",
]
