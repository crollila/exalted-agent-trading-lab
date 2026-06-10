from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    alpaca_api_key: str | None
    alpaca_secret_key: str | None
    alpaca_paper: bool | None
    alpaca_base_url: str
    database_path: Path
    dry_run: bool
    starting_equity: float
    min_cash_pct: float
    max_position_pct: float
    max_daily_turnover_pct: float
    max_new_positions_per_day: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        alpaca_paper_raw = os.getenv("ALPACA_PAPER")

        return cls(
            alpaca_api_key=os.getenv("ALPACA_API_KEY"),
            alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY"),
            alpaca_paper=None if alpaca_paper_raw is None else alpaca_paper_raw.lower() == "true",
            alpaca_base_url=os.getenv("ALPACA_BASE_URL", ""),
            database_path=Path(os.getenv("DATABASE_PATH", "data/trading_lab.sqlite3")),
            dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
            starting_equity=float(os.getenv("STARTING_EQUITY", "10000")),
            min_cash_pct=float(os.getenv("MIN_CASH_PCT", "0.10")),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.20")),
            max_daily_turnover_pct=float(os.getenv("MAX_DAILY_TURNOVER_PCT", "0.30")),
            max_new_positions_per_day=int(os.getenv("MAX_NEW_POSITIONS_PER_DAY", "5")),
        )
