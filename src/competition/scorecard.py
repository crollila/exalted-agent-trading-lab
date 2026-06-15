"""Team scorecards with SPY benchmark comparison (Part 7).

Scorecards are deterministic snapshots of a team's competition state. They are
persisted under ``data/scorecards/`` (ignored runtime path) and include the SPY
benchmark return and excess return vs SPY required by the competition spec.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SCORECARD_DIR = Path("data/scorecards")


@dataclass
class TeamScorecard:
    team_id: str
    week_start: str
    week_end: str
    starting_equity: float
    current_equity: float
    cash: float = 0.0
    buying_power: float | None = None
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    short_exposure: float = 0.0
    options_premium_at_risk: float = 0.0
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    spy_benchmark_return: float | None = None
    excess_return_vs_spy: float | None = None
    drawdown: float = 0.0
    proposals_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    simulation_only_count: int = 0
    orders_submitted: int = 0
    current_rank: int | None = None
    latest_lessons: list[str] = field(default_factory=list)
    strategy_changes: list[str] = field(default_factory=list)
    risk_events: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def team_return(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return (self.current_equity - self.starting_equity) / self.starting_equity

    def compute_excess_return(self) -> None:
        if self.spy_benchmark_return is None:
            self.excess_return_vs_spy = None
        else:
            self.excess_return_vs_spy = self.team_return - self.spy_benchmark_return

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TeamScorecard":
        return cls(**data)


def _latest_path(team_id: str, scorecard_dir: Path | str) -> Path:
    return Path(scorecard_dir) / f"{team_id}_latest.json"


def save_scorecard(scorecard: TeamScorecard, scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR) -> Path:
    directory = Path(scorecard_dir)
    directory.mkdir(parents=True, exist_ok=True)
    scorecard.compute_excess_return()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    history_path = directory / f"{scorecard.team_id}_{timestamp}.json"
    history_path.write_text(json.dumps(scorecard.as_dict(), indent=2), encoding="utf-8")

    latest_path = _latest_path(scorecard.team_id, scorecard_dir)
    latest_path.write_text(json.dumps(scorecard.as_dict(), indent=2), encoding="utf-8")
    return latest_path


def load_latest_scorecard(
    team_id: str,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
) -> TeamScorecard | None:
    path = _latest_path(team_id, scorecard_dir)
    if not path.exists():
        return None
    return TeamScorecard.from_dict(json.loads(path.read_text(encoding="utf-8")))


def rank_scorecards(scorecards: list[TeamScorecard]) -> list[TeamScorecard]:
    """Rank teams by excess return vs SPY (fallback to raw return)."""

    def sort_key(card: TeamScorecard) -> float:
        if card.excess_return_vs_spy is not None:
            return card.excess_return_vs_spy
        return card.team_return

    ranked = sorted(scorecards, key=sort_key, reverse=True)
    for index, card in enumerate(ranked, start=1):
        card.current_rank = index
    return ranked


def export_scorecards_markdown(
    scorecards: list[TeamScorecard],
    report_path: Path | str,
) -> Path:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = rank_scorecards(list(scorecards))
    lines = ["# Team Scorecards", "", "Paper-only. Past paper results do not prove live profitability.", ""]
    for card in ranked:
        card.compute_excess_return()
        lines.append(f"## {card.team_id} (rank {card.current_rank})")
        lines.append("")
        lines.append(f"- Team return: {card.team_return:.4f}")
        spy = "unknown" if card.spy_benchmark_return is None else f"{card.spy_benchmark_return:.4f}"
        excess = "unknown" if card.excess_return_vs_spy is None else f"{card.excess_return_vs_spy:.4f}"
        lines.append(f"- SPY return: {spy}")
        lines.append(f"- Excess vs SPY: {excess}")
        lines.append(f"- Orders submitted: {card.orders_submitted}")
        lines.append(
            f"- Proposals: {card.proposals_count} "
            f"(approved {card.approved_count}, rejected {card.rejected_count}, "
            f"simulation_only {card.simulation_only_count})"
        )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
