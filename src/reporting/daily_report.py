from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DailyReport:
    report_date: date
    strategy_id: str
    summary: dict

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date.isoformat(),
            "strategy_id": self.strategy_id,
            "summary": self.summary,
        }
