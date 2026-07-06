"""Per-agent persistent memory — how the agents "get smarter" over time.

Each of the six agents (2 teams x 3 roles) owns one JSON file:

* ``playbook``  — a short list of durable principles distilled from experience.
* ``lessons``   — dated lessons appended after each trading day.
* ``stats``     — days recorded and wins/losses vs SPY, so an agent knows its
  own track record.

Memory is injected into every prompt (bounded), grows daily via end-of-day
reflection, and is periodically compacted: old lessons are distilled into the
playbook so context stays small while knowledge accumulates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_LESSONS_KEPT = 30          # compaction trigger
LESSONS_KEPT_AFTER_COMPACT = 10
MAX_PLAYBOOK_ITEMS = 12
MAX_LESSONS_IN_PROMPT = 12


@dataclass
class AgentMemory:
    team_id: str
    role: str
    path: Path
    playbook: list[str] = field(default_factory=list)
    lessons: list[dict] = field(default_factory=list)  # {"date": str, "text": str}
    days_recorded: int = 0
    wins_vs_spy: int = 0
    losses_vs_spy: int = 0

    @classmethod
    def load(cls, team_id: str, role: str, data_dir: Path) -> "AgentMemory":
        path = Path(data_dir) / "memory" / team_id / f"{role}.json"
        memory = cls(team_id=team_id, role=role, path=path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                memory.playbook = [str(x) for x in data.get("playbook", [])][:MAX_PLAYBOOK_ITEMS]
                memory.lessons = [
                    {"date": str(l.get("date", "")), "text": str(l.get("text", ""))}
                    for l in data.get("lessons", [])
                    if str(l.get("text", "")).strip()
                ]
                stats = data.get("stats", {})
                memory.days_recorded = int(stats.get("days_recorded", 0))
                memory.wins_vs_spy = int(stats.get("wins_vs_spy", 0))
                memory.losses_vs_spy = int(stats.get("losses_vs_spy", 0))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                # A corrupt memory file starts fresh rather than crashing the day.
                pass
        return memory

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "team_id": self.team_id,
            "role": self.role,
            "playbook": self.playbook[:MAX_PLAYBOOK_ITEMS],
            "lessons": self.lessons,
            "stats": {
                "days_recorded": self.days_recorded,
                "wins_vs_spy": self.wins_vs_spy,
                "losses_vs_spy": self.losses_vs_spy,
            },
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add_lessons(self, day: str, texts: list[str]) -> None:
        for text in texts:
            cleaned = str(text).strip()
            if cleaned:
                self.lessons.append({"date": day, "text": cleaned})

    def set_playbook(self, items: list[str]) -> None:
        cleaned = [str(x).strip() for x in items if str(x).strip()]
        if cleaned:
            self.playbook = cleaned[:MAX_PLAYBOOK_ITEMS]

    def record_day(self, beat_spy: bool | None) -> None:
        self.days_recorded += 1
        if beat_spy is True:
            self.wins_vs_spy += 1
        elif beat_spy is False:
            self.losses_vs_spy += 1

    @property
    def needs_compaction(self) -> bool:
        return len(self.lessons) > MAX_LESSONS_KEPT

    def compact(self, new_playbook: list[str]) -> None:
        """Apply a distilled playbook and drop the oldest lessons."""

        self.set_playbook(new_playbook)
        self.lessons = self.lessons[-LESSONS_KEPT_AFTER_COMPACT:]

    def render(self, max_chars: int = 2400) -> str:
        """Compact text block for prompts: track record + playbook + recent lessons."""

        lines: list[str] = []
        if self.days_recorded:
            lines.append(
                f"Track record: {self.days_recorded} day(s) recorded, "
                f"{self.wins_vs_spy} beat SPY, {self.losses_vs_spy} lost to SPY."
            )
        if self.playbook:
            lines.append("Playbook (durable principles from your own past results):")
            lines.extend(f"- {item}" for item in self.playbook)
        recent = self.lessons[-MAX_LESSONS_IN_PROMPT:]
        if recent:
            lines.append("Recent lessons:")
            lines.extend(f"- [{l['date']}] {l['text']}" for l in recent)
        if not lines:
            lines.append("No memory yet — this is your first day. Trade carefully and learn.")
        text = "\n".join(lines)
        return text[:max_chars]


def load_team_memories(team_id: str, roles: tuple[str, ...], data_dir: Path) -> dict[str, AgentMemory]:
    return {role: AgentMemory.load(team_id, role, data_dir) for role in roles}
