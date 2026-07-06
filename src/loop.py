"""The forever loop: trade all day, learn at the close, sleep till the open.

    while True:
        market open?   -> run a cycle for each team, wait CYCLE_MINUTES
        just closed?   -> run the end-of-day pass once per trading day
        market closed? -> sleep until the next open

One process, no watchdog, no background threads. Ctrl+C exits cleanly. The
kill switch pauses trading without stopping the loop. All state that must
survive a restart lives in files under ``data/``.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from src.broker import broker_for_team
from src.config import TEAM_IDS, Settings
from src.cycle import run_team_cycle
from src.eod import run_eod
from src.kill_switch import read_kill_switch
from src.llm import LLM
from src.market_time import now_utc, ny_trading_date, to_ny
from src.notify import report_error

MAX_SLEEP_CHUNK = 1800  # print a heartbeat at least every 30 minutes


def _eod_marker_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "runtime" / "eod_done.txt"


def _eod_done_for(settings: Settings, date: str) -> bool:
    path = _eod_marker_path(settings)
    return path.exists() and path.read_text(encoding="utf-8").strip() == date


def _mark_eod_done(settings: Settings, date: str) -> None:
    path = _eod_marker_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(date, encoding="utf-8")


def is_pre_open_today(next_open: datetime | None, now: datetime | None = None) -> bool:
    """True when the market's next open falls on TODAY'S New York date — i.e.
    we are before the open of a trading day, not after its close. Compared in
    NY time because UTC dates roll over at 8pm ET."""

    if next_open is None:
        return False
    return to_ny(next_open).date() == ny_trading_date(now)


def _sleep_until(target: datetime | None, reason: str) -> None:
    if target is None:
        print(f"{reason} — next-open time unknown; checking again in 15 minutes.")
        time.sleep(900)
        return
    remaining = (target - now_utc()).total_seconds()
    if remaining <= 0:
        return
    chunk = min(remaining + 5, MAX_SLEEP_CHUNK)
    hours = remaining / 3600
    print(f"{reason} — sleeping {chunk / 60:.0f} min (next open in {hours:.1f}h at {target}).")
    time.sleep(chunk)


def run_forever(settings: Settings) -> None:
    print("=" * 72)
    print("EXALTED AGENT TRADING LAB — Alpha vs Beta vs SPY (paper trading only)")
    print(f"Teams: {', '.join(TEAM_IDS)} | cycle every {settings.cycle_minutes} min while market is open")
    print(f"LLM: {settings.llm_provider} / {settings.model_default} | dry_run={settings.dry_run}")
    print("Stop with Ctrl+C. Pause trading with: python -m src.main kill on")
    print("=" * 72)

    llm = LLM(settings)  # fail fast at startup if the provider is misconfigured
    brokers = {team_id: broker_for_team(settings, team_id) for team_id in TEAM_IDS}

    while True:
        try:
            kill_state = read_kill_switch()
            if kill_state.engaged:
                print(kill_state.describe() + " Checking again in 5 minutes.")
                time.sleep(300)
                continue

            clock = brokers[TEAM_IDS[0]].clock()

            if clock.is_open:
                cycle_started = time.monotonic()
                for team_id in TEAM_IDS:
                    result = run_team_cycle(settings, team_id, broker=brokers[team_id], llm=llm)
                    print(f"\n--- {team_id} cycle @ {result.started_at} ---")
                    for line in result.narrative:
                        print(f"  {line}")
                    if result.error:
                        print(f"  !!! {result.error}")
                elapsed = time.monotonic() - cycle_started
                wait = max(60.0, settings.cycle_minutes * 60 - elapsed)
                next_close = clock.next_close
                print(f"\nNext cycle in {wait / 60:.0f} min (market closes at {next_close}).")
                time.sleep(wait)
                continue

            # Market closed. Run EOD once per trading day, after the close.
            today = ny_trading_date().isoformat()
            calendar = brokers[TEAM_IDS[0]].calendar_day(ny_trading_date())
            was_trading_day = calendar is not None
            if was_trading_day and not _eod_done_for(settings, today):
                # Only run the EOD pass after the close — not pre-open on the
                # same ET date.
                if not is_pre_open_today(clock.next_open):
                    print(f"Market closed — running end-of-day pass for {today}...")
                    try:
                        report = run_eod(settings, llm=llm)
                        print(f"EOD complete. Report: {report}")
                    except Exception as exc:  # noqa: BLE001 - EOD failure must not kill the loop
                        report_error(settings, "end-of-day pass", str(exc))
                    _mark_eod_done(settings, today)

            _sleep_until(clock.next_open, "Market closed")

        except KeyboardInterrupt:
            print("\nStopped by user. Goodbye.")
            return
        except Exception as exc:  # noqa: BLE001 - keep the loop alive, loudly
            report_error(settings, "loop", f"{exc}. Retrying in 5 minutes.")
            time.sleep(300)
