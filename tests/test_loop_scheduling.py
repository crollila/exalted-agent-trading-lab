"""Loop scheduling decisions (pure helpers; the loop itself is not run)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.loop import is_pre_open_today

NY = ZoneInfo("America/New_York")


def ny_dt(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NY)


def test_pre_open_morning_is_true():
    # Monday 8:00 ET, market opens Monday 9:30 ET -> pre-open, no EOD yet.
    now = ny_dt(2026, 7, 6, 8, 0)
    next_open = ny_dt(2026, 7, 6, 9, 30)
    assert is_pre_open_today(next_open, now=now)


def test_after_close_same_evening_is_false():
    # Monday 16:30 ET, next open Tuesday -> post-close, EOD should run.
    now = ny_dt(2026, 7, 6, 16, 30)
    next_open = ny_dt(2026, 7, 7, 9, 30)
    assert not is_pre_open_today(next_open, now=now)


def test_late_evening_after_utc_rollover_is_false():
    # Monday 21:00 ET is already Tuesday in UTC — the bug this guards against.
    # Next open Tuesday 9:30 ET; today (ET) is still Monday -> EOD must run.
    now = ny_dt(2026, 7, 6, 21, 0)
    next_open = ny_dt(2026, 7, 7, 9, 30)
    assert not is_pre_open_today(next_open, now=now)


def test_weekend_is_false():
    # Sunday: next open Monday -> not pre-open *today*; EOD is separately
    # gated by the trading calendar (Sunday is not a trading day).
    now = ny_dt(2026, 7, 5, 12, 0)
    next_open = ny_dt(2026, 7, 6, 9, 30)
    assert not is_pre_open_today(next_open, now=now)


def test_unknown_next_open_is_false():
    assert not is_pre_open_today(None, now=ny_dt(2026, 7, 6, 8, 0))
