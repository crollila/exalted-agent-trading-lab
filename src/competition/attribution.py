"""Proposal/trade attribution + effectiveness tracking (Task 7).

Records one attribution entry per proposal in a cycle and computes outcome metrics
(return %, excess vs SPY, thesis outcome) when prices are available. Stored under
the ignored runtime path ``data/attribution/``. No secrets.

The refresh system (``refresh-proposal-attribution``) updates pending outcomes
with the latest market prices + a SPY benchmark so Alpha and Beta can learn from
actual paper-trading outcomes. Refresh is read-only with respect to the broker:
it fetches prices through the existing safe market-data wrapper and only rewrites
local attribution JSONL files. It never submits orders and never prints secrets.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ATTRIBUTION_DIR = Path("data/attribution")

# Small default excess-return threshold (0.5%) for the worked/failed verdict.
# Configurable via env ATTRIBUTION_OUTCOME_THRESHOLD or the CLI --threshold flag.
DEFAULT_OUTCOME_THRESHOLD = 0.005


def default_outcome_threshold() -> float:
    raw = os.getenv("ATTRIBUTION_OUTCOME_THRESHOLD")
    if not raw:
        return DEFAULT_OUTCOME_THRESHOLD
    try:
        value = abs(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_OUTCOME_THRESHOLD
    return value or DEFAULT_OUTCOME_THRESHOLD


@dataclass
class ProposalAttribution:
    proposal_id: str
    team_id: str
    strategy_id: str
    asset_type: str
    symbol: str
    thesis: str
    cycle_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data_sources_used: list[str] = field(default_factory=list)
    research_source_ids: list[str] = field(default_factory=list)
    routing: str = "rejected"  # execution_eligible | simulation_only | rejected
    broker_submitted: bool = False
    broker_rejected: bool = False
    broker_reject_reason: str | None = None
    broker_reject_code: str | None = None
    failure_category: str | None = None  # insufficient_buying_power | wash_trade | broker_error | unknown
    order_id: str | None = None
    action: str | None = None
    quantity: float | None = None
    entry_price: float | None = None
    current_price: float | None = None
    position_status: str = "none"  # none | open | closed
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    return_pct: float | None = None
    spy_return: float | None = None
    excess_return_vs_spy: float | None = None
    holding_period: str | None = None
    thesis_outcome: str = "pending"  # pending | worked | failed | mixed
    lesson_learned: str | None = None
    next_adjustment: str | None = None
    # --- Refresh fields (Task: outcome refresh). Backward compatible (defaults). ---
    spy_start_price: float | None = None
    spy_current_price: float | None = None
    spy_return_pct: float | None = None
    excess_return_pct: float | None = None
    outcome_status: str = "pending"  # pending | worked | failed | mixed
    refreshed_at: str | None = None
    refresh_skip_reason: str | None = None

    def compute_outcome(self) -> None:
        """Record-time outcome from the prices stored on the proposal.

        This runs when an attribution row is first written (entry vs the price
        snapshot at proposal time). The richer ``refresh_outcome`` updates it
        later with live prices + a SPY benchmark.
        """

        if self.entry_price and self.current_price and self.entry_price > 0:
            ret = (self.current_price - self.entry_price) / self.entry_price
            # Short exposure profits when price falls.
            if "short" in self.asset_type:
                ret = -ret
            self.return_pct = ret
            if self.spy_return is not None:
                self.excess_return_vs_spy = ret - self.spy_return
            if ret > 0.001:
                self.thesis_outcome = "worked"
            elif ret < -0.001:
                self.thesis_outcome = "failed"
            else:
                self.thesis_outcome = "mixed"
        else:
            self.return_pct = None
            self.excess_return_vs_spy = None
            self.thesis_outcome = "pending"
        self.outcome_status = self.thesis_outcome

    def is_option(self) -> bool:
        return self.asset_type.startswith("option")

    def refresh_outcome(
        self,
        *,
        current_price: float | None,
        spy_start_price: float | None,
        spy_current_price: float | None,
        threshold: float = DEFAULT_OUTCOME_THRESHOLD,
        now: datetime | None = None,
    ) -> str | None:
        """Update the proposal's outcome with live prices + a SPY benchmark.

        Returns ``None`` when the row was scored, or a short skip reason string
        when it had to stay ``pending`` (missing entry/current price, SPY
        benchmark unavailable, options not supported, etc.). Never raises.
        """

        now = now or datetime.now(timezone.utc)
        self.refreshed_at = now.isoformat()
        self.refresh_skip_reason = None

        # Options refresh would need option-chain pricing; underlying-only moves
        # would be misleading. Leave pending with a clear, honest reason.
        if self.is_option():
            return self._skip("options outcome refresh not supported (single-leg underlying only)")

        if not self.entry_price or self.entry_price <= 0:
            return self._skip("missing entry/decision price")

        if current_price is None:
            return self._skip(f"missing current price for {self.symbol}")

        self.current_price = float(current_price)
        ret = (self.current_price - self.entry_price) / self.entry_price
        if "short" in self.asset_type:
            ret = -ret
        self.return_pct = ret

        if self.quantity is not None:
            # Sign already folded into ret for shorts; recover a per-share delta.
            per_share = self.current_price - self.entry_price
            if "short" in self.asset_type:
                per_share = -per_share
            self.unrealized_pnl = per_share * float(self.quantity)

        spy_ret = _spy_return(spy_start_price, spy_current_price)
        self.spy_start_price = spy_start_price
        self.spy_current_price = spy_current_price
        self.spy_return_pct = spy_ret
        # Keep the legacy field populated for backward-compatible readers.
        if spy_ret is not None:
            self.spy_return = spy_ret

        if spy_ret is None:
            return self._skip("SPY benchmark unavailable for the holding period")

        excess = ret - spy_ret
        self.excess_return_pct = excess
        self.excess_return_vs_spy = excess

        if excess >= threshold:
            verdict = "worked"
        elif excess <= -threshold:
            verdict = "failed"
        else:
            # Near the flat band: inconclusive but data-backed -> mixed.
            verdict = "mixed"
        self.outcome_status = verdict
        self.thesis_outcome = verdict
        return None

    def _skip(self, reason: str) -> str:
        self.refresh_skip_reason = reason
        self.outcome_status = "pending"
        self.thesis_outcome = "pending"
        return reason

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELD_NAMES = {f.name for f in fields(ProposalAttribution)}


def _from_dict(data: dict[str, Any]) -> ProposalAttribution:
    """Build an attribution row, tolerating old rows (missing new keys) and
    unexpected/extra keys (ignored). Keeps the JSONL format forward/backward safe."""

    filtered = {k: v for k, v in data.items() if k in _FIELD_NAMES}
    entry = ProposalAttribution(**filtered)
    # Old rows predate ``outcome_status``; mirror the legacy thesis_outcome.
    if "outcome_status" not in data:
        entry.outcome_status = entry.thesis_outcome
    return entry


def _team_path(team_id: str, attribution_dir: Path | str) -> Path:
    return Path(attribution_dir) / f"{team_id}_attribution.jsonl"


def _spy_return(starting_price: float | None, current_price: float | None) -> float | None:
    if not starting_price or not current_price or starting_price <= 0:
        return None
    return (current_price - starting_price) / starting_price


def record_attributions(
    entries: list[ProposalAttribution],
    *,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
) -> Path | None:
    if not entries:
        return None
    directory = Path(attribution_dir)
    directory.mkdir(parents=True, exist_ok=True)
    team_id = entries[0].team_id
    path = _team_path(team_id, attribution_dir)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            entry.compute_outcome()
            handle.write(json.dumps(entry.as_dict(), default=str) + "\n")
    return path


def load_team_attribution(
    team_id: str,
    *,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
) -> list[ProposalAttribution]:
    path = _team_path(team_id, attribution_dir)
    if not path.exists():
        return []
    out: list[ProposalAttribution] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(_from_dict(json.loads(line)))
    return out


def _write_team_attribution(
    team_id: str,
    entries: list[ProposalAttribution],
    *,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
) -> Path:
    """Atomically rewrite a team's attribution JSONL (temp file + os.replace)."""

    directory = Path(attribution_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = _team_path(team_id, attribution_dir)
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f"{team_id}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry.as_dict(), default=str) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


@dataclass
class RefreshSkip:
    proposal_id: str
    symbol: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RefreshSummary:
    team_id: str
    scanned: int = 0
    refreshed: int = 0
    pending: int = 0
    worked: int = 0
    failed: int = 0
    mixed: int = 0
    best: ProposalAttribution | None = None
    worst: ProposalAttribution | None = None
    skipped: list[RefreshSkip] = field(default_factory=list)
    spy_start_price: float | None = None
    spy_current_price: float | None = None
    spy_return_pct: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "scanned": self.scanned,
            "refreshed": self.refreshed,
            "pending": self.pending,
            "worked": self.worked,
            "failed": self.failed,
            "mixed": self.mixed,
            "best": self.best.as_dict() if self.best else None,
            "worst": self.worst.as_dict() if self.worst else None,
            "skipped": [s.as_dict() for s in self.skipped],
            "spy_start_price": self.spy_start_price,
            "spy_current_price": self.spy_current_price,
            "spy_return_pct": self.spy_return_pct,
        }


def refresh_team_attribution(
    team_id: str,
    *,
    price_fn: Any | None,
    spy_start_price: float | None = None,
    spy_current_price: float | None = None,
    threshold: float = DEFAULT_OUTCOME_THRESHOLD,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    now: datetime | None = None,
) -> RefreshSummary:
    """Refresh a team's attribution outcomes with live prices + a SPY benchmark.

    ``price_fn`` is a ``symbol -> float`` callable (the safe Alpaca market-data
    wrapper). Any per-symbol fetch failure degrades that row to pending with a
    skip reason; it never raises and never invents a price. Writes back
    atomically. Returns a summary even when nothing could be scored.
    """

    entries = load_team_attribution(team_id, attribution_dir=attribution_dir)
    summary = RefreshSummary(
        team_id=team_id,
        spy_start_price=spy_start_price,
        spy_current_price=spy_current_price,
        spy_return_pct=_spy_return(spy_start_price, spy_current_price),
    )
    if not entries:
        return summary

    price_cache: dict[str, float | None] = {}

    def _price(symbol: str) -> float | None:
        key = symbol.upper()
        if key in price_cache:
            return price_cache[key]
        value: float | None = None
        if price_fn is not None:
            try:
                value = float(price_fn(key))
            except Exception:  # noqa: BLE001 - degrade to unknown; never invent a price
                value = None
        price_cache[key] = value
        return value

    for entry in entries:
        summary.scanned += 1
        current = None if entry.is_option() else _price(entry.symbol)
        skip = entry.refresh_outcome(
            current_price=current,
            spy_start_price=spy_start_price,
            spy_current_price=spy_current_price,
            threshold=threshold,
            now=now,
        )
        if skip is not None:
            summary.skipped.append(RefreshSkip(entry.proposal_id, entry.symbol, skip))
        else:
            summary.refreshed += 1

    _write_team_attribution(team_id, entries, attribution_dir=attribution_dir)

    scored = [e for e in entries if e.excess_return_pct is not None]
    for entry in entries:
        if entry.outcome_status == "worked":
            summary.worked += 1
        elif entry.outcome_status == "failed":
            summary.failed += 1
        elif entry.outcome_status == "mixed":
            summary.mixed += 1
        else:
            summary.pending += 1
    if scored:
        summary.best = max(scored, key=lambda e: e.excess_return_pct)
        summary.worst = min(scored, key=lambda e: e.excess_return_pct)
    return summary


def performance_feedback(
    team_id: str,
    *,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    recent: int = 20,
) -> dict[str, Any]:
    """Summarize recent winners/losers/best/worst for the next prompt (Task 8).

    Also exposes a compact ``outcome_feedback`` block built from refreshed
    outcomes (worked/failed counts, top worked/failed proposals with excess vs
    SPY, common winning/losing research themes, SPY-relative performance). This
    is research feedback only — it never authorizes bypassing risk controls.
    Missing/old/empty attribution never raises.
    """

    entries = load_team_attribution(team_id, attribution_dir=attribution_dir)[-recent:]
    scored = [e for e in entries if e.return_pct is not None]
    winners = sorted((e for e in scored if e.return_pct > 0), key=lambda e: e.return_pct, reverse=True)
    losers = sorted((e for e in scored if e.return_pct < 0), key=lambda e: e.return_pct)

    by_symbol: dict[str, list[float]] = {}
    by_strategy: dict[str, list[float]] = {}
    for entry in scored:
        by_symbol.setdefault(entry.symbol, []).append(entry.return_pct)
        by_strategy.setdefault(entry.asset_type, []).append(entry.return_pct)

    def best_worst(mapping: dict[str, list[float]]) -> tuple[str | None, str | None]:
        if not mapping:
            return None, None
        avg = {k: sum(v) / len(v) for k, v in mapping.items()}
        best = max(avg, key=avg.get)
        worst = min(avg, key=avg.get)
        return best, worst

    best_symbol, worst_symbol = best_worst(by_symbol)
    best_strategy, worst_strategy = best_worst(by_strategy)

    return {
        "recent_winners": [
            {"symbol": e.symbol, "asset_type": e.asset_type, "return_pct": e.return_pct} for e in winners[:5]
        ],
        "recent_losers": [
            {"symbol": e.symbol, "asset_type": e.asset_type, "return_pct": e.return_pct} for e in losers[:5]
        ],
        "rejected_recent": [
            {"symbol": e.symbol, "asset_type": e.asset_type}
            for e in entries
            if e.routing == "rejected"
        ][:5],
        "broker_errors_recent": [
            {
                "symbol": e.symbol,
                "lesson": e.lesson_learned,
                "failure_category": e.failure_category,
                "broker_reject_reason": e.broker_reject_reason,
            }
            for e in entries
            if e.broker_rejected or (e.routing == "execution_eligible" and not e.broker_submitted)
        ][:5],
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "best_strategy": best_strategy,
        "worst_strategy": worst_strategy,
        "pending_count": sum(1 for e in entries if e.outcome_status == "pending"),
        "outcome_feedback": _outcome_feedback(entries),
    }


def _outcome_feedback(entries: list[ProposalAttribution]) -> dict[str, Any]:
    """Compact, prompt-friendly outcome feedback from refreshed rows.

    Kept small on purpose so it does not bloat the LLM prompt. Outcomes are
    research feedback ONLY: the deterministic risk engine, team credentials, and
    kill switch still gate every trade regardless of what this reports.
    """

    refreshed = [e for e in entries if e.refreshed_at is not None]
    worked = sorted(
        (e for e in refreshed if e.outcome_status == "worked" and e.excess_return_pct is not None),
        key=lambda e: e.excess_return_pct,
        reverse=True,
    )
    failed = sorted(
        (e for e in refreshed if e.outcome_status == "failed" and e.excess_return_pct is not None),
        key=lambda e: e.excess_return_pct,
    )

    def themes(group: list[ProposalAttribution]) -> list[str]:
        counts: dict[str, int] = {}
        for e in group:
            for sid in e.research_source_ids:
                counts[sid] = counts.get(sid, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [sid for sid, _ in ranked[:5]]

    excesses = [e.excess_return_pct for e in refreshed if e.excess_return_pct is not None]
    avg_excess = (sum(excesses) / len(excesses)) if excesses else None

    rejections = [
        {
            "symbol": e.symbol,
            "failure_category": e.failure_category or "unknown",
            "reason": e.broker_reject_reason,
        }
        for e in entries
        if e.broker_rejected
    ]

    return {
        "note": "Research feedback only. Does NOT authorize bypassing risk, credentials, or the kill switch.",
        "refreshed_count": len(refreshed),
        "recent_broker_rejections": rejections[-5:],
        "worked_count": sum(1 for e in refreshed if e.outcome_status == "worked"),
        "failed_count": sum(1 for e in refreshed if e.outcome_status == "failed"),
        "mixed_count": sum(1 for e in refreshed if e.outcome_status == "mixed"),
        "pending_count": sum(1 for e in refreshed if e.outcome_status == "pending"),
        "best_recent_worked": [
            {"symbol": e.symbol, "asset_type": e.asset_type, "excess_return_pct": e.excess_return_pct}
            for e in worked[:3]
        ],
        "worst_recent_failed": [
            {"symbol": e.symbol, "asset_type": e.asset_type, "excess_return_pct": e.excess_return_pct}
            for e in failed[:3]
        ],
        "winning_themes": themes(worked),
        "losing_themes": themes(failed),
        "avg_excess_return_vs_spy": avg_excess,
    }
