"""Proposal/trade attribution + effectiveness tracking (Task 7).

Records one attribution entry per proposal in a cycle and computes outcome metrics
(return %, excess vs SPY, thesis outcome) when prices are available. Stored under
the ignored runtime path ``data/attribution/``. No secrets.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ATTRIBUTION_DIR = Path("data/attribution")


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
    order_id: str | None = None
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

    def compute_outcome(self) -> None:
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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _team_path(team_id: str, attribution_dir: Path | str) -> Path:
    return Path(attribution_dir) / f"{team_id}_attribution.jsonl"


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
            out.append(ProposalAttribution(**json.loads(line)))
    return out


def performance_feedback(
    team_id: str,
    *,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    recent: int = 20,
) -> dict[str, Any]:
    """Summarize recent winners/losers/best/worst for the next prompt (Task 8)."""

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
            {"symbol": e.symbol, "lesson": e.lesson_learned}
            for e in entries
            if e.routing == "execution_eligible" and not e.broker_submitted
        ][:5],
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "best_strategy": best_strategy,
        "worst_strategy": worst_strategy,
        "pending_count": sum(1 for e in entries if e.thesis_outcome == "pending"),
    }
