from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskRules:
    min_cash_pct: float = 0.10
    max_position_pct: float = 0.20
    max_daily_turnover_pct: float = 0.30
    max_new_positions_per_day: int = 5
    allow_options: bool = False
    allow_shorting: bool = False
    allow_margin: bool = False
    stocks_only: bool = True
