"""Deterministic sell-to-close / trim execution for LONG stock positions (Phase 7V).

This is the ONLY new path that may reduce an existing position, and it is
deliberately narrow and conservative:

* It only ever SELLS shares of an EXISTING long stock position.
* It can never open or increase a short (quantity is capped to held long shares;
  a request larger than the holding is clamped, never inverted).
* It rejects unknown/unheld symbols and non-long positions (closing a short would
  require buy-to-cover, which is intentionally NOT implemented).
* It re-reads Alpaca positions IMMEDIATELY before submission and re-validates
  against the freshly-held quantity, so a stale snapshot can never oversell.
* It honors the kill switch and paper-only controls, and logs the full decision
  chain (proposal -> recommendation -> deterministic decision -> order -> result).

No shorting, options, margin, or live trading is introduced here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.brokers.alpaca_client import AlpacaClientWrapper
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.competition.execution import classify_broker_error
from src.competition.position_review import read_position_fields
from src.config.portfolio_limits import PortfolioLimits
from src.safety.kill_switch import is_engaged

ACTION_TRIM = "trim"
ACTION_EXIT = "exit"


@dataclass(frozen=True)
class PositionActionProposal:
    """A proposal to reduce a long position. Quantity is advisory only.

    For ``exit`` the held quantity is sold in full. For ``trim`` the deterministic
    gate sells ``min(requested_qty, held)`` (or a default fraction when no explicit
    quantity is given). Source/reason are carried for the audit chain.
    """

    symbol: str
    action: str  # trim | exit
    requested_qty: float | None = None
    reason: str = ""
    source: str = "deterministic_review"
    proposal_id: str = ""


@dataclass
class SellToCloseDecision:
    symbol: str
    action: str
    approved: bool
    held_qty: float
    requested_qty: float | None
    approved_qty: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "approved": self.approved,
            "held_qty": self.held_qty,
            "requested_qty": self.requested_qty,
            "approved_qty": self.approved_qty,
            "reasons": list(self.reasons),
        }


@dataclass
class SellToCloseRecord:
    symbol: str
    action: str
    requested_qty: float | None
    approved_qty: float
    submitted: bool
    dry_run: bool
    detail: str
    broker_rejected: bool = False
    broker_reject_reason: str | None = None
    failure_category: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "requested_qty": self.requested_qty,
            "approved_qty": self.approved_qty,
            "submitted": self.submitted,
            "dry_run": self.dry_run,
            "detail": self.detail,
            "broker_rejected": self.broker_rejected,
            "broker_reject_reason": self.broker_reject_reason,
            "failure_category": self.failure_category,
        }


DEFAULT_TRIM_FRACTION = 0.25  # trim 25% of the holding when no explicit qty is given


def _held_long_qty(symbol: str, positions: list[Any]) -> float:
    """Currently-held LONG shares for ``symbol`` (0.0 if absent or net short)."""

    total = 0.0
    found = False
    for raw in positions:
        f = read_position_fields(raw)
        if f["symbol"] != symbol.upper():
            continue
        found = True
        qty = f["qty"] or 0.0
        side = f["side"]
        # A long position has positive qty and side 'long'. Anything else is not a
        # long we may reduce via sell-to-close.
        if side == "long" and qty > 0:
            total += qty
    return total if found else 0.0


def validate_sell_to_close(
    proposal: PositionActionProposal,
    positions: list[Any],
    *,
    limits: PortfolioLimits,
    trims_used_today: int = 0,
    exits_used_today: int = 0,
) -> SellToCloseDecision:
    """Deterministically decide an approved, capped sell quantity. Never oversells."""

    symbol = proposal.symbol.upper()
    reasons: list[str] = []

    if proposal.action not in (ACTION_TRIM, ACTION_EXIT):
        return SellToCloseDecision(symbol, proposal.action, False, 0.0, proposal.requested_qty, 0.0,
                                   [f"Unsupported position action {proposal.action!r}."])

    if not limits.enable_paper_sell_to_close:
        return SellToCloseDecision(symbol, proposal.action, False, 0.0, proposal.requested_qty, 0.0,
                                   ["Sell-to-close disabled (ENABLE_PAPER_SELL_TO_CLOSE=false)."])

    held = _held_long_qty(symbol, positions)
    if held <= 0:
        return SellToCloseDecision(symbol, proposal.action, False, held, proposal.requested_qty, 0.0,
                                   [f"No held LONG position in {symbol}; refusing to sell "
                                    "(shorts require buy-to-cover, which is not supported)."])

    # Per-day action caps (separate from entry caps).
    if proposal.action == ACTION_EXIT and exits_used_today >= limits.max_position_exits_per_day:
        return SellToCloseDecision(symbol, proposal.action, False, held, proposal.requested_qty, 0.0,
                                   [f"Daily exit cap reached ({limits.max_position_exits_per_day})."])
    if proposal.action == ACTION_TRIM and trims_used_today >= limits.max_position_trims_per_day:
        return SellToCloseDecision(symbol, proposal.action, False, held, proposal.requested_qty, 0.0,
                                   [f"Daily trim cap reached ({limits.max_position_trims_per_day})."])

    if proposal.action == ACTION_EXIT:
        approved = held
        reasons.append(f"Exit: sell all {held:g} held long shares (position goes flat; never short).")
    else:
        requested = proposal.requested_qty
        if requested is None or requested <= 0:
            requested = max(1.0, round(held * DEFAULT_TRIM_FRACTION))
            reasons.append(f"Trim: no explicit qty; defaulting to {DEFAULT_TRIM_FRACTION:.0%} of holding.")
        approved = min(requested, held)
        if requested > held:
            reasons.append(f"Requested {requested:g} > held {held:g}; capped to {approved:g} (never oversell).")
        else:
            reasons.append(f"Trim {approved:g} of {held:g} held shares.")

    # Whole shares only; final clamp guarantees 0 < approved <= held.
    approved = float(int(approved))
    approved = max(0.0, min(approved, held))
    if approved <= 0:
        return SellToCloseDecision(symbol, proposal.action, False, held, proposal.requested_qty, 0.0,
                                   reasons + ["Approved quantity rounded to zero; nothing to sell."])

    return SellToCloseDecision(symbol, proposal.action, True, held, proposal.requested_qty, approved, reasons)


def build_sell_to_close_order(decision: SellToCloseDecision, *, dry_run: bool) -> OrderRequest:
    """Build a reduce-only SELL OrderRequest from an approved decision."""

    if not decision.approved or decision.approved_qty <= 0:
        raise ValueError("Only an approved decision with positive quantity can build an order.")
    return OrderRequest(
        proposal_id=f"sell_to_close_{decision.symbol}",
        symbol=decision.symbol,
        action=TradeAction.SELL,
        asset_class=AssetClass.STOCK,
        quantity=decision.approved_qty,
        short=False,
        margin=False,
        sell_to_close=True,
        dry_run=dry_run,
        risk_approved=True,
    )


def execute_sell_to_close(
    proposals: list[PositionActionProposal],
    *,
    client: AlpacaClientWrapper | None,
    dry_run: bool,
    limits: PortfolioLimits,
    refresh_positions: Callable[[], list[Any]],
    kill_switch_path: str | None = None,
) -> list[SellToCloseRecord]:
    """Validate + execute sell-to-close reductions, refreshing positions per order.

    ``refresh_positions`` is called immediately before each order so the cap is
    always re-validated against the latest broker-held quantity. Best-effort and
    fully logged; never fakes a fill.
    """

    records: list[SellToCloseRecord] = []
    trims = 0
    exits = 0

    for proposal in proposals:
        # Refresh positions immediately before deciding/submitting this order so a
        # stale snapshot can never oversell or act on a since-closed position.
        positions = list(refresh_positions() or [])
        decision = validate_sell_to_close(
            proposal, positions, limits=limits,
            trims_used_today=trims, exits_used_today=exits,
        )
        if not decision.approved:
            records.append(SellToCloseRecord(
                symbol=decision.symbol, action=proposal.action,
                requested_qty=proposal.requested_qty, approved_qty=0.0,
                submitted=False, dry_run=dry_run,
                detail="Rejected by deterministic risk: " + "; ".join(decision.reasons),
            ))
            continue

        if is_engaged(kill_switch_path):
            records.append(SellToCloseRecord(
                symbol=decision.symbol, action=proposal.action,
                requested_qty=proposal.requested_qty, approved_qty=decision.approved_qty,
                submitted=False, dry_run=dry_run,
                detail="Kill switch engaged; sell-to-close blocked.",
            ))
            continue

        order = build_sell_to_close_order(decision, dry_run=dry_run)

        if dry_run:
            records.append(SellToCloseRecord(
                symbol=decision.symbol, action=proposal.action,
                requested_qty=proposal.requested_qty, approved_qty=decision.approved_qty,
                submitted=False, dry_run=True,
                detail=f"Dry-run: would sell-to-close {decision.approved_qty:g} {decision.symbol}; "
                       + "; ".join(decision.reasons),
            ))
            trims, exits = _bump(decision, trims, exits)
            continue

        if client is None:
            records.append(SellToCloseRecord(
                symbol=decision.symbol, action=proposal.action,
                requested_qty=proposal.requested_qty, approved_qty=decision.approved_qty,
                submitted=False, dry_run=False,
                detail="No broker client configured; submission skipped.",
            ))
            continue

        try:
            client.submit_paper_sell_to_close_order(order)
            records.append(SellToCloseRecord(
                symbol=decision.symbol, action=proposal.action,
                requested_qty=proposal.requested_qty, approved_qty=decision.approved_qty,
                submitted=True, dry_run=False,
                detail=f"Submitted reduce-only SELL {decision.approved_qty:g} {decision.symbol} to Alpaca paper. "
                       + "; ".join(decision.reasons),
            ))
            trims, exits = _bump(decision, trims, exits)
        except Exception as exc:  # noqa: BLE001 - log any broker/network failure; never fake a fill
            category, reason, _code = classify_broker_error(exc)
            records.append(SellToCloseRecord(
                symbol=decision.symbol, action=proposal.action,
                requested_qty=proposal.requested_qty, approved_qty=decision.approved_qty,
                submitted=False, dry_run=False,
                detail=f"Broker rejected sell-to-close ({category}): {reason}",
                broker_rejected=True, broker_reject_reason=reason, failure_category=category,
            ))

    return records


def _bump(decision: SellToCloseDecision, trims: int, exits: int) -> tuple[int, int]:
    if decision.action == ACTION_EXIT:
        return trims, exits + 1
    return trims + 1, exits


__all__ = [
    "PositionActionProposal",
    "SellToCloseDecision",
    "SellToCloseRecord",
    "validate_sell_to_close",
    "build_sell_to_close_order",
    "execute_sell_to_close",
    "ACTION_TRIM",
    "ACTION_EXIT",
]
