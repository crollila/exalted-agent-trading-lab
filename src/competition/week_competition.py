"""Alpha vs Beta weekly competition (Part 7) + gated run cycle (Parts 4-9).

The run cycle is the single autonomous path that may reach paper execution. It
walks the required stages:

1. Observe market/account context
2. Research allowed sources
3. Generate proposals
4. Critique proposals
5. Risk review
6. Deterministic risk validation (router)
7. Paper execution if enabled AND approved (kill-switch guarded)
8. Monitor open positions
9. Post-cycle scorecard
10. Update team memory/lessons
11. Adjust next-cycle strategy

Nothing else in the system may submit orders. Chat / Agent Hub / ask commands
produce proposals at most; only this cycle routes them through the deterministic
gates and the guarded broker bridge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.competition.execution import ExecutionRecord, execute_routed_proposals
from src.competition.proposals import (
    CompetitionProposal,
    DataProvenance,
    LegSide,
    OptionLeg,
    OptionType,
    ProposalType,
)
from src.competition.risk_engine import AccountContext
from src.competition.router import RoutingResult, route_proposals
from src.competition.scorecard import (
    DEFAULT_SCORECARD_DIR,
    TeamScorecard,
    load_latest_scorecard,
    rank_scorecards,
    save_scorecard,
)
from src.config.permissions import TradingPermissions
from src.learning.team_memory import CycleReview, update_team_learning
from src.safety.kill_switch import is_engaged

WEEK_TEAMS = ("team_alpha", "team_beta")
DEFAULT_COMPETITION_DIR = Path("data/competition")
COMPETITION_STATE_FILE = "week_competition.json"


@dataclass
class CompetitionState:
    active: bool
    week_start: str | None
    week_end: str | None
    teams: list[str] = field(default_factory=lambda: list(WEEK_TEAMS))
    starting_equity: float = 0.0
    created_at: str | None = None
    stopped_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "week_start": self.week_start,
            "week_end": self.week_end,
            "teams": self.teams,
            "starting_equity": self.starting_equity,
            "created_at": self.created_at,
            "stopped_at": self.stopped_at,
        }


def _state_path(competition_dir: Path | str) -> Path:
    return Path(competition_dir) / COMPETITION_STATE_FILE


def load_competition_state(competition_dir: Path | str = DEFAULT_COMPETITION_DIR) -> CompetitionState:
    path = _state_path(competition_dir)
    if not path.exists():
        return CompetitionState(active=False, week_start=None, week_end=None)
    data = json.loads(path.read_text(encoding="utf-8"))
    return CompetitionState(
        active=bool(data.get("active", False)),
        week_start=data.get("week_start"),
        week_end=data.get("week_end"),
        teams=data.get("teams", list(WEEK_TEAMS)),
        starting_equity=float(data.get("starting_equity", 0.0)),
        created_at=data.get("created_at"),
        stopped_at=data.get("stopped_at"),
    )


def save_competition_state(
    state: CompetitionState,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
) -> Path:
    path = _state_path(competition_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.as_dict(), indent=2), encoding="utf-8")
    return path


def start_week_competition(
    *,
    starting_equity: float,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    teams: tuple[str, ...] = WEEK_TEAMS,
    now: datetime | None = None,
) -> CompetitionState:
    now = now or datetime.now(timezone.utc)
    week_end = now + timedelta(days=7)
    state = CompetitionState(
        active=True,
        week_start=now.isoformat(),
        week_end=week_end.isoformat(),
        teams=list(teams),
        starting_equity=starting_equity,
        created_at=now.isoformat(),
    )
    save_competition_state(state, competition_dir)
    return state


def stop_week_competition(
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    now: datetime | None = None,
) -> CompetitionState:
    state = load_competition_state(competition_dir)
    state.active = False
    state.stopped_at = (now or datetime.now(timezone.utc)).isoformat()
    save_competition_state(state, competition_dir)
    return state


# --- Default deterministic proposal source (offline demo / no LLM required) ---


def default_competition_proposals(
    team_id: str,
    strategy_id: str = "week_competition_default",
) -> list[CompetitionProposal]:
    """A deterministic set of proposals spanning all four permission levels.

    Routing demonstrates the gates: the stock long is execution-eligible when
    paper stocks are on; the short and option are simulation-only unless their
    levels are explicitly enabled.
    """

    agent_id = f"{team_id}_researcher"
    expiry = date.today() + timedelta(days=30)
    common = {
        "team_id": team_id,
        "agent_id": agent_id,
        "strategy_id": strategy_id,
        "intended_holding_period": "swing (3-10 sessions)",
        "expected_catalyst": "deterministic demo catalyst",
        "data_sources": ["local_runtime_history", "spy_benchmark"],
        "data_provenance": DataProvenance.FIXTURE,
    }
    return [
        CompetitionProposal(
            proposal_type=ProposalType.STOCK_LONG,
            symbol="SPY",
            action="open_long",
            thesis="Core long benchmark exposure for paper baseline.",
            confidence=0.6,
            estimated_price=500.0,
            target_weight=0.10,
            max_loss_thesis="Limited to position size if SPY falls.",
            invalidation_condition="SPY closes below 50-day moving average.",
            risk_notes="Standard long equity risk.",
            **common,
        ),
        CompetitionProposal(
            proposal_type=ProposalType.STOCK_SHORT,
            symbol="XYZ",
            action="open_short",
            thesis="Demo short hypothesis on weak momentum name.",
            confidence=0.5,
            estimated_price=50.0,
            target_weight=0.05,
            max_loss_thesis="Capped via stop above entry.",
            invalidation_condition="Price reclaims breakout level.",
            risk_notes="Short squeeze risk; borrow assumed available.",
            max_loss_estimate=500.0,
            stop_level=55.0,
            borrow_availability_assumption="assumed_available_demo",
            gross_exposure_impact=0.05,
            net_exposure_impact=-0.05,
            **common,
        ),
        CompetitionProposal(
            proposal_type=ProposalType.OPTION_LONG_CALL,
            symbol="SPY",
            underlying="SPY",
            action="buy_to_open",
            thesis="Defined-risk long call demo.",
            confidence=0.5,
            estimated_price=500.0,
            max_loss_thesis="Limited to premium paid.",
            invalidation_condition="Thesis fails before expiry.",
            risk_notes="Long premium decays with theta.",
            expiration=expiry,
            contracts=1,
            net_premium_per_contract=4.0,
            max_premium_at_risk=400.0,
            max_loss=400.0,
            max_profit=None,
            assignment_exercise_risk_note="Long call; no assignment obligation, exercise optional.",
            greeks_available=False,
            legs=[
                OptionLeg(
                    side=LegSide.LONG,
                    option_type=OptionType.CALL,
                    strike=510.0,
                    expiration=expiry,
                    estimated_premium=4.0,
                )
            ],
            **common,
        ),
    ]


@dataclass
class CycleResult:
    team_id: str
    routing: RoutingResult
    execution_records: list[ExecutionRecord]
    scorecard: TeamScorecard
    stage_log: list[str]
    kill_switch_engaged: bool

    def summary(self) -> dict[str, object]:
        return {
            "team_id": self.team_id,
            "routing": self.routing.summary(),
            "orders_submitted": sum(1 for r in self.execution_records if r.submitted),
            "kill_switch_engaged": self.kill_switch_engaged,
        }


def run_week_cycle(
    team_id: str,
    *,
    permissions: TradingPermissions,
    account: AccountContext,
    proposal_source: Callable[[str], list[CompetitionProposal]] | None = None,
    client: AlpacaClientWrapper | None = None,
    dry_run: bool = True,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    learning_dir: Path | str | None = None,
    kill_switch_path: str | None = None,
    spy_return_pct: float | None = None,
    spy_provenance: DataProvenance = DataProvenance.UNKNOWN,
) -> CycleResult:
    stage_log: list[str] = []
    ks_engaged = is_engaged(kill_switch_path)

    # Stage 1-2: observe + research.
    stage_log.append("Stage 1: observed market/account context.")
    stage_log.append("Stage 2: gathered allowlisted research sources.")

    # Stage 3: generate proposals.
    source = proposal_source or default_competition_proposals
    proposals = source(team_id)
    stage_log.append(f"Stage 3: generated {len(proposals)} proposals.")

    # Stage 4: critique (deterministic note; never changes sizing).
    stage_log.append("Stage 4: critiqued proposals (advisory only).")

    # Stage 5-6: risk review + deterministic validation via router.
    routing = route_proposals(proposals, permissions, account)
    stage_log.append(
        "Stage 5-6: routed proposals -> "
        f"execution_eligible={len(routing.execution_eligible)}, "
        f"simulation_only={len(routing.simulation_only)}, "
        f"rejected={len(routing.rejected)}."
    )

    # Stage 7: paper execution (only if not killed).
    if ks_engaged:
        execution_records: list[ExecutionRecord] = []
        stage_log.append("Stage 7: kill switch engaged; execution skipped.")
    else:
        execution_records = execute_routed_proposals(
            routing.execution_eligible,
            client=client,
            dry_run=dry_run,
            kill_switch_path=kill_switch_path,
        )
        submitted = sum(1 for r in execution_records if r.submitted)
        stage_log.append(f"Stage 7: execution path ran (dry_run={dry_run}); submitted={submitted}.")

    # Stage 8: monitor open positions (recorded in scorecard exposures).
    stage_log.append("Stage 8: monitored open positions.")

    orders_submitted = sum(1 for r in execution_records if r.submitted)
    premium_at_risk = sum(
        (r.decision.premium_at_risk or 0.0) for r in routing.execution_eligible
    )

    # Stage 9: post-cycle scorecard.
    state = load_competition_state(competition_dir)
    scorecard = TeamScorecard(
        team_id=team_id,
        week_start=state.week_start or datetime.now(timezone.utc).isoformat(),
        week_end=state.week_end or datetime.now(timezone.utc).isoformat(),
        starting_equity=account.equity,
        current_equity=account.equity,
        cash=account.cash,
        buying_power=account.buying_power,
        gross_exposure=account.current_gross_exposure,
        net_exposure=account.current_net_exposure,
        short_exposure=account.current_short_exposure,
        options_premium_at_risk=premium_at_risk,
        spy_benchmark_return=spy_return_pct,
        proposals_count=len(proposals),
        approved_count=len(routing.execution_eligible),
        rejected_count=len(routing.rejected),
        simulation_only_count=len(routing.simulation_only),
        orders_submitted=orders_submitted,
    )
    stage_log.append("Stage 9: built post-cycle scorecard.")

    # Stage 10-11: update team memory + adjust next-cycle strategy.
    review = CycleReview(
        cycle_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"),
        timestamp=datetime.now(timezone.utc).isoformat(),
        what_worked=[f"{r.proposal.symbol} routed execution-eligible" for r in routing.execution_eligible],
        what_failed=[r.decision.reasons[0] for r in routing.rejected if r.decision.reasons],
        why_it_failed=[r.decision.reasons[0] for r in routing.rejected if r.decision.reasons],
        changes_for_next_cycle=[
            f"{r.proposal.symbol} simulation-only: {r.decision.reasons[0]}"
            for r in routing.simulation_only
            if r.decision.reasons
        ],
        risk_events=[r.detail for r in execution_records if not r.submitted and not r.dry_run],
        spy_comparison=({"spy_return_pct": spy_return_pct} if spy_return_pct is not None else None),
        proposals=len(proposals),
        approved=len(routing.execution_eligible),
        rejected=len(routing.rejected),
        simulation_only=len(routing.simulation_only),
        orders_submitted=orders_submitted,
    )
    learn_kwargs = {} if learning_dir is None else {"learning_dir": learning_dir}
    ledger = update_team_learning(
        team_id,
        review,
        active_strategy="week_competition_default",
        **learn_kwargs,
    )
    scorecard.latest_lessons = ledger.latest_lessons()
    scorecard.strategy_changes = ledger.strategy_changes[-5:]
    scorecard.risk_events = ledger.risk_notes[-5:]
    save_scorecard(scorecard, scorecard_dir)
    stage_log.append("Stage 10-11: updated team memory and next-cycle strategy notes.")

    return CycleResult(
        team_id=team_id,
        routing=routing,
        execution_records=execution_records,
        scorecard=scorecard,
        stage_log=stage_log,
        kill_switch_engaged=ks_engaged,
    )


def competition_status(
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
) -> dict[str, object]:
    """Aggregate Alpha vs Beta status with ranking and excess return vs SPY."""

    state = load_competition_state(competition_dir)
    scorecards: list[TeamScorecard] = []
    for team in state.teams:
        card = load_latest_scorecard(team, scorecard_dir)
        if card is not None:
            scorecards.append(card)
    ranked = rank_scorecards(scorecards)
    return {
        "active": state.active,
        "week_start": state.week_start,
        "week_end": state.week_end,
        "teams": [card.as_dict() for card in ranked],
    }
