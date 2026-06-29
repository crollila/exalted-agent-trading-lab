"""Render + persist the read-only team portfolio review (Phase 7V).

Pure formatting + best-effort file persistence under the ignored runtime path.
No secrets are ever included (only symbols, prices, P&L, weights, reasons).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.competition.position_review import TeamPortfolioReview

DEFAULT_REVIEW_DIR = Path("data/runtime/portfolio_reviews")


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):+.2%}"
    except (TypeError, ValueError):
        return str(value)


def format_review_terminal(review: TeamPortfolioReview) -> str:
    h = review.health
    counts = review.counts()
    lines: list[str] = []
    lines.append(f"================ {review.team_id} portfolio review ================")
    lines.append(f"Generated: {review.generated_at}")
    lines.append(
        f"Equity {_money(review.equity)} | Cash {_money(review.cash)} | "
        f"Buying power {_money(review.buying_power)}"
    )
    gross = "n/a" if h.gross_exposure_pct is None else f"{h.gross_exposure_pct:.0%}"
    lines.append(
        f"Open positions: {h.open_position_count} | Gross exposure: {gross} | "
        f"Long MV {_money(h.long_market_value)}"
    )
    lines.append(
        f"Recommended actions -> hold={counts.get('hold',0)} trim={counts.get('trim',0)} "
        f"exit={counts.get('exit',0)} watch={counts.get('watch',0)}"
    )
    lines.append("")

    if h.critical_problems:
        lines.append("CRITICAL PORTFOLIO PROBLEMS:")
        for p in h.critical_problems:
            lines.append(f"  ! {p}")
    else:
        lines.append("Critical portfolio problems: none detected.")
    lines.append(f"New buys: {'BLOCKED' if h.block_new_buys else 'PERMITTED'}  -  {h.block_new_buys_reason}")
    lines.append("")

    if not review.positions:
        lines.append("No open positions.")
    for p in review.positions:
        lines.append(f"[{p.recommended_action.upper()}] {p.symbol}  ({p.side})")
        lines.append(
            f"    qty={p.quantity:g} avg_entry={_money(p.avg_entry_price)} "
            f"current={_money(p.current_price)} mkt_val={_money(p.market_value)}"
        )
        lines.append(
            f"    uP&L={_money(p.unrealized_pl)} ({_pct(p.unrealized_pl_pct)}) "
            f"weight={_pct(p.portfolio_weight)} days_held={p.days_held if p.days_held is not None else 'n/a'}"
        )
        lines.append(
            f"    thesis_status={p.thesis_status} conviction={p.conviction_score} "
            f"target={_money(p.target_price)} stop={_money(p.downside_stop)}"
        )
        thesis = p.original_thesis or "(no local thesis on file)"
        lines.append(f"    thesis: {thesis}")
        lines.append(f"    reason: {p.reason}")
    lines.append("")
    for note in review.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def render_review_markdown(review: TeamPortfolioReview) -> str:
    h = review.health
    counts = review.counts()
    out: list[str] = []
    out.append(f"# Portfolio review  -  {review.team_id}")
    out.append("")
    out.append(f"_Generated: {review.generated_at}  -  paper-only, read-only._")
    out.append("")
    out.append(
        f"- Equity: {_money(review.equity)} | Cash: {_money(review.cash)} | "
        f"Buying power: {_money(review.buying_power)}"
    )
    gross = "n/a" if h.gross_exposure_pct is None else f"{h.gross_exposure_pct:.0%}"
    out.append(f"- Open positions: {h.open_position_count} | Gross exposure: {gross}")
    out.append(
        f"- Actions: hold={counts.get('hold',0)}, trim={counts.get('trim',0)}, "
        f"exit={counts.get('exit',0)}, watch={counts.get('watch',0)}"
    )
    out.append(f"- New buys: **{'BLOCKED' if h.block_new_buys else 'PERMITTED'}**  -  {h.block_new_buys_reason}")
    out.append("")
    if h.critical_problems:
        out.append("## Critical problems")
        for p in h.critical_problems:
            out.append(f"- {p}")
        out.append("")
    out.append("## Positions (worst-first)")
    out.append("")
    out.append("| Action | Symbol | Side | Qty | Avg | Current | uP&L | Return | Weight | Thesis | Reason |")
    out.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    for p in review.positions:
        out.append(
            f"| {p.recommended_action} | {p.symbol} | {p.side} | {p.quantity:g} | "
            f"{_money(p.avg_entry_price)} | {_money(p.current_price)} | {_money(p.unrealized_pl)} | "
            f"{_pct(p.unrealized_pl_pct)} | {_pct(p.portfolio_weight)} | {p.thesis_status} | {p.reason} |"
        )
    out.append("")
    for note in review.notes:
        out.append(f"> {note}")
    return "\n".join(out)


def save_review(
    review: TeamPortfolioReview,
    *,
    review_dir: Path | str = DEFAULT_REVIEW_DIR,
) -> dict[str, Path]:
    """Persist Markdown + JSON under the ignored runtime path. Best-effort."""

    directory = Path(review_dir)
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / f"{review.team_id}_latest.md"
    json_path = directory / f"{review.team_id}_latest.json"
    md_path.write_text(render_review_markdown(review), encoding="utf-8")
    json_path.write_text(json.dumps(review.as_dict(), indent=2), encoding="utf-8")
    return {"markdown": md_path, "json": json_path}


__all__ = [
    "DEFAULT_REVIEW_DIR",
    "format_review_terminal",
    "render_review_markdown",
    "save_review",
]
