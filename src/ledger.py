"""Trade ledger: one JSONL file per team, one line per order attempt.

Every line ties an order to the thesis that produced it, so end-of-day
reflection can ask "did the thing we believed actually happen?".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.market_time import now_utc, ny_trading_date


@dataclass
class TradeRecord:
    ts: str
    date: str
    team_id: str
    symbol: str
    action: str            # buy | sell | short | cover
    order_side: str        # buy | sell
    qty: int
    est_price: float | None
    est_notional: float
    thesis: str
    exit_plan: str
    confidence: float
    submitted: bool
    order_id: str | None
    status: str | None
    error: str | None


def _ledger_path(data_dir: Path, team_id: str) -> Path:
    return Path(data_dir) / "ledger" / f"{team_id}_trades.jsonl"


def record_trade(
    data_dir: Path,
    team_id: str,
    *,
    symbol: str,
    action: str,
    order_side: str,
    qty: int,
    est_price: float | None,
    est_notional: float,
    thesis: str,
    exit_plan: str,
    confidence: float,
    submitted: bool,
    order_id: str | None,
    status: str | None,
    error: str | None,
) -> TradeRecord:
    record = TradeRecord(
        ts=now_utc().isoformat(),
        date=ny_trading_date().isoformat(),
        team_id=team_id,
        symbol=symbol,
        action=action,
        order_side=order_side,
        qty=qty,
        est_price=est_price,
        est_notional=round(est_notional, 2),
        thesis=thesis,
        exit_plan=exit_plan,
        confidence=confidence,
        submitted=submitted,
        order_id=order_id,
        status=status,
        error=error,
    )
    path = _ledger_path(data_dir, team_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record)) + "\n")
    return record


def read_trades(data_dir: Path, team_id: str, date: str | None = None) -> list[dict]:
    """All ledger rows (optionally for one ET date), oldest first."""

    path = _ledger_path(data_dir, team_id)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if date is None or row.get("date") == date:
            rows.append(row)
    return rows
