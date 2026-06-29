"""End-of-day per-team report + daily learning artifact (Phase 7V).

Deterministic, paper-only, no secrets. Builds a concise Discord summary plus a
full saved Markdown/JSON report once per team per US trading date, after the
regular session closes. Uses the Alpaca market clock (is_open=False) and the ET
trading date so it never sends on a non-trading day and never sends twice.

Nothing here submits orders or calls an LLM; it summarizes already-recorded
state (account, positions review, the day's submitted orders, scorecard, SPY).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.market_time import ny_trading_date, to_ny
from src.competition.position_review import TeamPortfolioReview
from src.ui.dashboard_state import redact_secret_like_text

DEFAULT_EOD_DIR = Path("data/runtime/eod_reports")
STATE_FILE = "eod_sent_state.json"
PAPER_DISCLAIMER = (
    "Paper trading only. No live trading, options, shorting, or margin execution. "
    "Sell-to-close reduces/closes existing long stock only. LLMs never execute orders."
)


@dataclass
class OrderLine:
    symbol: str
    side: str  # buy | sell
    quantity: float
    price: float | None
    notional: float | None
    status: str
    reason: str


@dataclass
class EodReport:
    team_id: str
    trading_date: str
    session_status: str  # closed | open | non-trading | unknown
    starting_equity: float | None
    ending_equity: float | None
    daily_pl: float | None
    daily_return_pct: float | None
    spy_daily_return_pct: float | None
    excess_vs_spy_pct: float | None
    cash: float | None
    buying_power: float | None
    gross_exposure_pct: float | None
    open_position_count: int
    submitted_orders: list[OrderLine] = field(default_factory=list)
    held_positions: list[dict[str, Any]] = field(default_factory=list)
    rejected_or_skipped: list[str] = field(default_factory=list)
    top_winners: list[dict[str, Any]] = field(default_factory=list)
    top_losers: list[dict[str, Any]] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)
    thesis_changes: list[str] = field(default_factory=list)
    next_day_watchlist: list[str] = field(default_factory=list)
    next_day_plan: list[str] = field(default_factory=list)
    disclaimer: str = PAPER_DISCLAIMER

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return _redact(data)


def _redact(data: Any) -> Any:
    if isinstance(data, str):
        return redact_secret_like_text(data)
    if isinstance(data, list):
        return [_redact(v) for v in data]
    if isinstance(data, dict):
        return {k: _redact(v) for k, v in data.items()}
    return data


# --- once-per-trading-date guard ---------------------------------------------


def _state_path(eod_dir: Path | str) -> Path:
    return Path(eod_dir) / STATE_FILE


def load_sent_state(eod_dir: Path | str = DEFAULT_EOD_DIR) -> dict[str, str]:
    path = _state_path(eod_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - a corrupt state file is treated as empty
        return {}


def already_sent(team_id: str, trading_date: str, *, eod_dir: Path | str = DEFAULT_EOD_DIR) -> bool:
    return load_sent_state(eod_dir).get(team_id) == trading_date


def mark_sent(team_id: str, trading_date: str, *, eod_dir: Path | str = DEFAULT_EOD_DIR) -> None:
    directory = Path(eod_dir)
    directory.mkdir(parents=True, exist_ok=True)
    state = load_sent_state(eod_dir)
    state[team_id] = trading_date
    _state_path(eod_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_send_eod(
    team_id: str,
    *,
    market_is_open: bool | None,
    now: datetime | None = None,
    eod_dir: Path | str = DEFAULT_EOD_DIR,
    force: bool = False,
) -> tuple[bool, str]:
    """Decide whether to send the EOD report now. Once per team per ET trading date.

    Only sends when the regular session is known CLOSED (``market_is_open is
    False``) and the report has not already been sent for today's ET date. An
    unknown clock never auto-sends (returns False) unless ``force`` is set.
    """

    trading_date = ny_trading_date(now).isoformat()
    if already_sent(team_id, trading_date, eod_dir=eod_dir):
        return False, f"Already sent EOD for {team_id} on {trading_date}."
    if force:
        return True, f"Forced EOD send for {team_id} ({trading_date})."
    if market_is_open is None:
        return False, "Market clock unknown; not auto-sending EOD (use --force to override)."
    if market_is_open:
        return False, "Market still open; EOD sends after the regular session closes."
    return True, f"Market closed; EOD ready for {team_id} ({trading_date})."


# --- builder ------------------------------------------------------------------


def build_eod_report(
    review: TeamPortfolioReview,
    *,
    starting_equity: float | None,
    spy_daily_return_pct: float | None,
    submitted_orders: list[OrderLine],
    rejected_or_skipped: list[str],
    learnings: list[str],
    thesis_changes: list[str],
    next_day_watchlist: list[str],
    market_is_open: bool | None,
    now: datetime | None = None,
) -> EodReport:
    """Deterministically assemble the EOD report from the day's recorded state."""

    now = now or datetime.now(timezone.utc)
    h = review.health
    ending_equity = review.equity
    daily_pl = None
    daily_ret = None
    if starting_equity and ending_equity is not None and starting_equity > 0:
        daily_pl = ending_equity - starting_equity
        daily_ret = daily_pl / starting_equity
    excess = None
    if daily_ret is not None and spy_daily_return_pct is not None:
        excess = daily_ret - spy_daily_return_pct

    # Winners/losers from the reviewed long positions.
    longs = [p for p in review.positions if p.side == "long" and p.unrealized_pl_pct is not None]
    winners = sorted(longs, key=lambda p: p.unrealized_pl_pct, reverse=True)[:3]
    losers = sorted(longs, key=lambda p: p.unrealized_pl_pct)[:3]

    def _wl(p) -> dict[str, Any]:
        return {"symbol": p.symbol, "return_pct": p.unrealized_pl_pct, "unrealized_pl": p.unrealized_pl}

    held = [
        {"symbol": p.symbol, "action": p.recommended_action, "reason": p.reason,
         "return_pct": p.unrealized_pl_pct, "weight": p.portfolio_weight}
        for p in review.positions if p.recommended_action in ("hold", "watch")
    ]

    plan = _next_day_plan(review)
    session_status = (
        "closed" if market_is_open is False else ("open" if market_is_open else "unknown")
    )
    return EodReport(
        team_id=review.team_id,
        trading_date=ny_trading_date(now).isoformat(),
        session_status=session_status,
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        daily_pl=daily_pl,
        daily_return_pct=daily_ret,
        spy_daily_return_pct=spy_daily_return_pct,
        excess_vs_spy_pct=excess,
        cash=review.cash,
        buying_power=review.buying_power,
        gross_exposure_pct=h.gross_exposure_pct,
        open_position_count=h.open_position_count,
        submitted_orders=submitted_orders,
        held_positions=held,
        rejected_or_skipped=rejected_or_skipped,
        top_winners=[_wl(p) for p in winners],
        top_losers=[_wl(p) for p in losers],
        learnings=learnings,
        thesis_changes=thesis_changes,
        next_day_watchlist=next_day_watchlist,
        next_day_plan=plan,
    )


def _next_day_plan(review: TeamPortfolioReview) -> list[str]:
    counts = review.counts()
    plan: list[str] = []
    if review.health.block_new_buys:
        plan.append("New buys blocked: free capital via approved trims/exits before any entry.")
    if counts.get("exit"):
        plan.append(f"Exit candidates: {counts['exit']} position(s) flagged for sell-to-close.")
    if counts.get("trim"):
        plan.append(f"Trim candidates: {counts['trim']} overweight/strong position(s).")
    if counts.get("hold"):
        plan.append(f"Hold/observe {counts['hold']} position(s) with intact theses.")
    if not plan:
        plan.append("Hold and observe; no action clears the bar yet.")
    return plan


# --- rendering ----------------------------------------------------------------


def _money(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):+.2%}"
    except (TypeError, ValueError):
        return str(v)


def render_eod_discord(report: EodReport) -> str:
    """Concise Discord-sized summary. No secrets."""

    lines = [
        f"**EOD {report.team_id}** - {report.trading_date} (session: {report.session_status})",
        f"Equity {_money(report.ending_equity)} | Day P&L {_money(report.daily_pl)} "
        f"({_pct(report.daily_return_pct)}) | vs SPY {_pct(report.excess_vs_spy_pct)}",
        f"Cash {_money(report.cash)} | BP {_money(report.buying_power)} | "
        f"positions {report.open_position_count}",
    ]
    if report.submitted_orders:
        orders = ", ".join(
            f"{o.side.upper()} {o.quantity:g} {o.symbol} ({o.status})" for o in report.submitted_orders[:6]
        )
        lines.append(f"Orders: {orders}")
    else:
        lines.append("Orders: none (no-trade day).")
    if report.top_winners:
        lines.append("Winners: " + ", ".join(f"{w['symbol']} {_pct(w['return_pct'])}" for w in report.top_winners))
    if report.top_losers:
        lines.append("Losers: " + ", ".join(f"{l['symbol']} {_pct(l['return_pct'])}" for l in report.top_losers))
    if report.learnings:
        lines.append("Learned: " + report.learnings[0])
    if report.next_day_plan:
        lines.append("Tomorrow: " + report.next_day_plan[0])
    lines.append(f"_{report.disclaimer}_")
    return "\n".join(lines)


def render_eod_markdown(report: EodReport) -> str:
    out = [f"# EOD report - {report.team_id} - {report.trading_date}", ""]
    out.append(f"_Session: {report.session_status}. {report.disclaimer}_")
    out.append("")
    out.append("## Performance")
    out.append(f"- Starting equity: {_money(report.starting_equity)}")
    out.append(f"- Ending equity: {_money(report.ending_equity)}")
    out.append(f"- Daily P&L: {_money(report.daily_pl)} ({_pct(report.daily_return_pct)})")
    out.append(f"- SPY daily: {_pct(report.spy_daily_return_pct)} | Excess vs SPY: {_pct(report.excess_vs_spy_pct)}")
    out.append(f"- Cash {_money(report.cash)} | Buying power {_money(report.buying_power)} | "
               f"Gross {('n/a' if report.gross_exposure_pct is None else f'{report.gross_exposure_pct:.0%}')} | "
               f"Positions {report.open_position_count}")
    out.append("")
    out.append("## Submitted orders")
    if report.submitted_orders:
        for o in report.submitted_orders:
            out.append(f"- {o.side.upper()} {o.quantity:g} {o.symbol} @ {_money(o.price)} "
                       f"({_money(o.notional)}) - {o.status} - {o.reason}")
    else:
        out.append("- None (no-trade day).")
    out.append("")
    out.append("## Held / watched (reviewed, no order)")
    for p in report.held_positions:
        out.append(f"- {p['symbol']} [{p['action']}] {_pct(p.get('return_pct'))} - {p['reason']}")
    out.append("")
    if report.rejected_or_skipped:
        out.append("## Rejected / skipped")
        for r in report.rejected_or_skipped:
            out.append(f"- {r}")
        out.append("")
    out.append("## Winners / losers")
    out.append("- Winners: " + (", ".join(f"{w['symbol']} {_pct(w['return_pct'])}" for w in report.top_winners) or "n/a"))
    out.append("- Losers: " + (", ".join(f"{l['symbol']} {_pct(l['return_pct'])}" for l in report.top_losers) or "n/a"))
    out.append("")
    out.append("## Learnings")
    for l in (report.learnings or ["(none recorded)"]):
        out.append(f"- {l}")
    if report.thesis_changes:
        out.append("")
        out.append("## Thesis changes")
        for t in report.thesis_changes:
            out.append(f"- {t}")
    out.append("")
    out.append("## Next day")
    out.append("- Watchlist: " + (", ".join(report.next_day_watchlist) or "n/a"))
    for p in report.next_day_plan:
        out.append(f"- Plan: {p}")
    return "\n".join(out)


def save_eod_report(report: EodReport, *, eod_dir: Path | str = DEFAULT_EOD_DIR) -> dict[str, Path]:
    directory = Path(eod_dir)
    directory.mkdir(parents=True, exist_ok=True)
    md = directory / f"{report.team_id}_{report.trading_date}.md"
    js = directory / f"{report.team_id}_{report.trading_date}.json"
    md.write_text(render_eod_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return {"markdown": md, "json": js}


__all__ = [
    "DEFAULT_EOD_DIR", "PAPER_DISCLAIMER",
    "OrderLine", "EodReport",
    "should_send_eod", "already_sent", "mark_sent", "load_sent_state",
    "build_eod_report", "render_eod_discord", "render_eod_markdown", "save_eod_report",
]
