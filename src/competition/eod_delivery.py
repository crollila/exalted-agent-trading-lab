"""Automatic EOD report delivery state + eligibility (Phase 7X).

Decides — deterministically and from the Alpaca clock/calendar — when the
end-of-day report may be sent (once per team per US trading date, after the
regular session closes; never on weekends/holidays or pre-open), and tracks a
durable delivery record so restarts cannot duplicate a successful send and a
failed Discord delivery is retried on a later iteration.

No secrets are stored. Nothing here trades.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.competition.eod_report import DEFAULT_EOD_DIR
from src.competition.market_time import ny_trading_date, to_ny

DELIVERY_STATE_FILE = "eod_delivery_state.json"
# Errors that are NOT worth retrying every iteration (config-level, not transient).
TERMINAL_ERRORS = {"discord_not_configured"}


@dataclass
class DeliveryRecord:
    team_id: str
    trading_date: str
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


def _state_path(eod_dir: Path | str) -> Path:
    return Path(eod_dir) / DELIVERY_STATE_FILE


def _key(team_id: str, trading_date: str) -> str:
    return f"{team_id}:{trading_date}"


def load_delivery_state(eod_dir: Path | str = DEFAULT_EOD_DIR) -> dict[str, DeliveryRecord]:
    path = _state_path(eod_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt state file is treated as empty
        return {}
    out: dict[str, DeliveryRecord] = {}
    for key, rec in (raw or {}).items():
        if isinstance(rec, dict):
            rec.pop("retry_pending", None)
            try:
                out[key] = DeliveryRecord(**rec)
            except TypeError:
                continue
    return out


def save_delivery_state(state: dict[str, DeliveryRecord], eod_dir: Path | str = DEFAULT_EOD_DIR) -> None:
    directory = Path(eod_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {k: v.as_dict() for k, v in state.items()}
    _state_path(directory).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_record(team_id: str, trading_date: str, *, eod_dir: Path | str = DEFAULT_EOD_DIR) -> DeliveryRecord:
    state = load_delivery_state(eod_dir)
    return state.get(_key(team_id, trading_date), DeliveryRecord(team_id=team_id, trading_date=trading_date))


def upsert_record(record: DeliveryRecord, *, eod_dir: Path | str = DEFAULT_EOD_DIR) -> None:
    state = load_delivery_state(eod_dir)
    state[_key(record.team_id, record.trading_date)] = record
    save_delivery_state(state, eod_dir)


def eod_send_eligible(
    team_id: str,
    *,
    clock_is_open: bool | None,
    calendar_day: dict[str, Any] | None,
    now: datetime | None = None,
    eod_dir: Path | str = DEFAULT_EOD_DIR,
    force: bool = False,
) -> tuple[bool, str]:
    """Deterministic eligibility for sending today's EOD report.

    Eligible only when: today is an Alpaca trading day, the market is closed, and
    the current ET time is at/after the regular session close. Already-delivered
    reports are never re-sent; a failed (generated-but-undelivered) report stays
    eligible so it can retry. ``force`` overrides the clock/calendar gate (still
    not the already-delivered guard).
    """

    now_et = to_ny(now)
    trading_date = ny_trading_date(now).isoformat()
    record = get_record(team_id, trading_date, eod_dir=eod_dir)
    if record.delivered:
        return False, f"already delivered for {team_id} on {trading_date}"
    if record.retry_pending:
        return True, f"retry pending (prior delivery error: {record.error})"
    if force:
        return True, f"forced send for {team_id} ({trading_date})"
    if calendar_day is None:
        return False, "not an Alpaca trading day (weekend/holiday); no EOD"
    if clock_is_open is None:
        return False, "market clock unknown; not auto-sending EOD"
    if clock_is_open:
        return False, "market still open; EOD sends after the regular close"
    close_iso = calendar_day.get("close")
    if close_iso:
        try:
            close_dt = datetime.fromisoformat(close_iso)
            close_et = to_ny(close_dt)
            # Compare on the same ET calendar date; pre-open (before today's close) waits.
            if now_et.date() == close_et.date() and now_et.time() < close_et.time():
                return False, "before today's regular session close (pre-open/intraday)"
        except (TypeError, ValueError):
            pass
    return True, f"after regular close on trading day {trading_date}"


__all__ = [
    "DELIVERY_STATE_FILE", "DeliveryRecord",
    "load_delivery_state", "save_delivery_state", "get_record", "upsert_record",
    "eod_send_eligible",
]
