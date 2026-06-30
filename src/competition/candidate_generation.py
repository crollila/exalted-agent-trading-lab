"""Candidate-generation auditability (Phase 7Z).

Splits the proposal pipeline into clear, recorded stages so it is always possible
to say *exactly* why a completed cycle produced zero proposals — without forcing a
trade or adding a mandatory minimum trade count.

Stages recorded:

1. portfolio manager allowance
2. candidate-generation allowance
3. model/provider call outcome
4. parsed proposal count
5. routing result
6. deterministic risk result

A completed cycle that submits no order is assigned **exactly one** machine
readable ``no_trade_reason_class``; it is never left null. A healthy
zero-position, full-cash account is allowed to reach candidate generation unless a
*current* deterministic condition blocks it — historical losses or a stale
playbook item alone must never indefinitely force ``max_new=0``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Exactly-one no-trade reason classes (stable strings).
NO_CURRENT_SIGNAL = "no_current_signal"
PORTFOLIO_MANAGER_HOLD = "portfolio_manager_hold"
CANDIDATE_GENERATION_DISABLED = "candidate_generation_disabled"
PROVIDER_FAILURE = "provider_failure"
INVALID_MODEL_OUTPUT = "invalid_model_output"
MODEL_ZERO_CANDIDATES = "model_zero_candidates"
RISK_REJECTED = "risk_rejected"
DAILY_CAP_REACHED = "daily_cap_reached"
AUTONOMY_DISABLED = "autonomy_disabled"
ACCOUNT_STATE_UNAVAILABLE = "account_state_unavailable"
LIVE_PORTFOLIO_HEALTH_BLOCK = "live_portfolio_health_block"

ALL_NO_TRADE_REASON_CLASSES = (
    NO_CURRENT_SIGNAL,
    PORTFOLIO_MANAGER_HOLD,
    CANDIDATE_GENERATION_DISABLED,
    PROVIDER_FAILURE,
    INVALID_MODEL_OUTPUT,
    MODEL_ZERO_CANDIDATES,
    RISK_REJECTED,
    DAILY_CAP_REACHED,
    AUTONOMY_DISABLED,
    ACCOUNT_STATE_UNAVAILABLE,
    LIVE_PORTFOLIO_HEALTH_BLOCK,
)

# Provider/model call outcome categories (never secrets, never raw prompt text).
PROVIDER_OUTCOME_NOT_CALLED = "not_called"
PROVIDER_OUTCOME_SUCCESS = "success"
PROVIDER_OUTCOME_FAILURE = "provider_failure"
PROVIDER_OUTCOME_INVALID_OUTPUT = "invalid_model_output"
PROVIDER_OUTCOME_ZERO_CANDIDATES = "model_zero_candidates"

# Execution / submission block reasons — these describe a cycle that PRODUCED
# execution-eligible (risk-approved) proposals but submitted no order. They are
# strictly separate from ``no_trade_reason_class``: a cycle is EITHER a genuine
# no-trade (no executable candidate) OR a submission block (executable candidate
# not submitted), never both.
EXEC_DRY_RUN = "dry_run"
EXEC_KILL_SWITCH_ENGAGED = "kill_switch_engaged"
EXEC_TEAM_AUTONOMY_DISABLED = "team_autonomy_disabled"
EXEC_BROKER_CLIENT_UNAVAILABLE = "broker_client_unavailable"
EXEC_PROVIDER_FAILURE_BEFORE_EXECUTION = "provider_failure_before_execution"
EXEC_REVIEW_ONLY = "review_only"
EXEC_PAPER_SUBMISSION_NOT_ATTEMPTED = "paper_submission_not_attempted"

ALL_EXECUTION_BLOCK_REASONS = (
    EXEC_DRY_RUN,
    EXEC_KILL_SWITCH_ENGAGED,
    EXEC_TEAM_AUTONOMY_DISABLED,
    EXEC_BROKER_CLIENT_UNAVAILABLE,
    EXEC_PROVIDER_FAILURE_BEFORE_EXECUTION,
    EXEC_REVIEW_ONLY,
    EXEC_PAPER_SUBMISSION_NOT_ATTEMPTED,
)


@dataclass
class CandidateGenerationOutcome:
    """Machine-readable record of the candidate-generation pipeline for a cycle."""

    team_id: str
    # Stage allowances.
    portfolio_manager_allowed: bool
    candidate_generation_allowed: bool
    reached_candidate_generation: bool
    # Provider/model call (names only; never secrets or raw prompt).
    provider_called: bool
    provider_name: str | None
    model_name: str | None
    provider_outcome: str
    provider_failure_category: str | None
    # Parsed proposals + routing + deterministic risk.
    parsed_proposal_count: int
    routed_execution_eligible: int
    routed_simulation_only: int
    routed_rejected: int
    orders_submitted: int
    # Exactly one of these is set when no order was submitted (both None on submit):
    #   * no_trade_reason_class — a GENUINE no-trade (no executable candidate emerged).
    #   * execution_block_reason — executable candidate(s) existed but were not
    #     submitted (dry-run / kill switch / review-only / autonomy off / no client).
    # They are mutually exclusive; no_trade_reason_class NEVER describes a cycle that
    # produced execution-eligible proposals.
    no_trade_reason_class: str | None
    execution_block_reason: str | None = None
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_no_trade(self) -> bool:
        """True only for a GENUINE no-trade (not a submission/execution block)."""

        return self.no_trade_reason_class is not None

    @property
    def is_submission_blocked(self) -> bool:
        """True when executable candidates existed but no order was submitted."""

        return self.execution_block_reason is not None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_candidate_outcome(
    *,
    team_id: str,
    account_available: bool,
    execution_config_enabled: bool = True,
    health_block: bool,
    health_block_reason: str | None = None,
    portfolio_manager_allows_new: bool,
    portfolio_manager_is_genuine_hold: bool,
    candidate_generation_enabled: bool = True,
    provider_called: bool,
    provider_name: str | None = None,
    model_name: str | None = None,
    provider_failed: bool = False,
    provider_failure_category: str | None = None,
    invalid_model_output: bool = False,
    parsed_proposal_count: int,
    routed_execution_eligible: int,
    routed_simulation_only: int,
    routed_rejected: int,
    orders_submitted: int,
    daily_cap_reached: bool = False,
    risk_approved_count: int | None = None,
    dry_run: bool = False,
    kill_switch_engaged: bool = False,
    review_only: bool = False,
    team_autonomy_enabled: bool = True,
    broker_client_available: bool = True,
) -> CandidateGenerationOutcome:
    """Deterministically classify a cycle's candidate-generation outcome.

    Two mutually exclusive outcomes are possible when no order was submitted:

    * ``no_trade_reason_class`` — a GENUINE no-trade: no execution-eligible
      candidate ever emerged (account unavailable, config off, health block,
      provider failure, invalid output, model-zero, PM hold, risk rejection, cap,
      or no current signal).
    * ``execution_block_reason`` — execution-eligible candidate(s) DID emerge but
      no paper order was submitted (dry-run / kill switch / review-only / team
      autonomy off / no broker client). ``no_trade_reason_class`` is then ``None``
      so it never describes a cycle that produced execution-eligible proposals.

    ``execution_config_enabled`` is paper-mode AND stocks-enabled (a config gate
    that determines whether any execution-eligible route exists at all). Per-team
    autonomy is reported via ``team_autonomy_enabled`` (diagnostics + the
    submission-block taxonomy) and never silently widens or narrows a real gate.
    """

    # Provider call outcome category.
    if not provider_called:
        provider_outcome = PROVIDER_OUTCOME_NOT_CALLED
    elif provider_failed:
        provider_outcome = PROVIDER_OUTCOME_FAILURE
    elif invalid_model_output and parsed_proposal_count == 0:
        provider_outcome = PROVIDER_OUTCOME_INVALID_OUTPUT
    elif parsed_proposal_count == 0:
        provider_outcome = PROVIDER_OUTCOME_ZERO_CANDIDATES
    else:
        provider_outcome = PROVIDER_OUTCOME_SUCCESS

    # Candidate generation is ALLOWED by the pre-generation gates only: a live
    # account, the execution config on, current health not blocking, and config
    # enabled. It deliberately does NOT depend on the PM's new-order allowance nor
    # on execution-mode flags (dry-run/kill-switch/review-only) — generation still
    # happens in those modes — so a healthy account always reaches generation.
    candidate_generation_allowed = (
        account_available
        and execution_config_enabled
        and not health_block
        and candidate_generation_enabled
    )
    reached_candidate_generation = candidate_generation_allowed and provider_called

    outcome = CandidateGenerationOutcome(
        team_id=team_id,
        portfolio_manager_allowed=portfolio_manager_allows_new,
        candidate_generation_allowed=candidate_generation_allowed,
        reached_candidate_generation=reached_candidate_generation,
        provider_called=provider_called,
        provider_name=provider_name,
        model_name=model_name,
        provider_outcome=provider_outcome,
        provider_failure_category=provider_failure_category,
        parsed_proposal_count=parsed_proposal_count,
        routed_execution_eligible=routed_execution_eligible,
        routed_simulation_only=routed_simulation_only,
        routed_rejected=routed_rejected,
        orders_submitted=orders_submitted,
        no_trade_reason_class=None,
    )

    if orders_submitted > 0:
        outcome.detail = f"{orders_submitted} paper order(s) submitted."
        return outcome

    # Hard pre-conditions invalidate the whole cycle and take precedence over any
    # (untrusted) execution-eligibility computed against a fallback account: an
    # unavailable account or a non-paper/stocks-off config is a GENUINE no-trade.
    if not account_available:
        outcome.no_trade_reason_class = ACCOUNT_STATE_UNAVAILABLE
        outcome.detail = "Live account unavailable; cycle could not ground on current broker state."
        return outcome
    if not execution_config_enabled:
        outcome.no_trade_reason_class = AUTONOMY_DISABLED
        outcome.detail = (
            "Execution config disabled (non-paper mode or stocks disabled); no execution-eligible route."
        )
        return outcome

    # Did the cycle produce execution-eligible (risk-approved) candidates that
    # simply were not submitted? Review-only demotes eligible proposals to the
    # simulation bucket, so fall back to the pre-portfolio-gate approved count.
    pre_gate_eligible = risk_approved_count if risk_approved_count is not None else routed_execution_eligible
    had_executable_candidates = routed_execution_eligible > 0 or (review_only and pre_gate_eligible > 0)

    if had_executable_candidates:
        # SUBMISSION/EXECUTION BLOCK — explicitly NOT a no-trade.
        block, detail = _classify_execution_block(
            kill_switch_engaged=kill_switch_engaged,
            review_only=review_only,
            dry_run=dry_run,
            team_autonomy_enabled=team_autonomy_enabled,
            broker_client_available=broker_client_available,
        )
        outcome.execution_block_reason = block
        outcome.detail = detail
        return outcome

    # --- Genuine no-trade: assign exactly one no_trade_reason_class. ---
    reason, detail = _classify_no_trade_reason(
        account_available=account_available,
        execution_config_enabled=execution_config_enabled,
        health_block=health_block,
        health_block_reason=health_block_reason,
        candidate_generation_enabled=candidate_generation_enabled,
        portfolio_manager_allows_new=portfolio_manager_allows_new,
        portfolio_manager_is_genuine_hold=portfolio_manager_is_genuine_hold,
        provider_called=provider_called,
        provider_failed=provider_failed,
        invalid_model_output=invalid_model_output,
        parsed_proposal_count=parsed_proposal_count,
        routed_execution_eligible=routed_execution_eligible,
        routed_simulation_only=routed_simulation_only,
        routed_rejected=routed_rejected,
        daily_cap_reached=daily_cap_reached,
    )
    outcome.no_trade_reason_class = reason
    outcome.detail = detail
    return outcome


def _classify_execution_block(
    *,
    kill_switch_engaged: bool,
    review_only: bool,
    dry_run: bool,
    team_autonomy_enabled: bool,
    broker_client_available: bool,
) -> tuple[str, str]:
    """Stable precedence for why execution-eligible candidates were not submitted."""

    if kill_switch_engaged:
        return EXEC_KILL_SWITCH_ENGAGED, "Execution-eligible proposals blocked: kill switch engaged."
    if review_only:
        return EXEC_REVIEW_ONLY, "Execution-eligible proposals not submitted: review-only cycle (advisory)."
    if dry_run:
        return EXEC_DRY_RUN, "Execution-eligible proposals not submitted: dry-run mode."
    if not team_autonomy_enabled:
        return EXEC_TEAM_AUTONOMY_DISABLED, "Execution-eligible proposals not submitted: team autonomy disabled."
    if not broker_client_available:
        return EXEC_BROKER_CLIENT_UNAVAILABLE, "Execution-eligible proposals not submitted: broker client unavailable."
    return EXEC_PAPER_SUBMISSION_NOT_ATTEMPTED, (
        "Execution-eligible proposals were approved but no paper order was submitted."
    )


def _classify_no_trade_reason(
    *,
    account_available: bool,
    execution_config_enabled: bool,
    health_block: bool,
    health_block_reason: str | None,
    candidate_generation_enabled: bool,
    portfolio_manager_allows_new: bool,
    portfolio_manager_is_genuine_hold: bool,
    provider_called: bool,
    provider_failed: bool,
    invalid_model_output: bool,
    parsed_proposal_count: int,
    routed_execution_eligible: int,
    routed_simulation_only: int,
    routed_rejected: int,
    daily_cap_reached: bool,
) -> tuple[str, str]:
    """Stable precedence for the single GENUINE no-trade reason class.

    Only reached when the cycle produced NO execution-eligible candidate. An
    execution-eligible-but-not-submitted cycle is handled earlier as a submission
    block and never lands here.
    """

    # 1) Current state we cannot trust / are not allowed to act on.
    if not account_available:
        return ACCOUNT_STATE_UNAVAILABLE, (
            "Live account unavailable; cycle could not ground on current broker state."
        )
    if not execution_config_enabled:
        return AUTONOMY_DISABLED, (
            "Execution config disabled (non-paper mode or stocks disabled); no execution-eligible route."
        )
    if health_block:
        return LIVE_PORTFOLIO_HEALTH_BLOCK, (
            health_block_reason or "Current portfolio health blocks new-money buys (deterministic)."
        )

    # 2) Provider/model call outcome (only meaningful when generation was attempted).
    if provider_called:
        if provider_failed:
            return PROVIDER_FAILURE, "Routed model/provider call failed; no candidates produced."
        if invalid_model_output and parsed_proposal_count == 0:
            return INVALID_MODEL_OUTPUT, "Model returned output that failed validation; no usable candidates."
        if parsed_proposal_count == 0:
            return MODEL_ZERO_CANDIDATES, "Model produced zero candidates (held / proposed nothing)."

    # 3) PM/config blocked candidate generation before any model call.
    if not candidate_generation_enabled:
        return CANDIDATE_GENERATION_DISABLED, "Candidate generation disabled by config this cycle."
    if not portfolio_manager_allows_new:
        if portfolio_manager_is_genuine_hold:
            return PORTFOLIO_MANAGER_HOLD, "Portfolio manager chose hold/no-trade on current evidence."
        # PM blocked but not a genuine evidence-backed hold and no model signal.
        return NO_CURRENT_SIGNAL, "No current signal cleared review and no new-order allowance."

    # 4) Candidates existed but none became execution-eligible.
    if parsed_proposal_count > 0:
        if routed_execution_eligible == 0 and routed_rejected > 0:
            return RISK_REJECTED, "All candidates rejected by the deterministic risk engine."
        if daily_cap_reached:
            return DAILY_CAP_REACHED, "Daily order/notional cap reached; new entries demoted to simulation."
        if routed_execution_eligible == 0 and routed_simulation_only > 0:
            return DAILY_CAP_REACHED, "Candidates demoted to simulation-only (cap or portfolio gate)."

    # 5) Default: nothing cleared the bar this cycle (a healthy no-trade).
    return NO_CURRENT_SIGNAL, "No current signal cleared review; holding and observing (valid no-trade)."


__all__ = [
    "NO_CURRENT_SIGNAL",
    "PORTFOLIO_MANAGER_HOLD",
    "CANDIDATE_GENERATION_DISABLED",
    "PROVIDER_FAILURE",
    "INVALID_MODEL_OUTPUT",
    "MODEL_ZERO_CANDIDATES",
    "RISK_REJECTED",
    "DAILY_CAP_REACHED",
    "AUTONOMY_DISABLED",
    "ACCOUNT_STATE_UNAVAILABLE",
    "LIVE_PORTFOLIO_HEALTH_BLOCK",
    "ALL_NO_TRADE_REASON_CLASSES",
    "PROVIDER_OUTCOME_NOT_CALLED",
    "PROVIDER_OUTCOME_SUCCESS",
    "PROVIDER_OUTCOME_FAILURE",
    "PROVIDER_OUTCOME_INVALID_OUTPUT",
    "PROVIDER_OUTCOME_ZERO_CANDIDATES",
    "EXEC_DRY_RUN",
    "EXEC_KILL_SWITCH_ENGAGED",
    "EXEC_TEAM_AUTONOMY_DISABLED",
    "EXEC_BROKER_CLIENT_UNAVAILABLE",
    "EXEC_PROVIDER_FAILURE_BEFORE_EXECUTION",
    "EXEC_REVIEW_ONLY",
    "EXEC_PAPER_SUBMISSION_NOT_ATTEMPTED",
    "ALL_EXECUTION_BLOCK_REASONS",
    "CandidateGenerationOutcome",
    "classify_candidate_outcome",
]
