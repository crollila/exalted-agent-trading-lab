"""The competition scoreboard: every trading day, each team's return is scored
against SPY and against the other team. This file is the answer to "who is
winning?".

Day return basis: Alpaca's ``equity`` vs ``last_equity`` (equity at previous
trading-day close), so intraday deposits/withdrawals aside, it is the clean
one-day account return. SPY's day return uses the previous close vs the latest
price from the same snapshot call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import TEAM_DISPLAY_NAMES, TEAM_IDS


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "scoreboard.json"


def load_scoreboard(data_dir: Path) -> dict[str, Any]:
    path = _path(data_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"days": []}


def _save(data_dir: Path, scoreboard: dict[str, Any]) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scoreboard, indent=2), encoding="utf-8")


def record_day(
    data_dir: Path,
    *,
    date: str,
    team_returns: dict[str, float | None],   # team_id -> day return (e.g. 0.004)
    team_equities: dict[str, float | None],  # team_id -> closing equity
    spy_return: float | None,
) -> dict[str, Any]:
    """Idempotently record (or overwrite) one trading day's result."""

    scoreboard = load_scoreboard(data_dir)
    days = [d for d in scoreboard["days"] if d.get("date") != date]

    def _beat_spy(team_id: str) -> bool | None:
        r = team_returns.get(team_id)
        if r is None or spy_return is None:
            return None
        return r > spy_return

    alpha_r = team_returns.get("team_alpha")
    beta_r = team_returns.get("team_beta")
    if alpha_r is None or beta_r is None:
        head_to_head = None
    elif abs(alpha_r - beta_r) < 1e-9:
        head_to_head = "tie"
    else:
        head_to_head = "team_alpha" if alpha_r > beta_r else "team_beta"

    day = {
        "date": date,
        "spy_return": spy_return,
        "teams": {
            team_id: {
                "return": team_returns.get(team_id),
                "equity": team_equities.get(team_id),
                "beat_spy": _beat_spy(team_id),
            }
            for team_id in TEAM_IDS
        },
        "head_to_head": head_to_head,
    }
    days.append(day)
    days.sort(key=lambda d: d["date"])
    scoreboard["days"] = days
    _save(data_dir, scoreboard)
    return day


TRADING_DAYS_PER_YEAR = 252


def risk_stats(daily_returns: list[float]) -> dict[str, float | None]:
    """Annualized Sharpe (0% risk-free), annualized volatility, max drawdown.

    Needs >= 5 observed days for Sharpe/vol to mean anything; below that they
    are None rather than noise dressed as statistics.
    """

    n = len(daily_returns)
    out: dict[str, float | None] = {"sharpe": None, "volatility": None, "max_drawdown": None}
    if n == 0:
        return out

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in daily_returns:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    out["max_drawdown"] = max_dd

    if n >= 5:
        mean = sum(daily_returns) / n
        variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        std = variance ** 0.5
        out["volatility"] = std * TRADING_DAYS_PER_YEAR ** 0.5
        if std > 1e-12:
            out["sharpe"] = (mean / std) * TRADING_DAYS_PER_YEAR ** 0.5
    return out


def totals(scoreboard: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        team_id: {
            "days": 0, "beat_spy": 0, "lost_to_spy": 0, "h2h_wins": 0, "cum_return": None,
            "sharpe": None, "volatility": None, "max_drawdown": None,
        }
        for team_id in TEAM_IDS
    }
    returns_by_team: dict[str, list[float]] = {team_id: [] for team_id in TEAM_IDS}
    ties = 0
    for day in scoreboard.get("days", []):
        h2h = day.get("head_to_head")
        if h2h == "tie":
            ties += 1
        for team_id in TEAM_IDS:
            team_day = (day.get("teams") or {}).get(team_id) or {}
            r = team_day.get("return")
            if r is None:
                continue
            stats = out[team_id]
            stats["days"] += 1
            returns_by_team[team_id].append(r)
            if team_day.get("beat_spy") is True:
                stats["beat_spy"] += 1
            elif team_day.get("beat_spy") is False:
                stats["lost_to_spy"] += 1
            if h2h == team_id:
                stats["h2h_wins"] += 1
            compounded = (1 + (stats["cum_return"] or 0.0)) * (1 + r) - 1
            stats["cum_return"] = compounded
    for team_id in TEAM_IDS:
        out[team_id].update(risk_stats(returns_by_team[team_id]))
    out["ties"] = ties
    return out


def spy_cumulative(scoreboard: dict[str, Any]) -> float | None:
    cum = None
    for day in scoreboard.get("days", []):
        r = day.get("spy_return")
        if r is None:
            continue
        cum = (1 + (cum or 0.0)) * (1 + r) - 1
    return cum


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.2f}%"


def render(scoreboard: dict[str, Any], last_days: int = 10) -> str:
    """Human-readable scoreboard summary."""

    days = scoreboard.get("days", [])
    stats = totals(scoreboard)
    spy_cum = spy_cumulative(scoreboard)

    lines = ["=== COMPETITION SCOREBOARD ==="]
    if not days:
        lines.append("No trading days recorded yet. The first end-of-day pass will populate this.")
        return "\n".join(lines)

    lines.append(f"Trading days scored: {len(days)} | SPY cumulative: {_pct(spy_cum)}")
    for team_id in TEAM_IDS:
        s = stats[team_id]
        sharpe = "n/a" if s["sharpe"] is None else f"{s['sharpe']:.2f}"
        drawdown = "n/a" if s["max_drawdown"] is None else _pct(s["max_drawdown"])
        lines.append(
            f"{TEAM_DISPLAY_NAMES[team_id]}: cumulative {_pct(s['cum_return'])} | "
            f"vs SPY {s['beat_spy']}W-{s['lost_to_spy']}L | head-to-head wins {s['h2h_wins']} | "
            f"Sharpe {sharpe} | max drawdown {drawdown}"
        )
    if stats["ties"]:
        lines.append(f"Head-to-head ties: {stats['ties']}")

    lines.append("")
    lines.append(f"Last {min(last_days, len(days))} day(s):")
    for day in days[-last_days:]:
        alpha = day["teams"]["team_alpha"]
        beta = day["teams"]["team_beta"]
        h2h = day.get("head_to_head")
        winner = TEAM_DISPLAY_NAMES.get(h2h, "tie") if h2h else "n/a"
        lines.append(
            f"  {day['date']}: Alpha {_pct(alpha.get('return'))} | "
            f"Beta {_pct(beta.get('return'))} | SPY {_pct(day.get('spy_return'))} "
            f"| day winner: {winner}"
        )
    return "\n".join(lines)
