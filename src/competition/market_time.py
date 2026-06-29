"""Shared market-time helpers (Phase 7U).

Centralizes America/New_York handling so the competition loop, its diagnostic,
and the daily-order reconciliation all agree on what "today" means. Previously
the loop only reasoned in UTC, which made daily usage accounting ambiguous near
the UTC/ET day boundary.

Pure/deterministic: every function accepts an optional ``now`` for testing and
never performs I/O. No secrets are read here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_ny(moment: datetime | None = None) -> datetime:
    """Return ``moment`` (default: now) as an America/New_York aware datetime."""

    moment = moment or now_utc()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(NEW_YORK)


def ny_trading_date(moment: datetime | None = None) -> date:
    """Calendar date in America/New_York for the given moment (default: now)."""

    return to_ny(moment).date()


def ny_session_start_utc(moment: datetime | None = None) -> datetime:
    """UTC timestamp for midnight (00:00) America/New_York of the moment's date.

    Used as the lower bound when counting *today's* paper orders so the per-team
    daily-order cap is scoped to the correct ET trading date regardless of the
    host machine's timezone or the UTC day rollover.
    """

    ny = to_ny(moment)
    midnight_ny = ny.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_ny.astimezone(timezone.utc)


__all__ = [
    "NEW_YORK",
    "now_utc",
    "to_ny",
    "ny_trading_date",
    "ny_session_start_utc",
]
