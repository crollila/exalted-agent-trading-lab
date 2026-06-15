"""Daily SPY attribution + strategy debate / self-review (Phase 7N).

Two cheap, local, deterministic artifacts built from data the system already
persists (scorecards + refreshed attribution + team memory):

* ``DailySpyAttribution`` — why a team beat or lost to SPY (returns, excess,
  long/short contribution estimates, top winners/losers, submitted/rejected,
  no-trade cycles, best-effort sector buckets, and a concise driver explanation).
* ``DailyTeamReview`` — a compact strategy-debate artifact answering the standard
  self-review questions. Persisted under the ignored runtime path ``data/reviews/``.

No network, no broker, no LLM, no secrets. Everything degrades safely on missing
data (returns ``unknown``/empty rather than raising).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.attribution import (
    DEFAULT_ATTRIBUTION_DIR,
    ProposalAttribution,
    load_team_attribution,
    performance_feedback,
)
from src.competition.scorecard import (
    DEFAULT_SCORECARD_DIR,
    TeamScorecard,
    load_latest_scorecard,
    load_scorecard_history,
)
from src.learning.team_memory import DEFAULT_LEARNING_DIR, TeamLearningLedger

DEFAULT_REVIEWS_DIR = Path("data/reviews")

# Best-effort symbol buckets used when broker sector data is unavailable.
SYMBOL_BUCKETS: dict[str, set[str]] = {
    "index_etf": {"SPY", "QQQ"},
    "semis_ai": {"NVDA", "AMD"},
    "megacap_software_cloud": {"MSFT", "GOOGL", "META", "AMZN"},
    "high_beta_auto_ev": {"TSLA"},
}


def bucket_for(symbol: str) -> str:
    key = (symbol or "").upper()
    for bucket, members in SYMBOL_BUCKETS.items():
        if key in members:
            return bucket
    return "unknown"


def _metric(entry: ProposalAttribution) -> float | None:
    """Best available signed performance metric for a scored entry."""

    if entry.excess_return_pct is not None:
        return entry.excess_return_pct
    return entry.return_pct


@dataclass
class DailySpyAttribution:
    team_id: str
    team_return: float | None = None
    spy_return: float | None = None
    excess_return: float | None = None
    beginning_equity: float | None = None
    ending_equity: float | None = None
    long_contribution_est: float | None = None
    short_contribution_est: float | None = None
    top_winners: list[dict[str, Any]] = field(default_factory=list)
    top_losers: list[dict[str, Any]] = field(default_factory=list)
    submitted_orders: int = 0
    broker_rejections: int = 0
    broker_rejection_categories: list[str] = field(default_factory=list)
    no_trade_cycles: int = 0
    sector_exposure: dict[str, int] = field(default_factory=dict)
    drivers: list[str] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_daily_spy_attribution(
    team_id: str,
    *,
    scorecard: TeamScorecard | None,
    entries: list[ProposalAttribution],
    no_trade_cycles: int = 0,
) -> DailySpyAttribution:
    """Compute a team's SPY attribution from local scorecard + attribution data."""

    result = DailySpyAttribution(team_id=team_id)
    if scorecard is not None:
        result.team_return = scorecard.team_return
        result.spy_return = scorecard.spy_benchmark_return
        result.excess_return = scorecard.excess_return_vs_spy
        result.beginning_equity = scorecard.starting_equity
        result.ending_equity = scorecard.current_equity
    result.no_trade_cycles = no_trade_cycles

    scored = [e for e in entries if _metric(e) is not None]
    longs = [e for e in scored if "short" not in e.asset_type and not e.asset_type.startswith("option")]
    shorts = [e for e in scored if "short" in e.asset_type]

    def _mean(values: list[float]) -> float | None:
        return (sum(values) / len(values)) if values else None

    result.long_contribution_est = _mean([e.return_pct for e in longs if e.return_pct is not None])
    result.short_contribution_est = _mean([e.return_pct for e in shorts if e.return_pct is not None])

    # Best metric per distinct symbol -> winners / losers.
    by_symbol: dict[str, float] = {}
    for e in scored:
        m = _metric(e)
        if m is None:
            continue
        # Keep the strongest-magnitude reading per symbol.
        if e.symbol not in by_symbol or abs(m) > abs(by_symbol[e.symbol]):
            by_symbol[e.symbol] = m
    ranked = sorted(by_symbol.items(), key=lambda kv: kv[1], reverse=True)
    result.top_winners = [
        {"symbol": s, "metric": round(v, 6), "bucket": bucket_for(s)} for s, v in ranked if v > 0
    ][:5]
    result.top_losers = [
        {"symbol": s, "metric": round(v, 6), "bucket": bucket_for(s)} for s, v in reversed(ranked) if v < 0
    ][:5]

    result.submitted_orders = sum(1 for e in entries if e.broker_submitted)
    rejected = [e for e in entries if e.broker_rejected]
    result.broker_rejections = len(rejected)
    result.broker_rejection_categories = sorted({e.failure_category or "unknown" for e in rejected})

    # Best-effort sector buckets from distinct symbols seen this period.
    buckets: dict[str, int] = {}
    for symbol in {e.symbol for e in entries}:
        buckets[bucket_for(symbol)] = buckets.get(bucket_for(symbol), 0) + 1
    result.sector_exposure = buckets

    result.drivers, result.explanation = _explain(result, longs, shorts)
    return result


def _explain(
    a: DailySpyAttribution,
    longs: list[ProposalAttribution],
    shorts: list[ProposalAttribution],
) -> tuple[list[str], str]:
    drivers: list[str] = []
    if a.excess_return is not None and a.excess_return > 0:
        return ["outperformed"], (
            f"Beat SPY by {a.excess_return:+.4f} excess. "
            f"Long contribution est {a.long_contribution_est}, short {a.short_contribution_est}."
        )

    # Underperformance (or unknown excess): attribute to likely drivers.
    if a.broker_rejections > 0:
        drivers.append("broker_rejections")
    if a.short_contribution_est is not None and a.short_contribution_est < 0:
        drivers.append("short_exposure")
    if any("margin" in e.asset_type for e in (longs + shorts)) and (a.excess_return or 0) < 0:
        drivers.append("leverage_margin")
    if a.submitted_orders == 0 and a.no_trade_cycles > 0:
        drivers.append("too_much_cash_no_trade")
    if a.top_losers and (not a.top_winners or abs(a.top_losers[0]["metric"]) > a.top_winners[0]["metric"]):
        drivers.append("stock_selection")
    # Dominant losing bucket (sector exposure).
    losing_buckets: dict[str, int] = {}
    for loser in a.top_losers:
        losing_buckets[loser["bucket"]] = losing_buckets.get(loser["bucket"], 0) + 1
    if losing_buckets:
        worst_bucket = max(losing_buckets, key=losing_buckets.get)
        if worst_bucket != "unknown" and losing_buckets[worst_bucket] >= 2:
            drivers.append(f"sector_exposure:{worst_bucket}")
    if (
        a.team_return is not None
        and a.spy_return is not None
        and a.team_return < a.spy_return
        and a.submitted_orders == 0
    ):
        drivers.append("missed_beta")
    if not drivers:
        drivers.append("bad_timing")

    excess_text = "unknown" if a.excess_return is None else f"{a.excess_return:+.4f}"
    explanation = (
        f"Excess vs SPY {excess_text}. Likely drivers: {', '.join(drivers)}. "
        f"Submitted {a.submitted_orders}, broker rejections {a.broker_rejections}, "
        f"no-trade cycles {a.no_trade_cycles}."
    )
    return drivers, explanation


def load_daily_spy_attribution(
    team_id: str,
    *,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
) -> DailySpyAttribution:
    """Convenience loader that reads local scorecard + attribution + history."""

    scorecard = load_latest_scorecard(team_id, scorecard_dir)
    entries = load_team_attribution(team_id, attribution_dir=attribution_dir)
    history = load_scorecard_history(team_id, scorecard_dir)
    no_trade = sum(1 for c in history if getattr(c, "portfolio_no_trade", False))
    return compute_daily_spy_attribution(
        team_id, scorecard=scorecard, entries=entries, no_trade_cycles=no_trade
    )


def format_daily_spy_attribution(a: DailySpyAttribution) -> str:
    def fmt(v: float | None) -> str:
        return "unknown" if v is None else f"{v:.4f}"

    lines = [f"=== Daily SPY attribution: {a.team_id} (paper-only) ==="]
    lines.append(f"Team return: {fmt(a.team_return)} | SPY return: {fmt(a.spy_return)} | Excess: {fmt(a.excess_return)}")
    lines.append(f"Beginning equity: {fmt(a.beginning_equity)} | Ending equity: {fmt(a.ending_equity)}")
    lines.append(
        f"Long contribution est: {fmt(a.long_contribution_est)} | "
        f"Short contribution est: {fmt(a.short_contribution_est)}"
    )
    lines.append(f"Submitted orders: {a.submitted_orders} | Broker rejections: {a.broker_rejections} "
                 f"{a.broker_rejection_categories or ''}")
    lines.append(f"No-trade/hold cycles: {a.no_trade_cycles}")
    lines.append(f"Top winners: {a.top_winners or '(none)'}")
    lines.append(f"Top losers: {a.top_losers or '(none)'}")
    lines.append(f"Sector buckets: {a.sector_exposure or '(none)'}")
    lines.append(f"Drivers: {', '.join(a.drivers) or '(none)'}")
    lines.append(f"Explanation: {a.explanation}")
    return "\n".join(lines)


# --- Strategy debate / daily self-review artifact ---------------------------


@dataclass
class DailyTeamReview:
    team_id: str
    date: str
    spy_relative_result: str = ""
    why_vs_spy: str = ""
    helped: list[str] = field(default_factory=list)
    hurt: list[str] = field(default_factory=list)
    shorts_assessment: str = ""
    broker_rejection_drag: str = ""
    prior_thesis_outcome: str = ""
    stop_doing: list[str] = field(default_factory=list)
    keep_doing: list[str] = field(default_factory=list)
    test_next: list[str] = field(default_factory=list)
    recommended_mode: str = ""  # exploration | conservation
    watch_symbols: list[str] = field(default_factory=list)
    reduce_churn: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_daily_team_review(
    team_id: str,
    *,
    attribution: DailySpyAttribution,
    feedback: dict[str, Any] | None = None,
    ledger: TeamLearningLedger | None = None,
) -> DailyTeamReview:
    """Build the compact strategy-debate artifact from local signals only."""

    feedback = feedback or {}
    outcome = feedback.get("outcome_feedback", {}) or {}
    is_alpha = team_id == "team_alpha"

    excess = attribution.excess_return
    if excess is None:
        spy_relative = "unknown"
        beat = None
    else:
        beat = excess > 0
        spy_relative = f"{'beat' if beat else 'trailed'} SPY by {excess:+.4f} excess"

    helped = [f"{w['symbol']} ({w['bucket']})" for w in attribution.top_winners]
    hurt = [f"{l['symbol']} ({l['bucket']})" for l in attribution.top_losers]

    short_c = attribution.short_contribution_est
    if short_c is None:
        shorts_assessment = "no short exposure this period"
    elif short_c >= 0:
        shorts_assessment = f"shorts helped (avg {short_c:+.4f})"
    else:
        shorts_assessment = f"shorts hurt (avg {short_c:+.4f})"

    if attribution.broker_rejections > 0:
        broker_drag = (
            f"yes — {attribution.broker_rejections} rejection(s) "
            f"({', '.join(attribution.broker_rejection_categories)})"
        )
    else:
        broker_drag = "no broker rejections"

    worked = int(outcome.get("worked_count", 0) or 0)
    failed = int(outcome.get("failed_count", 0) or 0)
    if worked == 0 and failed == 0:
        prior_thesis = "unproven (no scored outcomes yet)"
    elif worked > failed:
        prior_thesis = f"largely worked ({worked} worked vs {failed} failed)"
    elif failed > worked:
        prior_thesis = f"largely failed ({failed} failed vs {worked} worked)"
    else:
        prior_thesis = f"mixed ({worked} worked, {failed} failed)"

    stop_doing: list[str] = []
    if "short_exposure" in attribution.drivers:
        stop_doing.append("adding short exposure that lags SPY")
    if "broker_rejections" in attribution.drivers:
        stop_doing.append("submitting orders that exceed buying power")
    for driver in attribution.drivers:
        if driver.startswith("sector_exposure:"):
            stop_doing.append(f"overconcentrating in {driver.split(':', 1)[1]}")
    if "too_much_cash_no_trade" in attribution.drivers:
        stop_doing.append("sitting in cash when ideas clear the bar")

    keep_doing = [f"holding/adding {w['symbol']}" for w in attribution.top_winners[:2]]
    if not keep_doing:
        keep_doing.append("disciplined no-trade when nothing beats the book")

    test_next: list[str] = []
    if attribution.top_winners:
        test_next.append(f"size up the {attribution.top_winners[0]['bucket']} winners")
    if beat is False:
        test_next.append("tighten stops / rotate the weakest holding")

    # Mode recommendation: conservation when behind or churn is hurting; else personality default.
    reduce_churn = bool(
        attribution.broker_rejections > 0 or (beat is False) or "too_much_cash_no_trade" in attribution.drivers
    )
    if beat is False or attribution.broker_rejections > 0:
        recommended_mode = "conservation"
    else:
        recommended_mode = "exploration" if is_alpha else "conservation"

    watch = [w["symbol"] for w in attribution.top_winners] + [l["symbol"] for l in attribution.top_losers]
    watch_symbols = sorted(dict.fromkeys(watch))[:8]

    return DailyTeamReview(
        team_id=team_id,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        spy_relative_result=spy_relative,
        why_vs_spy=attribution.explanation,
        helped=helped,
        hurt=hurt,
        shorts_assessment=shorts_assessment,
        broker_rejection_drag=broker_drag,
        prior_thesis_outcome=prior_thesis,
        stop_doing=stop_doing or ["nothing critical flagged"],
        keep_doing=keep_doing,
        test_next=test_next or ["hold and observe"],
        recommended_mode=recommended_mode,
        watch_symbols=watch_symbols,
        reduce_churn=reduce_churn,
    )


def _review_paths(team_id: str, reviews_dir: Path | str, date: str) -> tuple[Path, Path]:
    directory = Path(reviews_dir)
    return directory / f"{team_id}_{date}.json", directory / f"{team_id}_latest.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_daily_team_review(
    review: DailyTeamReview,
    *,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
) -> Path:
    dated, latest = _review_paths(review.team_id, reviews_dir, review.date)
    payload = json.dumps(review.as_dict(), indent=2, default=str)
    _atomic_write(dated, payload)
    _atomic_write(latest, payload)
    return latest


def load_latest_daily_team_review(
    team_id: str,
    *,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
) -> DailyTeamReview | None:
    _, latest = _review_paths(team_id, reviews_dir, "ignored")
    if not latest.exists():
        return None
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in {f.name for f in fields(DailyTeamReview)}}
        return DailyTeamReview(**known)
    except (ValueError, TypeError, OSError):
        return None


def export_daily_team_review(
    team_id: str,
    *,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
) -> DailyTeamReview:
    """Build + persist a team's daily review from local data (no network/LLM)."""

    attribution = load_daily_spy_attribution(
        team_id, scorecard_dir=scorecard_dir, attribution_dir=attribution_dir
    )
    try:
        feedback = performance_feedback(team_id, attribution_dir=attribution_dir)
    except Exception:  # noqa: BLE001 - never crash on missing/old attribution
        feedback = {}
    ledger = TeamLearningLedger.load(team_id, learning_dir)
    review = build_daily_team_review(
        team_id, attribution=attribution, feedback=feedback, ledger=ledger
    )
    save_daily_team_review(review, reviews_dir=reviews_dir)
    return review


def format_daily_team_review(r: DailyTeamReview) -> str:
    lines = [f"=== Daily team review: {r.team_id} ({r.date}) ==="]
    lines.append(f"SPY-relative: {r.spy_relative_result}")
    lines.append(f"Why vs SPY: {r.why_vs_spy}")
    lines.append(f"Helped: {', '.join(r.helped) or '(none)'}")
    lines.append(f"Hurt: {', '.join(r.hurt) or '(none)'}")
    lines.append(f"Shorts: {r.shorts_assessment}")
    lines.append(f"Broker rejection drag: {r.broker_rejection_drag}")
    lines.append(f"Prior thesis: {r.prior_thesis_outcome}")
    lines.append(f"Stop doing: {', '.join(r.stop_doing)}")
    lines.append(f"Keep doing: {', '.join(r.keep_doing)}")
    lines.append(f"Test next: {', '.join(r.test_next)}")
    lines.append(f"Recommended mode: {r.recommended_mode} (reduce_churn={r.reduce_churn})")
    lines.append(f"Watch: {', '.join(r.watch_symbols) or '(none)'}")
    return "\n".join(lines)


def daily_review_context(
    team_id: str,
    *,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
) -> dict[str, Any]:
    """Compact previous-review context for the next LLM cycle (cost-controlled).

    Research feedback only — never authorizes bypassing risk, credentials, or the
    kill switch. Returns a small dict even when no review exists yet.
    """

    review = load_latest_daily_team_review(team_id, reviews_dir=reviews_dir)
    ledger = TeamLearningLedger.load(team_id, learning_dir)
    if review is None:
        return {
            "available": False,
            "note": "No prior daily review. Research feedback only; never bypass risk.",
            "mode": ledger.mode or "",
            "avoid_next_cycle": ledger.avoid_next_cycle[-5:],
        }
    return {
        "available": True,
        "note": "Prior daily review (research feedback only; never bypass risk/credentials/kill switch).",
        "spy_relative_result": review.spy_relative_result,
        "what_worked": review.keep_doing[:3],
        "what_failed": review.stop_doing[:3],
        "avoid_next_cycle": ledger.avoid_next_cycle[-5:],
        "mode": review.recommended_mode or ledger.mode or "",
        "watch_symbols": review.watch_symbols[:8],
        "reduce_churn": review.reduce_churn,
    }
