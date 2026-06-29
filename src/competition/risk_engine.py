"""Advanced deterministic risk engine (Part 5).

Every advanced paper proposal (shorting, margin, options) is evaluated here.
The engine is fully deterministic: given the same proposal, permissions, and
account context it always returns the same decision and the same approved size.

Crucially, the approved quantity / contract count is *computed here*, never taken
from the LLM-provided fields. LLM-supplied sizing intent (``target_weight``,
``contracts``) is treated as a request that the engine bounds, recomputes, and may
reject.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from src.competition.proposals import CompetitionProposal, ProposalType
from src.config.permissions import PermissionLevel, TradingPermissions


class Route(str, Enum):
    EXECUTION_ELIGIBLE = "execution_eligible"
    SIMULATION_ONLY = "simulation_only"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AccountContext:
    """Deterministic snapshot of account state used for risk math.

    Exposures are expressed as fractions of equity (e.g. 0.30 == 30% of equity).
    ``daily_loss_pct`` is a positive fraction representing realized+unrealized
    loss so far today (0.0 == flat/up).
    """

    equity: float
    cash: float = 0.0
    buying_power: float | None = None
    current_gross_exposure: float = 0.0
    current_net_exposure: float = 0.0
    current_short_exposure: float = 0.0
    daily_loss_pct: float = 0.0
    orders_today: int = 0
    # Gross paper notional already submitted for the current ET trading date
    # (entries + sell-to-close), reconciled from broker orders. Used to enforce
    # MAX_DAILY_NOTIONAL_PER_TEAM before each new order.
    daily_notional_today: float = 0.0
    as_of: date | None = None


@dataclass(frozen=True)
class AdvancedRiskDecision:
    proposal_id: str
    proposal_type: ProposalType
    level: int
    route: Route
    approved: bool
    reasons: list[str]
    approved_quantity: float | None = None
    approved_contracts: int | None = None
    approved_notional: float | None = None
    premium_at_risk: float | None = None
    gross_exposure_after: float | None = None
    net_exposure_after: float | None = None
    short_exposure_after: float | None = None
    forced_deleveraging_required: bool = False
    borrow_assumption_logged: str | None = None
    greeks_logged: dict[str, float] | None = None
    greeks_available: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type.value,
            "level": self.level,
            "route": self.route.value,
            "approved": self.approved,
            "reasons": list(self.reasons),
            "approved_quantity": self.approved_quantity,
            "approved_contracts": self.approved_contracts,
            "approved_notional": self.approved_notional,
            "premium_at_risk": self.premium_at_risk,
            "gross_exposure_after": self.gross_exposure_after,
            "net_exposure_after": self.net_exposure_after,
            "short_exposure_after": self.short_exposure_after,
            "forced_deleveraging_required": self.forced_deleveraging_required,
            "borrow_assumption_logged": self.borrow_assumption_logged,
            "greeks_available": self.greeks_available,
            "greeks_logged": self.greeks_logged,
            "notes": list(self.notes),
        }


def _level_for(proposal: CompetitionProposal) -> int:
    if proposal.is_option:
        return PermissionLevel.PAPER_OPTIONS
    if proposal.is_margin:
        return PermissionLevel.PAPER_MARGIN
    if proposal.is_short:
        return PermissionLevel.PAPER_SHORTING
    return PermissionLevel.PAPER_STOCKS


def _level_enabled(level: int, permissions: TradingPermissions) -> bool:
    return {
        PermissionLevel.PAPER_STOCKS: permissions.stocks_enabled(),
        PermissionLevel.PAPER_MARGIN: permissions.margin_enabled(),
        PermissionLevel.PAPER_SHORTING: permissions.shorting_enabled(),
        PermissionLevel.PAPER_OPTIONS: permissions.options_enabled(),
    }[level]


def evaluate_proposal(
    proposal: CompetitionProposal,
    permissions: TradingPermissions,
    account: AccountContext,
) -> AdvancedRiskDecision:
    """Deterministically evaluate a single proposal."""

    level = _level_for(proposal)

    # Hard precondition: paper mode. Live mode is never eligible.
    if not permissions.is_paper:
        return AdvancedRiskDecision(
            proposal_id=proposal.proposal_id,
            proposal_type=proposal.proposal_type,
            level=level,
            route=Route.REJECTED,
            approved=False,
            reasons=[f"Rejected: TRADING_MODE must be paper (got '{permissions.trading_mode}')."],
        )

    # Permission flag disabled → simulation-only (still researched, never executed).
    if not _level_enabled(level, permissions):
        return AdvancedRiskDecision(
            proposal_id=proposal.proposal_id,
            proposal_type=proposal.proposal_type,
            level=level,
            route=Route.SIMULATION_ONLY,
            approved=False,
            reasons=[f"Simulation only: permission level {level} is disabled by config."],
        )

    if proposal.is_option:
        return _evaluate_option(proposal, permissions, account, level)
    if proposal.is_short:
        return _evaluate_short(proposal, permissions, account, level)
    if proposal.is_margin:
        return _evaluate_margin_long(proposal, permissions, account, level)
    return _evaluate_stock_long(proposal, permissions, account, level)


def _reject(proposal, level, reasons) -> AdvancedRiskDecision:
    return AdvancedRiskDecision(
        proposal_id=proposal.proposal_id,
        proposal_type=proposal.proposal_type,
        level=level,
        route=Route.REJECTED,
        approved=False,
        reasons=reasons,
    )


def _daily_loss_breached(permissions: TradingPermissions, account: AccountContext) -> bool:
    return account.daily_loss_pct >= permissions.max_daily_loss_pct_per_team


# --- Level 1: paper stock long ---


def _evaluate_stock_long(proposal, permissions, account, level) -> AdvancedRiskDecision:
    reasons: list[str] = []
    if account.equity <= 0:
        return _reject(proposal, level, ["Rejected: account equity must be positive."])
    if _daily_loss_breached(permissions, account):
        reasons.append("Rejected: team daily loss cap breached.")

    weight = min(proposal.target_weight or 0.0, permissions.max_position_weight)
    notional = weight * account.equity
    quantity = math.floor(notional / proposal.estimated_price)
    if quantity < 1:
        reasons.append("Rejected: computed quantity rounds below one share.")

    if reasons:
        return _reject(proposal, level, reasons)

    approved_notional = quantity * proposal.estimated_price
    return AdvancedRiskDecision(
        proposal_id=proposal.proposal_id,
        proposal_type=proposal.proposal_type,
        level=level,
        route=Route.EXECUTION_ELIGIBLE,
        approved=True,
        reasons=["Approved: paper stock long within position weight cap."],
        approved_quantity=float(quantity),
        approved_notional=approved_notional,
        net_exposure_after=account.current_net_exposure + approved_notional / account.equity,
        gross_exposure_after=account.current_gross_exposure + approved_notional / account.equity,
    )


# --- Level 2: paper shorting ---


def _evaluate_short(proposal, permissions, account, level) -> AdvancedRiskDecision:
    reasons: list[str] = []
    if account.equity <= 0:
        return _reject(proposal, level, ["Rejected: account equity must be positive."])

    if not proposal.symbol:
        reasons.append("Rejected: short proposal missing symbol.")
    if proposal.estimated_price <= 0:
        reasons.append("Rejected: short proposal missing valid price.")
    if not proposal.thesis:
        reasons.append("Rejected: short proposal missing thesis.")
    if proposal.max_loss_estimate is None:
        reasons.append("Rejected: short proposal missing max loss estimate.")
    if proposal.stop_level is None:
        reasons.append("Rejected: short proposal missing stop/invalidation level.")
    if not (proposal.borrow_availability_assumption and proposal.borrow_availability_assumption.strip()):
        reasons.append("Rejected: short proposal missing borrow/availability assumption.")
    if _daily_loss_breached(permissions, account):
        reasons.append("Rejected: team daily loss cap breached.")

    # Deterministic sizing bounded by single-short weight.
    weight = min(
        proposal.target_weight or 0.0,
        permissions.max_single_short_weight,
        permissions.max_position_weight,
    )
    notional = weight * account.equity
    quantity = math.floor(notional / proposal.estimated_price)
    if quantity < 1:
        reasons.append("Rejected: computed short quantity rounds below one share.")

    approved_notional = quantity * proposal.estimated_price
    single_short_weight = approved_notional / account.equity if account.equity > 0 else 1.0
    short_exposure_after = account.current_short_exposure + single_short_weight

    if single_short_weight > permissions.max_single_short_weight + 1e-9:
        reasons.append("Rejected: single short exceeds max single short weight.")
    if short_exposure_after > permissions.max_short_exposure + 1e-9:
        reasons.append("Rejected: projected short exposure exceeds max short exposure.")

    if reasons:
        return _reject(proposal, level, reasons)

    borrow_log = (
        f"borrow_available_assumption={proposal.borrow_availability_assumption!r}; "
        f"stop_level={proposal.stop_level}; max_loss_estimate={proposal.max_loss_estimate}"
    )
    return AdvancedRiskDecision(
        proposal_id=proposal.proposal_id,
        proposal_type=proposal.proposal_type,
        level=level,
        route=Route.EXECUTION_ELIGIBLE,
        approved=True,
        reasons=["Approved: paper short within exposure caps."],
        approved_quantity=float(quantity),
        approved_notional=approved_notional,
        short_exposure_after=short_exposure_after,
        gross_exposure_after=account.current_gross_exposure + single_short_weight,
        net_exposure_after=account.current_net_exposure - single_short_weight,
        borrow_assumption_logged=borrow_log,
    )


# --- Level 3: paper margin (long or short via leverage) ---


def _evaluate_margin_long(proposal, permissions, account, level) -> AdvancedRiskDecision:
    reasons: list[str] = []
    if account.equity <= 0:
        return _reject(proposal, level, ["Rejected: account equity must be positive."])
    if account.buying_power is None:
        reasons.append("Rejected: margin proposal requires buying power/equity fields.")
    if _daily_loss_breached(permissions, account):
        reasons.append("Rejected: team daily loss cap breached.")

    is_short_side = proposal.proposal_type == ProposalType.MARGIN_STOCK_SHORT
    if is_short_side:
        if not (proposal.borrow_availability_assumption and proposal.borrow_availability_assumption.strip()):
            reasons.append("Rejected: margin short requires borrow/availability assumption.")
        if proposal.stop_level is None:
            reasons.append("Rejected: margin short requires stop/invalidation level.")
        if proposal.max_loss_estimate is None:
            reasons.append("Rejected: margin short requires max loss estimate.")

    weight = min(proposal.target_weight or 0.0, permissions.max_position_weight)
    notional = weight * account.equity
    quantity = math.floor(notional / proposal.estimated_price)
    if quantity < 1:
        reasons.append("Rejected: computed quantity rounds below one share.")

    approved_notional = quantity * proposal.estimated_price
    delta_weight = approved_notional / account.equity if account.equity > 0 else 1.0

    # Forced deleveraging: already over a cap before adding anything.
    forced = (
        account.current_gross_exposure > permissions.max_gross_exposure + 1e-9
        or account.current_net_exposure > permissions.max_net_exposure + 1e-9
    )
    if forced:
        reasons.append("Rejected: forced deleveraging active; account already over exposure caps.")

    gross_after = account.current_gross_exposure + delta_weight
    net_after = account.current_net_exposure + (-delta_weight if is_short_side else delta_weight)

    if gross_after > permissions.max_gross_exposure + 1e-9:
        reasons.append("Rejected: projected gross exposure exceeds max gross exposure.")
    if abs(net_after) > permissions.max_net_exposure + 1e-9:
        reasons.append("Rejected: projected net exposure exceeds max net exposure.")

    if reasons:
        return AdvancedRiskDecision(
            proposal_id=proposal.proposal_id,
            proposal_type=proposal.proposal_type,
            level=level,
            route=Route.REJECTED,
            approved=False,
            reasons=reasons,
            forced_deleveraging_required=forced,
        )

    return AdvancedRiskDecision(
        proposal_id=proposal.proposal_id,
        proposal_type=proposal.proposal_type,
        level=level,
        route=Route.EXECUTION_ELIGIBLE,
        approved=True,
        reasons=["Approved: paper margin within gross/net exposure caps."],
        approved_quantity=float(quantity),
        approved_notional=approved_notional,
        gross_exposure_after=gross_after,
        net_exposure_after=net_after,
        short_exposure_after=(
            account.current_short_exposure + delta_weight if is_short_side else account.current_short_exposure
        ),
        forced_deleveraging_required=False,
        borrow_assumption_logged=(
            proposal.borrow_availability_assumption if is_short_side else None
        ),
    )


# --- Level 4: paper options ---


def _evaluate_option(proposal, permissions, account, level) -> AdvancedRiskDecision:
    reasons: list[str] = []
    if account.equity <= 0:
        return _reject(proposal, level, ["Rejected: account equity must be positive."])

    if _daily_loss_breached(permissions, account):
        reasons.append("Rejected: team daily loss cap breached.")

    # No naked short options / uncovered legs unless explicitly allowed.
    if proposal.has_naked_short_leg and not permissions.allow_naked_options:
        reasons.append("Rejected: naked/uncovered short option legs are disabled.")

    # Defined-risk requirement: max loss must be present and computable.
    if proposal.max_loss is None:
        reasons.append("Rejected: option proposal missing max loss (undefined-risk).")
    if proposal.net_premium_per_contract is None or proposal.contracts is None:
        reasons.append("Rejected: option premium cannot be calculated.")
    if not (proposal.assignment_exercise_risk_note and proposal.assignment_exercise_risk_note.strip()):
        reasons.append("Rejected: option proposal missing assignment/exercise risk note.")

    # DTE checks.
    as_of = account.as_of or date.today()
    dte = proposal.dte(as_of)
    if dte <= 0:
        reasons.append("Rejected: 0DTE options are disabled.")
    elif dte < permissions.min_options_dte:
        reasons.append(f"Rejected: option DTE {dte} below minimum {permissions.min_options_dte}.")

    # Contract count cap.
    requested_contracts = proposal.contracts or 0
    if requested_contracts > permissions.max_options_contracts_per_trade:
        reasons.append("Rejected: option contract count exceeds max contracts per trade.")

    # Premium-at-risk budget (deterministically recomputed, never trusted from LLM).
    premium_at_risk: float | None = None
    if proposal.net_premium_per_contract is not None and proposal.contracts is not None:
        premium_at_risk = proposal.computed_premium_at_risk()
        budget = permissions.max_options_premium_at_risk * account.equity
        if premium_at_risk > budget + 1e-9:
            reasons.append("Rejected: option premium at risk exceeds max premium at risk.")

    greeks_available = bool(proposal.greeks_available and proposal.greeks)
    greeks_logged = proposal.greeks if greeks_available else None

    if reasons:
        return AdvancedRiskDecision(
            proposal_id=proposal.proposal_id,
            proposal_type=proposal.proposal_type,
            level=level,
            route=Route.REJECTED,
            approved=False,
            reasons=reasons,
            premium_at_risk=premium_at_risk,
            greeks_available=greeks_available,
            greeks_logged=greeks_logged,
            notes=([] if greeks_available else ["Greeks unavailable; logged as unavailable."]),
        )

    return AdvancedRiskDecision(
        proposal_id=proposal.proposal_id,
        proposal_type=proposal.proposal_type,
        level=level,
        route=Route.EXECUTION_ELIGIBLE,
        approved=True,
        reasons=["Approved: defined-risk options within premium-at-risk and DTE caps."],
        approved_contracts=requested_contracts,
        premium_at_risk=premium_at_risk,
        greeks_available=greeks_available,
        greeks_logged=greeks_logged,
        notes=([] if greeks_available else ["Greeks unavailable; logged as unavailable."]),
    )
