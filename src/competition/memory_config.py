"""Bounded-memory configuration (Phase 7W).

Centralizes retention windows and prompt-context caps so the long-running
competition loop never accumulates uncontrolled runtime bloat and never floods an
LLM prompt with raw history. Every value has a safe default and is overridable via
``.env``.

Deterministic Python owns retention and prompt bounds; nothing here can change
risk limits, execution permissions, or trade. No secrets are read.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from src.config.permissions import _read_bool, _read_int

# Root for all runtime memory artifacts (gitignored).
DEFAULT_MEMORY_ROOT = Path("data/runtime")


@dataclass(frozen=True)
class MemoryConfig:
    daily_summary_retention_days: int = 90
    raw_audit_retention_days: int = 30
    agent_response_retention_days: int = 14
    proposal_retention_days: int = 30
    keep_weekly_archives: bool = True
    max_playbook_lessons_per_team: int = 100
    max_daily_summaries_in_prompt: int = 5
    max_lessons_in_prompt: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MemoryConfig":
        if env is None:
            env = os.environ
        return cls(
            daily_summary_retention_days=_read_int(env, "MEMORY_DAILY_SUMMARY_RETENTION_DAYS", 90),
            raw_audit_retention_days=_read_int(env, "MEMORY_RAW_AUDIT_RETENTION_DAYS", 30),
            agent_response_retention_days=_read_int(env, "MEMORY_AGENT_RESPONSE_RETENTION_DAYS", 14),
            proposal_retention_days=_read_int(env, "MEMORY_PROPOSAL_RETENTION_DAYS", 30),
            keep_weekly_archives=_read_bool(env, "MEMORY_KEEP_WEEKLY_ARCHIVES", True),
            max_playbook_lessons_per_team=_read_int(env, "MEMORY_MAX_PLAYBOOK_LESSONS_PER_TEAM", 100),
            max_daily_summaries_in_prompt=_read_int(env, "MEMORY_MAX_DAILY_SUMMARIES_IN_PROMPT", 5),
            max_lessons_in_prompt=_read_int(env, "MEMORY_MAX_LESSONS_IN_PROMPT", 10),
        )

    def retention_days_for(self, category: str) -> int | None:
        """Retention window (days) for a memory category, or None when never auto-pruned."""

        return {
            "daily_summary": self.daily_summary_retention_days,
            "raw_audit": self.raw_audit_retention_days,
            "agent_response": self.agent_response_retention_days,
            "proposal": self.proposal_retention_days,
        }.get(category)

    def summary(self) -> dict[str, Any]:
        return asdict(self)


# Memory category -> directory. With the default (``root=None``) the production
# layout is returned (agent responses / paper cycles live under ``data/notes``,
# everything else under the gitignored ``data/runtime`` tree). When an explicit
# ``root`` is given (tests), EVERY category is isolated beneath that root so a
# test can never touch real files. Durable layers (playbook) are never
# auto-deleted; raw layers are pruned by retention.
def memory_dirs(root: Path | str | None = None) -> dict[str, Path]:
    if root is None:
        return {
            "daily_summary": DEFAULT_MEMORY_ROOT / "eod_reports",
            "daily_learning": DEFAULT_MEMORY_ROOT / "daily_learning",
            "raw_audit": DEFAULT_MEMORY_ROOT / "loop_audit",
            "agent_response": Path("data/notes/agent_responses"),
            "proposal": Path("data/notes/paper_cycles"),
            "portfolio_review": DEFAULT_MEMORY_ROOT / "portfolio_reviews",
            "playbook": DEFAULT_MEMORY_ROOT / "playbook",
            "weekly": DEFAULT_MEMORY_ROOT / "weekly_reviews",
            "archive": DEFAULT_MEMORY_ROOT / "memory_archives",
        }
    root = Path(root)
    return {
        "daily_summary": root / "eod_reports",
        "daily_learning": root / "daily_learning",
        "raw_audit": root / "loop_audit",
        "agent_response": root / "agent_responses",
        "proposal": root / "paper_cycles",
        "portfolio_review": root / "portfolio_reviews",
        "playbook": root / "playbook",          # durable, never auto-deleted
        "weekly": root / "weekly_reviews",
        "archive": root / "memory_archives",     # compressed weekly archives
    }


__all__ = ["MemoryConfig", "DEFAULT_MEMORY_ROOT", "memory_dirs"]
