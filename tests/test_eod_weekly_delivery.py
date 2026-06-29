"""Tests for auto EOD + weekly delivery eligibility, retry, and status (Phase 7X)."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from src.competition.eod_delivery import (
    DeliveryRecord,
    eod_send_eligible,
    get_record,
    upsert_record,
)
from src.competition.weekly_delivery import (
    WeeklyRecord,
    is_last_session_of_week,
    upsert_weekly_record,
    weekly_run_eligible,
)

# A Monday 17:00 ET (after a regular close) in UTC.
NOW = datetime(2026, 6, 29, 21, 0, tzinfo=timezone.utc)
TRADING_DAY = {"date": "2026-06-29", "open": "2026-06-29T09:30:00-04:00",
               "close": "2026-06-29T16:00:00-04:00"}


# --- EOD eligibility ----------------------------------------------------------


def test_eod_eligible_after_close_on_trading_day(tmp_path):
    ok, why = eod_send_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                now=NOW, eod_dir=tmp_path)
    assert ok is True


def test_eod_not_sent_when_market_open(tmp_path):
    ok, why = eod_send_eligible("team_alpha", clock_is_open=True, calendar_day=TRADING_DAY,
                                now=NOW, eod_dir=tmp_path)
    assert ok is False and "open" in why


def test_eod_not_sent_on_weekend_or_holiday(tmp_path):
    ok, why = eod_send_eligible("team_alpha", clock_is_open=False, calendar_day=None,
                                now=NOW, eod_dir=tmp_path)
    assert ok is False and "trading day" in why


def test_eod_not_sent_pre_open(tmp_path):
    pre = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)  # 08:00 ET, before the 16:00 close
    ok, why = eod_send_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                now=pre, eod_dir=tmp_path)
    assert ok is False and ("pre-open" in why or "before today's regular session close" in why)


def test_eod_not_sent_twice_after_delivery(tmp_path):
    rec = DeliveryRecord(team_id="team_alpha", trading_date="2026-06-29",
                         generated=True, delivered=True, destination="paper_trading_log")
    upsert_record(rec, eod_dir=tmp_path)
    ok, why = eod_send_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                now=NOW, eod_dir=tmp_path)
    assert ok is False and "already delivered" in why


def test_eod_retries_after_discord_failure(tmp_path):
    # generated but not delivered with a transient error -> retry pending -> eligible again.
    rec = DeliveryRecord(team_id="team_alpha", trading_date="2026-06-29",
                         generated=True, delivered=False, error="send_failed:team_alpha_channel")
    upsert_record(rec, eod_dir=tmp_path)
    assert rec.retry_pending is True
    ok, why = eod_send_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                now=NOW, eod_dir=tmp_path)
    assert ok is True and "retry pending" in why


def test_eod_terminal_error_does_not_retry(tmp_path):
    rec = DeliveryRecord(team_id="team_alpha", trading_date="2026-06-29",
                         generated=True, delivered=False, error="discord_not_configured")
    assert rec.retry_pending is False  # config error is terminal-for-today, no retry spam


def test_eod_status_reports_states(tmp_path):
    upsert_record(DeliveryRecord(team_id="team_beta", trading_date="2026-06-29",
                                 generated=True, delivered=True, destination="team_beta_channel",
                                 attempts=1), eod_dir=tmp_path)
    rec = get_record("team_beta", "2026-06-29", eod_dir=tmp_path)
    assert rec.generated and rec.delivered and rec.destination == "team_beta_channel"
    assert rec.attempts == 1 and rec.retry_pending is False


# --- weekly eligibility -------------------------------------------------------


def test_weekly_last_session_detection():
    # Friday close -> next open is next ISO week -> last session of week.
    friday = datetime(2026, 6, 26, 21, 0, tzinfo=timezone.utc)
    next_open_mon = "2026-06-29T09:30:00-04:00"
    assert is_last_session_of_week(calendar_day=TRADING_DAY, next_open_iso=next_open_mon, now=friday) is True
    # Monday close -> next open Tuesday (same week) -> not last session.
    next_open_tue = "2026-06-30T09:30:00-04:00"
    assert is_last_session_of_week(calendar_day=TRADING_DAY, next_open_iso=next_open_tue, now=NOW) is False


def test_weekly_eligible_only_on_last_session(tmp_path):
    friday = datetime(2026, 6, 26, 21, 0, tzinfo=timezone.utc)
    ok, _ = weekly_run_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                next_open_iso="2026-06-29T09:30:00-04:00", now=friday, weekly_dir=tmp_path)
    assert ok is True
    ok2, _ = weekly_run_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                 next_open_iso="2026-06-30T09:30:00-04:00", now=NOW, weekly_dir=tmp_path)
    assert ok2 is False


def test_weekly_runs_once_per_week(tmp_path):
    friday = datetime(2026, 6, 26, 21, 0, tzinfo=timezone.utc)
    from src.competition.weekly_synthesis import iso_week_tag
    upsert_weekly_record(WeeklyRecord(team_id="team_alpha", week_tag=iso_week_tag(friday),
                                      generated=True, delivered=True), weekly_dir=tmp_path)
    ok, why = weekly_run_eligible("team_alpha", clock_is_open=False, calendar_day=TRADING_DAY,
                                  next_open_iso="2026-06-29T09:30:00-04:00", now=friday, weekly_dir=tmp_path)
    assert ok is False and "already completed" in why


def test_weekly_modules_never_submit_orders():
    import src.competition.weekly_delivery as wd
    import src.competition.eod_delivery as ed
    for mod in (wd, ed):
        src = inspect.getsource(mod)
        assert "submit_order" not in src and "submit_paper" not in src
