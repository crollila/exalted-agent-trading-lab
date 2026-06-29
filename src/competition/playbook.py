"""Durable, curated per-team playbook of validated lessons (Phase 7W).

The playbook is the long-term memory layer: compact, bounded, and only updated
through deterministic evidence checks. An LLM may *propose* a lesson, but a lesson
is only stored/strengthened here when it carries real supporting evidence — never
invented. Lessons are superseded (not silently deleted) when contradicted by newer
evidence, and the store is capped per team.

Categories (per the design): recurring strengths, recurring mistakes, risk /
portfolio-management lessons, strategy observations, preferred market regimes,
known failure modes.

Storage: ``data/runtime/playbook/<team>_playbook.json`` (gitignored, durable).
No secrets are stored. Nothing here trades or changes settings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PLAYBOOK_DIR = Path("data/runtime/playbook")

CATEGORIES = (
    "strength",
    "mistake",
    "risk_lesson",
    "strategy_observation",
    "preferred_regime",
    "failure_mode",
)


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def lesson_id_for(category: str, text: str) -> str:
    digest = hashlib.sha256(f"{category}|{_norm(text)}".encode("utf-8")).hexdigest()
    return digest[:16]


@dataclass
class PlaybookLesson:
    lesson_id: str
    category: str
    text: str
    evidence_count: int = 0
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    action_type: str | None = None
    regime: str | None = None
    created_at: str = ""
    last_validated: str = ""
    retired: bool = False
    superseded_by: str | None = None

    @property
    def active(self) -> bool:
        return not self.retired and self.superseded_by is None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TeamPlaybook:
    team_id: str
    lessons: list[PlaybookLesson] = field(default_factory=list)
    updated_at: str = ""

    # --- persistence ---
    @classmethod
    def path_for(cls, team_id: str, playbook_dir: Path | str = DEFAULT_PLAYBOOK_DIR) -> Path:
        return Path(playbook_dir) / f"{team_id}_playbook.json"

    @classmethod
    def load(cls, team_id: str, playbook_dir: Path | str = DEFAULT_PLAYBOOK_DIR) -> "TeamPlaybook":
        path = cls.path_for(team_id, playbook_dir)
        if not path.exists():
            return cls(team_id=team_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt playbook degrades to empty, never crashes
            return cls(team_id=team_id)
        lessons = [PlaybookLesson(**l) for l in data.get("lessons", []) if isinstance(l, dict)]
        return cls(team_id=team_id, lessons=lessons, updated_at=data.get("updated_at", ""))

    def save(self, playbook_dir: Path | str = DEFAULT_PLAYBOOK_DIR) -> Path:
        path = self.path_for(self.team_id, playbook_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        payload = {"team_id": self.team_id, "updated_at": self.updated_at,
                   "lessons": [l.as_dict() for l in self.lessons]}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    # --- queries ---
    def get(self, lesson_id: str) -> PlaybookLesson | None:
        return next((l for l in self.lessons if l.lesson_id == lesson_id), None)

    def active_lessons(self) -> list[PlaybookLesson]:
        return [l for l in self.lessons if l.active]

    # --- mutations (deterministic) ---
    def upsert(
        self,
        *,
        category: str,
        text: str,
        confidence: float,
        evidence_refs: list[str],
        symbols: list[str] | None = None,
        action_type: str | None = None,
        regime: str | None = None,
        now: datetime | None = None,
    ) -> PlaybookLesson:
        """Add a lesson or strengthen an existing identical one (evidence += 1)."""

        now = now or datetime.now(timezone.utc)
        lid = lesson_id_for(category, text)
        existing = self.get(lid)
        refs = sorted({*(evidence_refs or [])})
        if existing is not None:
            existing.evidence_count += 1
            existing.evidence_refs = sorted({*existing.evidence_refs, *refs})
            existing.confidence = max(existing.confidence, float(confidence))
            existing.last_validated = now.isoformat()
            # Re-validated evidence revives a previously-retired lesson.
            existing.retired = False
            existing.superseded_by = None
            if symbols:
                existing.symbols = sorted({*existing.symbols, *[s.upper() for s in symbols]})
            existing.action_type = existing.action_type or action_type
            existing.regime = existing.regime or regime
            return existing

        lesson = PlaybookLesson(
            lesson_id=lid, category=category, text=text,
            evidence_count=1, confidence=float(confidence), evidence_refs=refs,
            symbols=sorted({s.upper() for s in (symbols or [])}),
            action_type=action_type, regime=regime,
            created_at=now.isoformat(), last_validated=now.isoformat(),
        )
        self.lessons.append(lesson)
        return lesson

    def supersede(self, old_id: str, new_id: str | None = None, *, reason: str = "") -> bool:
        """Mark a stale/contradicted lesson superseded (never deletes it)."""

        lesson = self.get(old_id)
        if lesson is None or not lesson.active:
            return False
        lesson.superseded_by = new_id or "newer_evidence"
        if reason:
            lesson.text = f"{lesson.text} [superseded: {reason}]"
        return True

    def retire(self, lesson_id: str, *, reason: str = "") -> bool:
        """Mark a lesson retired (kept for audit; excluded from retrieval)."""

        lesson = self.get(lesson_id)
        if lesson is None or lesson.retired:
            return False
        lesson.retired = True
        if reason:
            lesson.text = f"{lesson.text} [retired: {reason}]"
        return True

    def enforce_cap(self, max_lessons: int) -> list[str]:
        """Retire the weakest ACTIVE lessons beyond the cap. Returns retired ids.

        Weakness = (lowest evidence_count, then lowest confidence, then oldest
        validation). Retired lessons are kept on disk (not deleted) for audit.
        """

        active = self.active_lessons()
        if len(active) <= max_lessons:
            return []
        ranked = sorted(
            active,
            key=lambda l: (l.evidence_count, l.confidence, l.last_validated),
        )
        retired_ids: list[str] = []
        for lesson in ranked[: len(active) - max_lessons]:
            lesson.retired = True
            lesson.text = f"{lesson.text} [retired: playbook cap {max_lessons}]"
            retired_ids.append(lesson.lesson_id)
        return retired_ids


__all__ = ["PlaybookLesson", "TeamPlaybook", "CATEGORIES", "lesson_id_for", "DEFAULT_PLAYBOOK_DIR"]
