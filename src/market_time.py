"""America/New_York time helpers so the loop, ledger, and scoreboard agree on
what "today" means regardless of the host machine's timezone.

Pure functions; every one accepts an optional ``moment`` for testing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_ny(moment: datetime | None = None) -> datetime:
    moment = moment or now_utc()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(NEW_YORK)


def ny_trading_date(moment: datetime | None = None) -> date:
    """Calendar date in New York for the given moment (default: now)."""

    return to_ny(moment).date()


def ny_session_start_utc(moment: datetime | None = None) -> datetime:
    """UTC timestamp for midnight New York of the moment's date.

    Lower bound when counting *today's* orders, so daily caps are scoped to the
    ET trading date rather than the UTC one.
    """

    ny = to_ny(moment)
    return ny.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
