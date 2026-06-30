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
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.competition.benchmark import (
    TIMEFRAME_UNKNOWN,
    build_benchmark_anchors,
)
from src.competition.broker_snapshot import BrokerSnapshot, build_snapshot_from_parts
from src.competition.candidate_generation import (
    CandidateGenerationOutcome,
    classify_candidate_outcome,
)
from src.competition.state_reconciliation import ReconciliationResult
from src.competition.execution import ExecutionRecord, execute_routed_proposals
from src.competition.proposals import (
    CompetitionProposal,
    DataProvenance,
    LegSide,
    OptionLeg,
    OptionType,
    ProposalType,
)
from src.competition.attribution import (
    DEFAULT_ATTRIBUTION_DIR,
    ProposalAttribution,
    performance_feedback,
    record_attributions,
)
from src.competition.portfolio_manager import (
    PortfolioDecision,
    PortfolioManagerConfig,
    review_portfolio,
)
from src.competition.risk_engine import AccountContext, AdvancedRiskDecision, Route
from src.competition.router import RoutedProposal, RoutingResult, route_proposals
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
    starting_spy_price: float | None = None
    created_at: str | None = None
    stopped_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "week_start": self.week_start,
            "week_end": self.week_end,
            "teams": self.teams,
            "starting_equity": self.starting_equity,
            "starting_spy_price": self.starting_spy_price,
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
        starting_spy_price=(
            float(data["starting_spy_price"]) if data.get("starting_spy_price") is not None else None
        ),
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
    starting_spy_price: float | None = None,
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
        starting_spy_price=starting_spy_price,
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
class ProposalBundle:
    """Proposals plus optional LLM cycle metadata for the learning loop.

    The deterministic ``default_competition_proposals`` returns a plain list; the
    LLM source returns this richer bundle so ``run_week_cycle`` can fold the
    model's market summary, learning update, hypothesis, and watchlist into the
    team's learning ledger. Sizing is still computed by the deterministic engine.
    """

    proposals: list[CompetitionProposal]
    market_summary: str | None = None
    learning_update: dict | None = None
    research_notes: list[dict] | None = None
    hypothesis: str | None = None
    watchlist: list[str] | None = None
    active_strategy: str | None = None
    rejected_ideas: list[str] = field(default_factory=list)
    raw_errors: list[str] = field(default_factory=list)
    # proposal_id -> research source ids the model cited for it (Task 5/7).
    proposal_source_ids: dict[str, list[str]] = field(default_factory=dict)
    research_source_ids: list[str] = field(default_factory=list)
    # Optional LLM tactical intent for the Portfolio Manager (Phase 7M, advisory only).
    portfolio_decision: dict | None = None
    # Phase 7Z: provider/model call outcome (names only; never secrets / raw prompt).
    provider_called: bool = False
    provider_failed: bool = False
    provider_name: str | None = None
    model_name: str | None = None

    @property
    def invalid_model_output(self) -> bool:
        """Provider answered but produced no usable candidate (validation failed).

        Distinguishes a malformed/invalid model response (had errors, parsed
        nothing) from a genuine empty-candidate response (``model_zero_candidates``)
        and from a provider/transport failure (``provider_failed``).
        """

        return (
            self.provider_called
            and not self.provider_failed
            and not self.proposals
            and bool(self.raw_errors)
        )


def _as_bundle(result) -> ProposalBundle:
    if isinstance(result, ProposalBundle):
        return result
    return ProposalBundle(proposals=list(result))


def _demote_to_simulation(routed: RoutedProposal, reason: str) -> RoutedProposal:
    """Return a copy of ``routed`` re-routed to simulation_only with a reason."""

    decision = routed.decision
    new_decision = AdvancedRiskDecision(
        proposal_id=decision.proposal_id,
        proposal_type=decision.proposal_type,
        level=decision.level,
        route=Route.SIMULATION_ONLY,
        approved=False,
        reasons=[*decision.reasons, reason],
        approved_quantity=decision.approved_quantity,
        approved_contracts=decision.approved_contracts,
        approved_notional=decision.approved_notional,
        premium_at_risk=decision.premium_at_risk,
    )
    return RoutedProposal(proposal=routed.proposal, decision=new_decision)


def apply_portfolio_gate(routing: RoutingResult, decision: PortfolioDecision) -> RoutingResult:
    """Enforce the Portfolio Manager decision on routed proposals.

    New-money buys (execution_eligible) beyond ``max_new_proposals_this_cycle``
    — or all of them when the team is in a no-trade/blocked state — are demoted
    to simulation_only (advisory) with a clear reason. This never *promotes*
    anything and never bypasses the deterministic risk engine.
    """

    eligible = list(routing.execution_eligible)
    if not eligible:
        return routing

    limit = decision.max_new_proposals_this_cycle if decision.allowed_to_generate_new_orders else 0
    if limit >= len(eligible):
        return routing

    # Keep the highest-confidence ideas; demote the rest to advisory.
    eligible.sort(key=lambda r: r.proposal.confidence, reverse=True)
    kept = eligible[:limit]
    demoted = eligible[limit:]
    reason = (
        f"Portfolio manager: {decision.decision_type} "
        f"(new-order cap {limit}; {decision.rejected_new_ideas_reason or 'capital allocation'})."
    )
    demoted_sim = [_demote_to_simulation(r, reason) for r in demoted]
    return RoutingResult(
        execution_eligible=kept,
        simulation_only=[*routing.simulation_only, *demoted_sim],
        rejected=list(routing.rejected),
    )


@dataclass
class CycleResult:
    team_id: str
    routing: RoutingResult
    execution_records: list[ExecutionRecord]
    scorecard: TeamScorecard
    stage_log: list[str]
    kill_switch_engaged: bool
    bundle: "ProposalBundle | None" = None
    portfolio_decision: "PortfolioDecision | None" = None
    review_only: bool = False
    # Phase 7Z: machine-readable candidate-generation outcome + grounding snapshot.
    candidate_outcome: "CandidateGenerationOutcome | None" = None
    snapshot: "BrokerSnapshot | None" = None

    @property
    def no_trade(self) -> bool:
        if self.review_only:
            return True
        if self.portfolio_decision is not None:
            return self.portfolio_decision.is_no_trade()
        return sum(1 for r in self.execution_records if r.submitted) == 0

    @property
    def no_trade_reason_class(self) -> str | None:
        return self.candidate_outcome.no_trade_reason_class if self.candidate_outcome else None

    def summary(self) -> dict[str, object]:
        return {
            "team_id": self.team_id,
            "routing": self.routing.summary(),
            "orders_submitted": sum(1 for r in self.execution_records if r.submitted),
            "kill_switch_engaged": self.kill_switch_engaged,
            "review_only": self.review_only,
            "portfolio_decision": (
                self.portfolio_decision.decision_type if self.portfolio_decision else None
            ),
            "no_trade": self.no_trade,
            "no_trade_reason_class": self.no_trade_reason_class,
        }


def _record_cycle_attribution(
    *,
    team_id: str,
    cycle_id: str,
    proposal_source: str,
    routing: RoutingResult,
    execution_records,
    bundle: ProposalBundle,
    spy_return_pct: float | None,
    llm_update: dict,
    attribution_dir: Path | str | None,
) -> None:
    exec_by_id = {r.proposal_id: r for r in execution_records}
    entries: list[ProposalAttribution] = []

    routed_groups = (
        ("execution_eligible", routing.execution_eligible),
        ("simulation_only", routing.simulation_only),
        ("rejected", routing.rejected),
    )
    for routing_label, group in routed_groups:
        for routed in group:
            proposal = routed.proposal
            decision = routed.decision
            record = exec_by_id.get(proposal.proposal_id)
            order_id = None
            submitted = False
            lesson = None
            broker_rejected = False
            broker_reject_reason = None
            broker_reject_code = None
            failure_category = None
            if record is not None:
                submitted = record.submitted
                raw_order_id = getattr(record.broker_response, "id", None) if record.broker_response else None
                order_id = str(raw_order_id) if raw_order_id is not None else None
                broker_rejected = record.broker_rejected
                broker_reject_reason = record.broker_reject_reason
                broker_reject_code = record.broker_reject_code
                failure_category = record.failure_category
                if not record.submitted and not record.dry_run:
                    lesson = record.detail
            # Approved sizing comes from the deterministic risk engine, never the model.
            quantity = decision.approved_quantity
            if quantity is None and decision.approved_contracts is not None:
                quantity = float(decision.approved_contracts)
            entries.append(
                ProposalAttribution(
                    proposal_id=proposal.proposal_id,
                    team_id=team_id,
                    strategy_id=proposal.strategy_id,
                    asset_type=proposal.proposal_type.value,
                    symbol=proposal.underlying or proposal.symbol,
                    thesis=proposal.thesis,
                    cycle_id=cycle_id,
                    data_sources_used=list(proposal.data_sources),
                    research_source_ids=bundle.proposal_source_ids.get(proposal.proposal_id, []),
                    routing=routing_label,
                    broker_submitted=submitted,
                    broker_rejected=broker_rejected,
                    broker_reject_reason=broker_reject_reason,
                    broker_reject_code=broker_reject_code,
                    failure_category=failure_category,
                    order_id=order_id,
                    action=proposal.action,
                    quantity=(float(quantity) if quantity is not None else None),
                    entry_price=proposal.estimated_price,
                    position_status=("open" if submitted else "none"),
                    holding_period=proposal.intended_holding_period,
                    spy_return=spy_return_pct,
                    thesis_outcome="pending",
                    lesson_learned=lesson or (llm_update.get("what_failed") if routing_label == "rejected" else None),
                    next_adjustment=llm_update.get("next_adjustment"),
                )
            )
    kwargs = {} if attribution_dir is None else {"attribution_dir": attribution_dir}
    record_attributions(entries, **kwargs)


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
    attribution_dir: Path | str | None = None,
    portfolio_config: PortfolioManagerConfig | None = None,
    positions: list | None = None,
    review_only: bool = False,
    snapshot: BrokerSnapshot | None = None,
    reconciliation: "object | None" = None,
    spy_start_price: float | None = None,
    spy_current_price: float | None = None,
    benchmark_timeframe: str = TIMEFRAME_UNKNOWN,
    candidate_generation_enabled: bool = True,
    team_autonomy_enabled: bool = True,
) -> CycleResult:
    stage_log: list[str] = []
    ks_engaged = is_engaged(kill_switch_path)
    if review_only:
        stage_log.append("Review-only cycle: portfolio/strategy review + memory; no new broker orders.")

    # Phase 7Z: ground the cycle on ONE immutable current-cycle broker snapshot.
    # When a caller (e.g. tests / the deterministic path) does not pass one, derive
    # it from the account + positions already provided so the snapshot is always
    # available downstream. A real failed live read arrives as an unavailable
    # snapshot and is honored (never treated as a flat/funded book).
    if snapshot is None:
        snapshot = build_snapshot_from_parts(
            team_id,
            account={
                "equity": account.equity,
                "cash": account.cash,
                "buying_power": account.buying_power,
            },
            raw_positions=positions or [],
            account_read_ok=True,
            orders_today=account.orders_today,
            daily_notional_today=account.daily_notional_today,
            as_of=account.as_of,
        )
        stage_log.append("Stage 0: derived broker snapshot from provided account/positions.")
    else:
        stage_log.append(
            f"Stage 0: broker snapshot {snapshot.status} "
            f"(source={snapshot.source}, positions={snapshot.position_count})."
        )

    # Stage 1-2: observe + research.
    stage_log.append("Stage 1: observed market/account context.")
    stage_log.append("Stage 2: gathered allowlisted research sources.")

    # Stage 3: generate proposals (default deterministic or LLM bundle).
    source = proposal_source or default_competition_proposals
    bundle = _as_bundle(source(team_id))
    proposals = bundle.proposals
    stage_log.append(f"Stage 3: generated {len(proposals)} proposals.")
    if bundle.raw_errors:
        stage_log.append(f"Stage 3: {len(bundle.raw_errors)} proposal(s) rejected during parsing.")
    if bundle.market_summary:
        stage_log.append(f"Stage 3: LLM market summary captured.")

    # Stage 4: critique (deterministic note; never changes sizing).
    stage_log.append("Stage 4: critiqued proposals (advisory only).")

    # Stage 4b: Portfolio Manager / Capital Allocator review (Phase 7M).
    pm_config = portfolio_config or PortfolioManagerConfig.from_env()
    pm_attribution_dir = attribution_dir if attribution_dir is not None else DEFAULT_ATTRIBUTION_DIR
    prior_scorecard = load_latest_scorecard(team_id, scorecard_dir)
    try:
        attribution_feedback = performance_feedback(team_id, attribution_dir=pm_attribution_dir)
    except Exception:  # noqa: BLE001 - missing/old attribution must never crash the cycle
        attribution_feedback = {}
    portfolio_decision = review_portfolio(
        team_id=team_id,
        config=pm_config,
        permissions=permissions,
        account=account,
        candidate_count=len(proposals),
        spy_excess=(prior_scorecard.excess_return_vs_spy if prior_scorecard else None),
        team_return=(prior_scorecard.team_return if prior_scorecard else None),
        spy_return=spy_return_pct,
        attribution_feedback=attribution_feedback,
        positions=positions,
        llm_intent=bundle.portfolio_decision,
    )
    stage_log.append(
        f"Stage 4b: portfolio manager decided {portfolio_decision.summary()}."
    )
    if portfolio_decision.low_buying_power:
        stage_log.append("Stage 4b: low buying power -> portfolio review (cycle not hard-stopped).")
    if portfolio_decision.rejected_new_ideas_reason:
        stage_log.append(f"Stage 4b: new-money buys blocked: {portfolio_decision.rejected_new_ideas_reason}")

    # Stage 5-6: risk review + deterministic validation via router.
    routing = route_proposals(proposals, permissions, account)
    # Pre-gate risk-approved count: how many proposals the deterministic risk engine
    # made execution-eligible BEFORE the portfolio gate (used to distinguish a
    # review-only submission block — which demotes eligible proposals — from a
    # genuine no-trade).
    risk_approved_count = len(routing.execution_eligible)
    # Stage 6b: apply the Portfolio Manager dynamic cap (demotes extra opens to advisory).
    # Review-only forces an advisory-only gate so nothing reaches execution.
    gate_decision = portfolio_decision
    if review_only:
        gate_decision = replace(
            portfolio_decision,
            allowed_to_generate_new_orders=False,
            max_new_proposals_this_cycle=0,
            rejected_new_ideas_reason="review-only mode: advisory recommendations only, no new orders",
        )
    routing = apply_portfolio_gate(routing, gate_decision)
    stage_log.append(
        "Stage 5-6: routed proposals -> "
        f"execution_eligible={len(routing.execution_eligible)}, "
        f"simulation_only={len(routing.simulation_only)}, "
        f"rejected={len(routing.rejected)}."
    )
    if portfolio_decision.is_no_trade():
        stage_log.append("Stage 6b: NO-TRADE decision — no new broker orders this cycle (valid outcome).")

    # Stage 7: paper execution (only if not killed and not review-only).
    if ks_engaged or review_only:
        execution_records: list[ExecutionRecord] = []
        stage_log.append(
            "Stage 7: kill switch engaged; execution skipped."
            if ks_engaged
            else "Stage 7: review-only mode; broker execution skipped (advisory only)."
        )
    else:
        execution_records = execute_routed_proposals(
            routing.execution_eligible,
            client=client,
            dry_run=dry_run,
            kill_switch_path=kill_switch_path,
            daily_notional_used=account.daily_notional_today,
        )
        submitted = sum(1 for r in execution_records if r.submitted)
        stage_log.append(f"Stage 7: execution path ran (dry_run={dry_run}); submitted={submitted}.")

    # Stage 8: monitor open positions (recorded in scorecard exposures).
    stage_log.append("Stage 8: monitored open positions.")

    orders_submitted = sum(1 for r in execution_records if r.submitted)
    broker_rejected_count = sum(1 for r in execution_records if r.broker_rejected)
    premium_at_risk = sum(
        (r.decision.premium_at_risk or 0.0) for r in routing.execution_eligible
    )

    # Stage 8b: classify the candidate-generation outcome. A GENUINE no-trade gets
    # exactly one ``no_trade_reason_class`` (never null); a cycle that produced
    # execution-eligible proposals but submitted nothing instead gets an explicit
    # ``execution_block_reason`` (dry-run / kill switch / review-only / team autonomy
    # off / no broker client) — so a no-trade class never describes an approved cycle.
    # ``execution_config_enabled`` is the config gate (paper mode AND stocks); the
    # execution-mode flags below are reported separately and never widen a real gate.
    execution_config_enabled = permissions.is_paper and permissions.stocks_enabled()
    pm_allows_new = (
        portfolio_decision.allowed_to_generate_new_orders
        and portfolio_decision.max_new_proposals_this_cycle > 0
    )
    pm_genuine_hold = portfolio_decision.is_no_trade() and (
        len(proposals) > 0 or portfolio_decision.low_buying_power
    )
    daily_cap_reached = account.orders_today >= permissions.max_daily_orders_per_team
    provider_failure_category = None
    if bundle.provider_failed and bundle.raw_errors:
        first = bundle.raw_errors[0].lower()
        if any(kw in first for kw in ("api_key", "api key", "missing", "credential", "blank", "not configured", "unavailable")):
            provider_failure_category = "missing_credentials"
        elif "non-json" in first or "json" in first or "output must be" in first:
            provider_failure_category = "invalid_provider_output"
        else:
            provider_failure_category = "call_failed"
    candidate_outcome = classify_candidate_outcome(
        team_id=team_id,
        account_available=snapshot.is_available,
        execution_config_enabled=execution_config_enabled,
        health_block=portfolio_decision.low_buying_power,
        health_block_reason=portfolio_decision.rejected_new_ideas_reason,
        portfolio_manager_allows_new=pm_allows_new,
        portfolio_manager_is_genuine_hold=pm_genuine_hold,
        candidate_generation_enabled=candidate_generation_enabled,
        provider_called=bundle.provider_called,
        provider_name=bundle.provider_name,
        model_name=bundle.model_name,
        provider_failed=bundle.provider_failed,
        provider_failure_category=provider_failure_category,
        invalid_model_output=bundle.invalid_model_output,
        parsed_proposal_count=len(proposals),
        routed_execution_eligible=len(routing.execution_eligible),
        routed_simulation_only=len(routing.simulation_only),
        routed_rejected=len(routing.rejected),
        orders_submitted=orders_submitted,
        daily_cap_reached=daily_cap_reached,
        risk_approved_count=risk_approved_count,
        dry_run=dry_run,
        kill_switch_engaged=ks_engaged,
        review_only=review_only,
        team_autonomy_enabled=team_autonomy_enabled,
        broker_client_available=client is not None,
    )
    stage_log.append(
        f"Stage 8b: candidate-generation outcome -> "
        f"reached={candidate_outcome.reached_candidate_generation}, "
        f"provider={candidate_outcome.provider_outcome}, "
        f"no_trade_reason={candidate_outcome.no_trade_reason_class or 'n/a'}, "
        f"execution_block={candidate_outcome.execution_block_reason or 'n/a'}."
    )

    # Stage 9: post-cycle scorecard.
    state = load_competition_state(competition_dir)
    reconciliation_status = (
        reconciliation.status
        if isinstance(reconciliation, ReconciliationResult)
        else ("account_state_unavailable" if not snapshot.is_available else "clean")
    )
    anchors = build_benchmark_anchors(
        team_id,
        timeframe=benchmark_timeframe,
        period_start=state.week_start,
        period_end=datetime.now(timezone.utc).isoformat(),
        team_start_equity=(state.starting_equity or account.equity),
        team_end_equity=account.equity,
        spy_start_price=spy_start_price,
        spy_end_price=spy_current_price,
    )
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
        broker_rejected_count=broker_rejected_count,
        portfolio_decision_type=portfolio_decision.decision_type,
        portfolio_no_trade=portfolio_decision.is_no_trade(),
        max_new_proposals=portfolio_decision.max_new_proposals_this_cycle,
        no_trade_reason_class=candidate_outcome.no_trade_reason_class,
        execution_block_reason=candidate_outcome.execution_block_reason,
        candidate_generation_outcome=candidate_outcome.as_dict(),
        reconciliation_status=reconciliation_status,
        reconciliation_conflicts=(
            list(reconciliation.warnings())
            if isinstance(reconciliation, ReconciliationResult)
            else []
        ),
        account_read_ok=snapshot.account_read_ok,
        account_snapshot_source=snapshot.source,
        account_snapshot_time=snapshot.snapshot_time,
        routed_provider=bundle.provider_name,
        routed_model=bundle.model_name,
        provider_outcome=candidate_outcome.provider_outcome,
        spy_start_price=spy_start_price,
        spy_end_price=spy_current_price,
        benchmark_period_start=anchors.period_start,
        benchmark_period_end=anchors.period_end,
        benchmark_timeframe=anchors.timeframe,
    )
    stage_log.append("Stage 9: built post-cycle scorecard.")

    # Stage 10-11: update team memory + adjust next-cycle strategy.
    what_worked = [f"{r.proposal.symbol} routed execution-eligible" for r in routing.execution_eligible]
    what_failed = [r.decision.reasons[0] for r in routing.rejected if r.decision.reasons]
    why_failed = [r.decision.reasons[0] for r in routing.rejected if r.decision.reasons]
    changes = [
        f"{r.proposal.symbol} simulation-only: {r.decision.reasons[0]}"
        for r in routing.simulation_only
        if r.decision.reasons
    ]
    post_trade_reviews = [
        f"{r.symbol} [{r.proposal_type}]: {r.detail}" for r in execution_records
    ]

    # Fold in the LLM's own learning update / rationale, when present.
    llm_update = bundle.learning_update or {}
    if isinstance(llm_update, dict):
        if llm_update.get("what_worked"):
            what_worked.append(f"LLM: {llm_update['what_worked']}")
        if llm_update.get("what_failed"):
            what_failed.append(f"LLM: {llm_update['what_failed']}")
        if llm_update.get("next_adjustment"):
            changes.append(f"LLM next adjustment: {llm_update['next_adjustment']}")
    if bundle.market_summary:
        post_trade_reviews.append(f"LLM market summary: {bundle.market_summary}")

    # Portfolio Manager decision folds into the team's compact strategy memory.
    changes.append(f"Portfolio manager: {portfolio_decision.decision_type} (cap {portfolio_decision.max_new_proposals_this_cycle}).")
    avoid_next_cycle: list[str] = []
    if portfolio_decision.rejected_new_ideas_reason:
        avoid_next_cycle.append(portfolio_decision.rejected_new_ideas_reason)
    for record in execution_records:
        if record.broker_rejected:
            avoid_next_cycle.append(
                f"{record.symbol}: broker {record.failure_category} ({record.broker_reject_reason})"
            )

    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    review = CycleReview(
        cycle_id=cycle_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        what_worked=what_worked,
        what_failed=what_failed,
        why_it_failed=why_failed,
        changes_for_next_cycle=changes,
        risk_events=[r.detail for r in execution_records if not r.submitted and not r.dry_run],
        post_trade_reviews=post_trade_reviews,
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
        active_strategy=bundle.active_strategy or "week_competition_default",
        hypothesis=bundle.hypothesis,
        watchlist=bundle.watchlist,
        rejected_ideas=bundle.rejected_ideas or None,
        mode=portfolio_decision.mode or None,
        avoid_next_cycle=avoid_next_cycle or None,
        mark_full_cycle=not review_only,
        **learn_kwargs,
    )
    scorecard.latest_lessons = ledger.latest_lessons()
    scorecard.strategy_changes = ledger.strategy_changes[-5:]
    scorecard.risk_events = ledger.risk_notes[-5:]
    save_scorecard(scorecard, scorecard_dir)
    stage_log.append("Stage 10-11: updated team memory and next-cycle strategy notes.")

    # Stage 7b: proposal/trade attribution + effectiveness tracking.
    _record_cycle_attribution(
        team_id=team_id,
        cycle_id=cycle_id,
        proposal_source=("llm" if bundle.market_summary or bundle.proposal_source_ids else "default"),
        routing=routing,
        execution_records=execution_records,
        bundle=bundle,
        spy_return_pct=spy_return_pct,
        llm_update=llm_update if isinstance(llm_update, dict) else {},
        attribution_dir=attribution_dir,
    )
    stage_log.append("Stage 7b: recorded proposal attribution for effectiveness tracking.")

    return CycleResult(
        team_id=team_id,
        routing=routing,
        execution_records=execution_records,
        scorecard=scorecard,
        stage_log=stage_log,
        kill_switch_engaged=ks_engaged,
        bundle=bundle,
        portfolio_decision=portfolio_decision,
        review_only=review_only,
        candidate_outcome=candidate_outcome,
        snapshot=snapshot,
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
