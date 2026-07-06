"""Deterministic risk engine — the authoritative gate on every trade.

The LLM agents propose and advise; THIS code decides. It runs after the risk
analyst and applies hard caps that no prompt can bypass:

* symbol must exist and be tradable (kills hallucinated tickers)
* a live price must be available (never trade on an invented price)
* per-position weight cap, gross-exposure cap, cash floor for buys
* buying-power check with a safety margin (the old system's most common real
  broker rejection was "insufficient buying power" — checked up front here)
* per-day order-count and notional caps, ET-scoped
* shorts require ALLOW_SHORTS and a shortable asset
* the risk analyst's verdict can only shrink or veto — an "adjusted" size
  larger than requested is clamped down

Every rejection carries a human-readable reason so cycles are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.agents import Proposal, RiskVerdict
from src.broker import AccountInfo, AssetInfo, PositionInfo
from src.config import RiskLimits

BUYING_POWER_SAFETY = 0.90  # use at most 90% of reported buying power


@dataclass
class RiskDecision:
    proposal_index: int
    symbol: str
    action: str            # buy | sell | short | cover
    order_side: str        # buy | sell (what the broker actually receives)
    approved: bool
    qty: int = 0
    est_price: float | None = None
    est_notional: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons) or ("approved" if self.approved else "rejected")


def _rejected(index: int, proposal: Proposal, side: str, *reasons: str) -> RiskDecision:
    return RiskDecision(
        proposal_index=index,
        symbol=proposal.symbol,
        action=proposal.action,
        order_side=side,
        approved=False,
        reasons=list(reasons),
    )


def evaluate_proposals(
    proposals: list[Proposal],
    verdicts: list[RiskVerdict],
    *,
    account: AccountInfo,
    positions: list[PositionInfo],
    limits: RiskLimits,
    orders_today: int,
    notional_today: float,
    price_of: Callable[[str], float | None],
    asset_of: Callable[[str], AssetInfo | None],
) -> list[RiskDecision]:
    """Size and gate each proposal. Returns one decision per proposal, in order."""

    equity = max(account.equity, 0.0)
    held = {p.symbol: p for p in positions}
    gross_exposure = sum(p.notional for p in positions)
    cash_available = account.cash
    buying_power_left = account.buying_power * BUYING_POWER_SAFETY
    orders_used = orders_today
    notional_used = notional_today
    verdict_by_index = {v.index: v for v in verdicts}
    approved_sides_this_cycle: set[tuple[str, str]] = set()

    decisions: list[RiskDecision] = []
    for index, proposal in enumerate(proposals):
        side = "buy" if proposal.action in ("buy", "cover") else "sell"
        reasons: list[str] = []

        # 1) Risk-analyst verdict (LLM veto/shrink; fail closed when missing).
        verdict = verdict_by_index.get(index)
        if verdict is None:
            decisions.append(_rejected(index, proposal, side, "no risk-analyst verdict (fail closed)"))
            continue
        if verdict.verdict == "reject":
            decisions.append(_rejected(index, proposal, side, f"risk analyst rejected: {verdict.reason}"))
            continue

        # Effective requested weight: analyst may only shrink, never enlarge.
        weight = proposal.weight_pct
        if verdict.verdict == "reduce" and verdict.adjusted_weight_pct:
            weight = min(weight, max(0.0, verdict.adjusted_weight_pct))
            reasons.append(f"risk analyst reduced size: {verdict.reason}")

        # 2) One order per symbol+side per cycle.
        if (proposal.symbol, side) in approved_sides_this_cycle:
            decisions.append(_rejected(index, proposal, side, "duplicate symbol/side already approved this cycle"))
            continue

        # 3) Daily order-count cap.
        if orders_used + 1 > limits.max_orders_per_day:
            decisions.append(
                _rejected(index, proposal, side, f"daily order cap reached ({limits.max_orders_per_day})")
            )
            continue

        # 4) Price must be known.
        price = price_of(proposal.symbol)
        if not price or price <= 0:
            decisions.append(_rejected(index, proposal, side, "no live price available for symbol"))
            continue

        # 5) Asset must exist / be tradable (and shortable for shorts).
        asset = asset_of(proposal.symbol)
        if asset is None or not asset.tradable:
            decisions.append(_rejected(index, proposal, side, "symbol not found or not tradable at broker"))
            continue

        position = held.get(proposal.symbol)

        if proposal.action in ("sell", "cover"):
            # ----- Exits: reduce/close an existing position. Never blocked by cash.
            required_side = "long" if proposal.action == "sell" else "short"
            if position is None or position.side != required_side:
                decisions.append(
                    _rejected(index, proposal, side, f"no {required_side} position in {proposal.symbol} to {proposal.action}")
                )
                continue
            qty = int(position.qty * proposal.fraction)
            if proposal.fraction >= 0.999:
                qty = int(position.qty)
            if qty < 1:
                decisions.append(_rejected(index, proposal, side, "computed exit quantity below 1 share"))
                continue
            est_notional = qty * price
        else:
            # ----- Entries: buy (long) or short.
            if proposal.action == "short":
                if not limits.allow_shorts:
                    decisions.append(_rejected(index, proposal, side, "shorting disabled (ALLOW_SHORTS=false)"))
                    continue
                if not asset.shortable:
                    decisions.append(_rejected(index, proposal, side, "asset not shortable at broker"))
                    continue
                if position is not None and position.side == "long":
                    decisions.append(
                        _rejected(index, proposal, side, "already long this symbol; sell it instead of shorting")
                    )
                    continue
            if proposal.action == "buy" and position is not None and position.side == "short":
                decisions.append(
                    _rejected(index, proposal, side, "already short this symbol; cover it instead of buying")
                )
                continue

            weight = min(weight, limits.max_position_pct)
            target_notional = weight * equity

            # Per-position cap including what is already held.
            existing_notional = position.notional if position is not None else 0.0
            max_additional = limits.max_position_pct * equity - existing_notional
            if max_additional <= 0:
                decisions.append(
                    _rejected(
                        index, proposal, side,
                        f"position already at/above max weight ({limits.max_position_pct:.0%})",
                    )
                )
                continue
            target_notional = min(target_notional, max_additional)

            # Gross-exposure cap.
            max_by_gross = limits.max_gross_exposure * equity - gross_exposure
            if max_by_gross <= 0:
                decisions.append(
                    _rejected(index, proposal, side, f"gross exposure cap reached ({limits.max_gross_exposure:.0%} of equity)")
                )
                continue
            target_notional = min(target_notional, max_by_gross)

            # Cash floor applies to buys (longs use cash, not margin).
            if proposal.action == "buy":
                spendable = cash_available - limits.min_cash_pct * equity
                if spendable <= 0:
                    decisions.append(
                        _rejected(index, proposal, side, f"cash floor reached (MIN_CASH_PCT={limits.min_cash_pct:.0%})")
                    )
                    continue
                target_notional = min(target_notional, spendable)

            # Buying power with safety margin (shorts consume margin BP).
            if target_notional > buying_power_left:
                target_notional = buying_power_left
            if target_notional <= 0:
                decisions.append(_rejected(index, proposal, side, "insufficient buying power"))
                continue

            # Daily notional cap.
            notional_room = limits.max_daily_notional - notional_used
            if notional_room <= 0:
                decisions.append(
                    _rejected(index, proposal, side, f"daily notional cap reached (${limits.max_daily_notional:,.0f})")
                )
                continue
            target_notional = min(target_notional, notional_room)

            qty = int(target_notional / price)
            if qty < 1:
                decisions.append(
                    _rejected(index, proposal, side, f"sized below 1 share at ${price:,.2f} after caps")
                )
                continue
            est_notional = qty * price

        # Approved — update running usage so later proposals see the impact.
        orders_used += 1
        notional_used += est_notional
        approved_sides_this_cycle.add((proposal.symbol, side))
        if proposal.action == "buy":
            cash_available -= est_notional
            buying_power_left -= est_notional
            gross_exposure += est_notional
        elif proposal.action == "short":
            buying_power_left -= est_notional
            gross_exposure += est_notional
        else:  # exits reduce exposure and free cash/BP (approximately)
            gross_exposure = max(0.0, gross_exposure - est_notional)
            if proposal.action == "sell":
                cash_available += est_notional

        decisions.append(
            RiskDecision(
                proposal_index=index,
                symbol=proposal.symbol,
                action=proposal.action,
                order_side=side,
                approved=True,
                qty=qty,
                est_price=price,
                est_notional=round(est_notional, 2),
                reasons=reasons or ["passed all deterministic checks"],
            )
        )

    return decisions
