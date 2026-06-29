"""Weekly review delivery state + eligibility (Phase 7X).

Runs the non-trading weekly synthesis once per team per ISO week, after the last
completed regular market session of the week (deterministically: a trading day,
market closed, and the next Alpaca open is in a different ISO week). Tracks a
durable result/delivery record so it never runs twice and a failed Discord post
is retried later. Never trades. No secrets stored.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.competition.market_time import to_ny
from src.competition.weekly_synthesis import DEFAULT_WEEKLY_DIR, iso_week_tag

DELIVERY_STATE_FILE = "weekly_delivery_state.json"
TERMINAL_ERRORS = {"discord_not_configured"}


@dataclass
class WeeklyRecord:
    team_id: str
    week_tag: str
    generated: bool = False
    delivered: bool = False
    destination: str | None = None
    error: str | None = None
    attempts: int = 0
    last_attempt_at: str | None = None

    @property
    def retry_pending(self) -> bool:
        return (
            self.generated and not self.delivered
            and self.error is not None and self.error not in TERMINAL_ERRORS
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["retry_pending"] = self.retry_pending
        return data


def _state_path(weekly_dir: Path | str) -> Path:
    return Path(weekly_dir) / DELIVERY_STATE_FILE


def _key(team_id: str, week_tag: str) -> str:
    return f"{team_id}:{week_tag}"


def load_weekly_state(weekly_dir: Path | str = DEFAULT_WEEKLY_DIR) -> dict[str, WeeklyRecord]:
    path = _state_path(weekly_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, WeeklyRecord] = {}
    for key, rec in (raw or {}).items():
        if isinstance(rec, dict):
            rec.pop("retry_pending", None)
            try:
                out[key] = WeeklyRecord(**rec)
            except TypeError:
                continue
    return out


def save_weekly_state(state: dict[str, WeeklyRecord], weekly_dir: Path | str = DEFAULT_WEEKLY_DIR) -> None:
    directory = Path(weekly_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _state_path(directory).write_text(
        json.dumps({k: v.as_dict() for k, v in state.items()}, indent=2), encoding="utf-8"
    )


def get_weekly_record(team_id: str, week_tag: str, *, weekly_dir: Path | str = DEFAULT_WEEKLY_DIR) -> WeeklyRecord:
    return load_weekly_state(weekly_dir).get(_key(team_id, week_tag), WeeklyRecord(team_id=team_id, week_tag=week_tag))


def upsert_weekly_record(record: WeeklyRecord, *, weekly_dir: Path | str = DEFAULT_WEEKLY_DIR) -> None:
    state = load_weekly_state(weekly_dir)
    state[_key(record.team_id, record.week_tag)] = record
    save_weekly_state(state, weekly_dir)


def is_last_session_of_week(*, calendar_day: dict[str, Any] | None, next_open_iso: str | None,
                            now: datetime | None = None) -> bool:
    """True when today is a trading day whose next open falls in a later ISO week."""

    if calendar_day is None or not next_open_iso:
        return False
    try:
        next_open = to_ny(datetime.fromisoformat(next_open_iso))
    except (TypeError, ValueError):
        return False
    return iso_week_tag(next_open) != iso_week_tag(now)


def weekly_run_eligible(
    team_id: str,
    *,
    clock_is_open: bool | None,
    calendar_day: dict[str, Any] | None,
    next_open_iso: str | None,
    now: datetime | None = None,
    weekly_dir: Path | str = DEFAULT_WEEKLY_DIR,
    force: bool = False,
) -> tuple[bool, str]:
    """Once per team/week, after the last completed regular session of the week."""

    week_tag = iso_week_tag(now)
    record = get_weekly_record(team_id, week_tag, weekly_dir=weekly_dir)
    if record.delivered:
        return False, f"already completed weekly review for {team_id} {week_tag}"
    if record.retry_pending:
        return True, f"retry pending (prior error: {record.error})"
    if force:
        return True, f"forced weekly run for {team_id} {week_tag}"
    if calendar_day is None:
        return False, "not a trading day; weekly runs after the last session of the week"
    if clock_is_open:
        return False, "market open; weekly runs after the regular close"
    if not is_last_session_of_week(calendar_day=calendar_day, next_open_iso=next_open_iso, now=now):
        return False, "not the last session of the ISO week yet"
    return True, f"eligible: last session of {week_tag}"


__all__ = [
    "DELIVERY_STATE_FILE", "WeeklyRecord",
    "load_weekly_state", "save_weekly_state", "get_weekly_record", "upsert_weekly_record",
    "is_last_session_of_week", "weekly_run_eligible",
]
