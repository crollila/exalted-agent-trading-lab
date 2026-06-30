"""Same-period benchmark anchors + truthful SPY attribution (Phase 7Z).

Every SPY-relative claim must come from the **same** period anchors:

* team equity at period start and current/end,
* SPY price at period start and current/end,
* period start/end timestamps,
* a timeframe label: ``intraday`` | ``weekly`` | ``all_time``.

Team return, SPY return, and excess are computed only from those shared anchors.
When an anchor is missing the value is ``None`` and callers must render ``n/a`` —
never "beat SPY" or "lost to SPY". This corrects math that could report e.g.
``+1.13%`` excess when the team return is ``0.0000`` and SPY is ``-0.0012`` (the
only valid excess there is ``+0.0012``), by refusing to mix a live team return
with a stale SPY return.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Timeframe labels.
TIMEFRAME_INTRADAY = "intraday"
TIMEFRAME_WEEKLY = "weekly"
TIMEFRAME_ALL_TIME = "all_time"
TIMEFRAME_UNKNOWN = "unknown"


def _ret(start: float | None, end: float | None) -> float | None:
    """Period return from two same-series anchors, or None when not computable."""

    if start is None or end is None:
        return None
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return None
    if start_f <= 0:
        return None
    return (end_f - start_f) / start_f


@dataclass(frozen=True)
class BenchmarkAnchors:
    """Immutable same-period anchors for a team vs SPY. No secrets."""

    team_id: str
    timeframe: str = TIMEFRAME_UNKNOWN
    period_start: str | None = None
    period_end: str | None = None
    team_start_equity: float | None = None
    team_end_equity: float | None = None
    spy_start_price: float | None = None
    spy_end_price: float | None = None

    @property
    def team_return(self) -> float | None:
        return _ret(self.team_start_equity, self.team_end_equity)

    @property
    def spy_return(self) -> float | None:
        return _ret(self.spy_start_price, self.spy_end_price)

    @property
    def excess_return(self) -> float | None:
        """Team minus SPY — only when BOTH come from these shared anchors."""

        team = self.team_return
        spy = self.spy_return
        if team is None or spy is None:
            return None
        return team - spy

    @property
    def anchors_available(self) -> bool:
        """True only when team return AND SPY return are both computable."""

        return self.team_return is not None and self.spy_return is not None

    def spy_relative_phrase(self) -> str:
        """A truthful one-line claim, or ``n/a`` when anchors are missing.

        Never states "beat SPY" / "lost to SPY" without same-period anchors.
        """

        excess = self.excess_return
        if excess is None:
            return "n/a (insufficient same-period anchors; no SPY-relative claim)"
        if excess > 0:
            return f"beat SPY by {excess:+.4f} excess (same-period anchors)"
        if excess < 0:
            return f"trailed SPY by {excess:+.4f} excess (same-period anchors)"
        return "matched SPY (0.0000 excess; same-period anchors)"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["team_return"] = self.team_return
        data["spy_return"] = self.spy_return
        data["excess_return"] = self.excess_return
        data["anchors_available"] = self.anchors_available
        return data


def build_benchmark_anchors(
    team_id: str,
    *,
    timeframe: str = TIMEFRAME_UNKNOWN,
    period_start: str | None = None,
    period_end: str | None = None,
    team_start_equity: float | None = None,
    team_end_equity: float | None = None,
    spy_start_price: float | None = None,
    spy_end_price: float | None = None,
) -> BenchmarkAnchors:
    """Build anchors; coerce numerics; leave missing values as None (-> n/a)."""

    def _f(v: Any) -> float | None:
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    return BenchmarkAnchors(
        team_id=team_id,
        timeframe=timeframe or TIMEFRAME_UNKNOWN,
        period_start=period_start,
        period_end=period_end,
        team_start_equity=_f(team_start_equity),
        team_end_equity=_f(team_end_equity),
        spy_start_price=_f(spy_start_price),
        spy_end_price=_f(spy_end_price),
    )


def safe_excess(team_return: float | None, spy_return: float | None) -> float | None:
    """Excess only when BOTH returns are present (same-period). Never invents one.

    Use this anywhere two returns are combined so a live team return is never
    mixed with a stale SPY return, and ``excess`` never silently becomes the team
    return when SPY is unknown.
    """

    if team_return is None or spy_return is None:
        return None
    return team_return - spy_return


__all__ = [
    "TIMEFRAME_INTRADAY",
    "TIMEFRAME_WEEKLY",
    "TIMEFRAME_ALL_TIME",
    "TIMEFRAME_UNKNOWN",
    "BenchmarkAnchors",
    "build_benchmark_anchors",
    "safe_excess",
]
