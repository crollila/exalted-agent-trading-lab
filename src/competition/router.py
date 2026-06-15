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


def route_proposals(
    proposals: list[CompetitionProposal],
    permissions: TradingPermissions,
    account: AccountContext,
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
        kept = execution[:remaining]
        demoted = execution[remaining:]
        for routed in demoted:
            note = (
                f"Simulation only: team daily order cap reached "
                f"({permissions.max_daily_orders_per_team})."
            )
            new_decision = AdvancedRiskDecision(
                proposal_id=routed.decision.proposal_id,
                proposal_type=routed.decision.proposal_type,
                level=routed.decision.level,
                route=Route.SIMULATION_ONLY,
                approved=False,
                reasons=[note],
                approved_quantity=routed.decision.approved_quantity,
                approved_contracts=routed.decision.approved_contracts,
                approved_notional=routed.decision.approved_notional,
                premium_at_risk=routed.decision.premium_at_risk,
            )
            simulation.append(RoutedProposal(proposal=routed.proposal, decision=new_decision))
        execution = kept

    return RoutingResult(
        execution_eligible=execution,
        simulation_only=simulation,
        rejected=rejected,
    )
