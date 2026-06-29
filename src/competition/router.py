"""Deterministic proposal routing (Part 4).

Routes a batch of proposals into three buckets:

* ``execution_eligible`` — paper permission enabled AND deterministic risk passed.
* ``simulation_only`` — permission flag disabled (researched but never executed),
  or eligible-but-over the team's daily order cap.
* ``rejected`` — malformed or violates a hard deterministic rule.

This module never submits orders. It is the only thing that decides which
proposals are *allowed* to reach the gated execution path; execution itself is a
separate, kill-switch-guarded step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.competition.proposals import CompetitionProposal
from src.competition.risk_engine import (
    AccountContext,
    AdvancedRiskDecision,
    Route,
    evaluate_proposal,
)
from src.config.permissions import TradingPermissions


@dataclass(frozen=True)
class RoutedProposal:
    proposal: CompetitionProposal
    decision: AdvancedRiskDecision


@dataclass(frozen=True)
class RoutingResult:
    execution_eligible: list[RoutedProposal] = field(default_factory=list)
    simulation_only: list[RoutedProposal] = field(default_factory=list)
    rejected: list[RoutedProposal] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "execution_eligible": len(self.execution_eligible),
            "simulation_only": len(self.simulation_only),
            "rejected": len(self.rejected),
        }


def _demote(routed: RoutedProposal, note: str) -> RoutedProposal:
    """Return a copy of ``routed`` re-routed to simulation_only with ``note``."""

    d = routed.decision
    return RoutedProposal(
        proposal=routed.proposal,
        decision=AdvancedRiskDecision(
            proposal_id=d.proposal_id,
            proposal_type=d.proposal_type,
            level=d.level,
            route=Route.SIMULATION_ONLY,
            approved=False,
            reasons=[note],
            approved_quantity=d.approved_quantity,
            approved_contracts=d.approved_contracts,
            approved_notional=d.approved_notional,
            premium_at_risk=d.premium_at_risk,
        ),
    )


def route_proposals(
    proposals: list[CompetitionProposal],
    permissions: TradingPermissions,
    account: AccountContext,
    *,
    max_daily_notional_per_team: float | None = None,
) -> RoutingResult:
    execution: list[RoutedProposal] = []
    simulation: list[RoutedProposal] = []
    rejected: list[RoutedProposal] = []

    for proposal in proposals:
        decision = evaluate_proposal(proposal, permissions, account)
        routed = RoutedProposal(proposal=proposal, decision=decision)
        if decision.route == Route.EXECUTION_ELIGIBLE:
            execution.append(routed)
        elif decision.route == Route.SIMULATION_ONLY:
            simulation.append(routed)
        else:
            rejected.append(routed)

    # Enforce the per-team daily order cap deterministically: keep the highest
    # confidence proposals as execution-eligible, demote the rest to simulation.
    remaining = max(permissions.max_daily_orders_per_team - account.orders_today, 0)
    if len(execution) > remaining:
        execution.sort(key=lambda r: r.proposal.confidence, reverse=True)
        for routed in execution[remaining:]:
            simulation.append(_demote(
                routed,
                f"Simulation only: team daily order cap reached "
                f"({permissions.max_daily_orders_per_team}).",
            ))
        execution = execution[:remaining]

    # Enforce the per-team daily NOTIONAL cap deterministically (Phase 7Y): walk the
    # surviving execution-eligible proposals highest-confidence first, accumulating
    # the already-used daily notional, and demote any that would push the team over
    # MAX_DAILY_NOTIONAL_PER_TEAM. Entries counted here; sell-to-close is enforced on
    # its own submission path with the same cap and policy.
    if max_daily_notional_per_team is None:
        from src.config.portfolio_limits import PortfolioLimits

        max_daily_notional_per_team = PortfolioLimits.from_env().max_daily_notional_per_team

    if max_daily_notional_per_team and max_daily_notional_per_team > 0:
        from src.competition.daily_notional import (
            cap_rejection_reason,
            proposal_order_notional,
            would_exceed_cap,
        )

        execution.sort(key=lambda r: r.proposal.confidence, reverse=True)
        used = float(account.daily_notional_today or 0.0)
        kept: list[RoutedProposal] = []
        for routed in execution:
            nxt = proposal_order_notional(routed.decision, routed.proposal)
            if would_exceed_cap(used, nxt, max_daily_notional_per_team):
                simulation.append(_demote(
                    routed, cap_rejection_reason(used, nxt, max_daily_notional_per_team)
                ))
            else:
                used += nxt
                kept.append(routed)
        execution = kept

    return RoutingResult(
        execution_eligible=execution,
        simulation_only=simulation,
        rejected=rejected,
    )
