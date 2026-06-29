"""Gated paper execution bridge (Part 6 wiring).

This is the *only* place that turns an approved, execution-eligible proposal into
a broker order. It is reached exclusively from the gated Run Cycle path — never
from chat, the Agent Hub, ask commands, or the UI.

Every attempted broker submission is logged. Every skipped unsupported adapter
path is logged. Nothing here fakes a successful broker order: in dry-run mode it
records ``submitted=False``; in live-paper mode it delegates to the kill-switch
guarded broker methods and records the real outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.brokers.options_adapter import OptionsAdapterNotConfigured, OptionsExecutionRefused
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.competition.proposals import CompetitionProposal, ProposalType
from src.competition.router import RoutedProposal
from src.safety.kill_switch import KillSwitchEngaged, is_engaged


# Broker failure categories surfaced to attribution + the Portfolio Manager.
FAILURE_INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
FAILURE_WASH_TRADE = "wash_trade"
FAILURE_BROKER_ERROR = "broker_error"
FAILURE_UNKNOWN = "unknown"


def classify_broker_error(exc: Exception) -> tuple[str, str, str | None]:
    """Classify a broker submission failure into (category, reason, code).

    Detection is text-based (Alpaca raises ``APIError`` with a message and an
    optional numeric ``code``). Unknown failures fall back to ``broker_error``.
    """

    reason = str(exc).strip() or exc.__class__.__name__
    low = reason.lower()
    code = None
    raw_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if raw_code is not None:
        code = str(raw_code)

    if "buying power" in low or "insufficient" in low and "power" in low:
        category = FAILURE_INSUFFICIENT_BUYING_POWER
    elif "wash" in low:
        category = FAILURE_WASH_TRADE
    else:
        category = FAILURE_BROKER_ERROR
    return category, reason, code


@dataclass(frozen=True)
class ExecutionRecord:
    proposal_id: str
    proposal_type: str
    symbol: str
    submitted: bool
    dry_run: bool
    detail: str
    broker_response: Any | None = None
    broker_rejected: bool = False
    broker_reject_reason: str | None = None
    broker_reject_code: str | None = None
    failure_category: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "symbol": self.symbol,
            "submitted": self.submitted,
            "dry_run": self.dry_run,
            "detail": self.detail,
            "broker_rejected": self.broker_rejected,
            "broker_reject_reason": self.broker_reject_reason,
            "broker_reject_code": self.broker_reject_code,
            "failure_category": self.failure_category,
        }


def build_order_request(routed: RoutedProposal, dry_run: bool) -> OrderRequest:
    """Deterministically build an OrderRequest from an approved routed proposal."""

    proposal = routed.proposal
    decision = routed.decision
    if not decision.approved:
        raise ValueError("Only execution-eligible (approved) proposals can build orders.")

    pt = proposal.proposal_type

    if proposal.is_option:
        contracts = decision.approved_contracts or 0
        if contracts < 1:
            raise ValueError("Approved options decision must include at least one contract.")
        return OrderRequest(
            proposal_id=proposal.proposal_id,
            symbol=proposal.underlying or proposal.symbol,
            action=TradeAction.BUY,  # long-only / buy-to-open defined risk
            asset_class=AssetClass.OPTION,
            quantity=float(contracts),
            contracts=contracts,
            option_symbol=proposal.underlying or proposal.symbol,
            option_contract={
                "legs": [leg.model_dump(mode="json") for leg in proposal.legs],
                "expiration": proposal.expiration.isoformat() if proposal.expiration else None,
            },
            dry_run=dry_run,
            risk_approved=True,
        )

    quantity = decision.approved_quantity or 0.0
    if quantity < 1:
        raise ValueError("Approved stock decision must include a positive quantity.")

    is_short = pt in (ProposalType.STOCK_SHORT, ProposalType.MARGIN_STOCK_SHORT)
    is_margin = pt in (ProposalType.MARGIN_STOCK_LONG, ProposalType.MARGIN_STOCK_SHORT)
    action = TradeAction.SELL if is_short else TradeAction.BUY

    return OrderRequest(
        proposal_id=proposal.proposal_id,
        symbol=proposal.symbol,
        action=action,
        asset_class=AssetClass.STOCK,
        quantity=quantity,
        short=is_short,
        margin=is_margin,
        dry_run=dry_run,
        risk_approved=True,
    )


def _dispatch(client: AlpacaClientWrapper, order: OrderRequest) -> Any:
    if order.asset_class == AssetClass.OPTION:
        return client.submit_paper_option_order(order)
    if order.short:
        return client.submit_paper_short_order(order)
    if order.margin:
        return client.submit_paper_margin_order(order)
    return client.submit_paper_order(order)


def execute_routed_proposals(
    routed_proposals: list[RoutedProposal],
    *,
    client: AlpacaClientWrapper | None,
    dry_run: bool,
    kill_switch_path: str | None = None,
    daily_notional_used: float = 0.0,
    max_daily_notional: float | None = None,
) -> list[ExecutionRecord]:
    """Execute a batch of execution-eligible proposals through the gated path.

    Phase 7Y: a final deterministic daily-notional gate runs immediately before
    each real submission. ``daily_notional_used`` seeds the running total from the
    broker-reconciled usage so far today; each successful submit increments it so
    the next order sees updated usage (post-submit reconciliation). An order that
    would exceed ``max_daily_notional`` is rejected (not submitted) with the exact
    cap reason. Entries and sell-to-close share this cap and policy.
    """

    from src.competition.daily_notional import (
        cap_rejection_reason,
        proposal_order_notional,
        would_exceed_cap,
    )

    if max_daily_notional is None:
        from src.config.portfolio_limits import PortfolioLimits

        max_daily_notional = PortfolioLimits.from_env().max_daily_notional_per_team

    records: list[ExecutionRecord] = []
    running_used = float(daily_notional_used or 0.0)

    for routed in routed_proposals:
        proposal = routed.proposal
        symbol = proposal.underlying or proposal.symbol

        if is_engaged(kill_switch_path):
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=dry_run,
                    detail="Kill switch engaged; broker submission blocked.",
                )
            )
            continue

        order = build_order_request(routed, dry_run=dry_run)

        if dry_run:
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=True,
                    detail="Dry-run: order built and logged, not submitted.",
                )
            )
            continue

        if client is None:
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=False,
                    detail="No broker client configured; submission skipped.",
                )
            )
            continue

        # Phase 7Y: final daily-notional gate immediately before submission.
        next_notional = proposal_order_notional(routed.decision, proposal)
        if would_exceed_cap(running_used, next_notional, max_daily_notional):
            reason = cap_rejection_reason(running_used, next_notional, max_daily_notional)
            print(f"[risk] {symbol}: {reason}")
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=False,
                    detail=reason,
                    failure_category="daily_notional_cap",
                )
            )
            continue

        try:
            response = _dispatch(client, order)
            running_used += next_notional  # reconcile so the next order sees updated usage
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=True,
                    dry_run=False,
                    detail="Submitted to Alpaca paper.",
                    broker_response=response,
                )
            )
        except OptionsAdapterNotConfigured as exc:
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=False,
                    detail=f"Options adapter not configured: {exc}",
                )
            )
        except OptionsExecutionRefused as exc:
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=False,
                    detail=f"Options execution refused: {exc}",
                )
            )
        except KillSwitchEngaged as exc:
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=False,
                    detail=f"Kill switch engaged: {exc}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - log any broker/network failure; never fake a fill
            category, reason, code = classify_broker_error(exc)
            records.append(
                ExecutionRecord(
                    proposal_id=proposal.proposal_id,
                    proposal_type=proposal.proposal_type.value,
                    symbol=symbol,
                    submitted=False,
                    dry_run=False,
                    detail=f"Broker rejected submission ({category}): {reason}",
                    broker_rejected=True,
                    broker_reject_reason=reason,
                    broker_reject_code=code,
                    failure_category=category,
                )
            )

    return records
