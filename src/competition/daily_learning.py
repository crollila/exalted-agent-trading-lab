"""Per-team daily learning artifact (Phase 7V).

Links the day's trades / non-trades to prior theses, realized/unrealized
outcomes, observed mistakes or missed opportunities, and next-day hypotheses.

This is RESEARCH FEEDBACK ONLY. It is a saved JSON artifact under the ignored
runtime path; it never changes ``.env``, risk limits, or execution permissions,
and it never submits an order. No secrets are written.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.market_time import ny_trading_date
from src.competition.position_review import TeamPortfolioReview
from src.ui.dashboard_state import redact_secret_like_text

DEFAULT_LEARNING_DIR = Path("data/runtime/daily_learning")
DISCLAIMER = (
    "Research feedback only. Does not change .env, risk limits, or execution "
    "permissions. Paper-only; no live/options/shorting/margin execution."
)


@dataclass
class DailyLearning:
    team_id: str
    trading_date: str
    generated_at: str
    trades: list[dict[str, Any]] = field(default_factory=list)
    non_trades: list[dict[str, Any]] = field(default_factory=list)
    realized_or_unrealized_outcomes: list[dict[str, Any]] = field(default_factory=list)
    mistakes_or_missed: list[str] = field(default_factory=list)
    strategy_risk_observations: list[str] = field(default_factory=list)
    next_day_hypotheses: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def as_dict(self) -> dict[str, Any]:
        return _redact(asdict(self))


def _redact(data: Any) -> Any:
    if isinstance(data, str):
        return redact_secret_like_text(data)
    if isinstance(data, list):
        return [_redact(v) for v in data]
    if isinstance(data, dict):
        return {k: _redact(v) for k, v in data.items()}
    return data


def build_daily_learning(
    review: TeamPortfolioReview,
    *,
    submitted_orders: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> DailyLearning:
    """Deterministically assemble the learning artifact from the day's review."""

    now = now or datetime.now(timezone.utc)
    submitted_orders = submitted_orders or []

    non_trades = [
        {"symbol": p.symbol, "action": p.recommended_action, "reason": p.reason}
        for p in review.positions if p.recommended_action in ("hold", "watch")
    ]
    outcomes = [
        {
            "symbol": p.symbol,
            "return_pct": p.unrealized_pl_pct,
            "thesis_status": p.thesis_status,
            "thesis": p.original_thesis,
        }
        for p in review.positions
    ]
    mistakes: list[str] = []
    for p in review.positions:
        if p.side == "long" and p.unrealized_pl_pct is not None and p.unrealized_pl_pct <= -0.15:
            mistakes.append(
                f"{p.symbol}: held through a {p.unrealized_pl_pct:.0%} drawdown; thesis now "
                f"'{p.thesis_status}'. Consider tighter invalidation next time."
            )
    observations: list[str] = []
    if review.health.block_new_buys:
        observations.append(
            "Capital exhausted/over-deployed: prioritize freeing room (trim/exit) over new entries."
        )
    if review.health.concentration_alerts:
        observations.append(
            "Concentration risk in: " + ", ".join(review.health.concentration_alerts)
            + " - size down toward limits."
        )
    hypotheses: list[str] = []
    losers = [p for p in review.positions if p.side == "long" and p.unrealized_pl_pct is not None]
    losers.sort(key=lambda p: p.unrealized_pl_pct)
    for p in losers[:2]:
        hypotheses.append(
            f"If {p.symbol} fails to reclaim its level, treat the thesis as invalidated and exit."
        )
    if not hypotheses:
        hypotheses.append("Re-evaluate watchlist for fresh catalysts before adding risk.")

    return DailyLearning(
        team_id=review.team_id,
        trading_date=ny_trading_date(now).isoformat(),
        generated_at=now.isoformat(),
        trades=list(submitted_orders),
        non_trades=non_trades,
        realized_or_unrealized_outcomes=outcomes,
        mistakes_or_missed=mistakes,
        strategy_risk_observations=observations,
        next_day_hypotheses=hypotheses,
    )


def save_daily_learning(
    learning: DailyLearning, *, learning_dir: Path | str = DEFAULT_LEARNING_DIR
) -> Path:
    directory = Path(learning_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{learning.team_id}_{learning.trading_date}.json"
    path.write_text(json.dumps(learning.as_dict(), indent=2), encoding="utf-8")
    return path


__all__ = ["DEFAULT_LEARNING_DIR", "DailyLearning", "build_daily_learning", "save_daily_learning"]
