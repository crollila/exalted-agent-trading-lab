"""Deterministic risk engine — the authoritative gate on every trade.

The LLM agents propose and advise; THIS code decides. It enforces the tighter
of the team's self-chosen charter and the immutable platform caps:

* symbol must exist and be tradable (kills hallucinated tickers)
* a live price must be available (never trade on an invented price)
* per-position weight cap and gross-exposure cap (charter ∧ platform)
* buys use cash only — unless the team enabled margin, then buying power
  (with a safety haircut; "insufficient buying power" was the old system's
  most common real broker rejection, so it is checked up front)
* shorts require shorts enabled (platform AND charter) and a shortable asset
* options are LONG calls/puts only, sized by premium at risk with per-trade
  and total-open-premium caps; the exact contract is resolved deterministically
* per-day order-count and notional caps, ET-scoped
* the risk analyst's verdict can only shrink or veto — an "adjusted" size
  larger than requested is clamped down

Every rejection carries a human-readable reason so cycles are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.agents import Proposal, RiskVerdict
from src.broker import AccountInfo, AssetInfo, OptionContract, PositionInfo
from src.charter import TeamCharter
from src.config import RiskLimits

BUYING_POWER_SAFETY = 0.90  # use at most 90% of reported buying power
OPTION_MULTIPLIER = 100.0

OptionResolver = Callable[..., OptionContract | None]


@dataclass
class RiskDecision:
    proposal_index: int
    symbol: str            # what the broker receives (stock ticker or OCC symbol)
    action: str            # buy | sell | short | cover
    order_side: str        # buy | sell
    approved: bool
    qty: int = 0
    est_price: float | None = None   # per share, or per-contract premium for options
    est_notional: float = 0.0        # dollars (premium x100 x qty for options)
    instrument: str = "stock"
    contract_desc: str | None = None # e.g. "NVDA 2026-08-07 $200 call"
    reasons: list[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons) or ("approved" if self.approved else "rejected")

    @property
    def describe_symbol(self) -> str:
        return self.contract_desc or self.symbol


def _rejected(index: int, proposal: Proposal, side: str, *reasons: str) -> RiskDecision:
    return RiskDecision(
        proposal_index=index,
        symbol=proposal.symbol,
        action=proposal.action,
        order_side=side,
        approved=False,
        instrument=proposal.instrument,
        reasons=list(reasons),
    )


def evaluate_proposals(
    proposals: list[Proposal],
    verdicts: list[RiskVerdict],
    *,
    account: AccountInfo,
    positions: list[PositionInfo],
    limits: RiskLimits,
    charter: TeamCharter | None = None,
    orders_today: int,
    notional_today: float,
    price_of: Callable[[str], float | None],
    asset_of: Callable[[str], AssetInfo | None],
    resolve_option: OptionResolver | None = None,
) -> list[RiskDecision]:
    """Size and gate each proposal. Returns one decision per proposal, in order."""

    equity = max(account.equity, 0.0)
    held = {p.symbol: p for p in positions}

    # Effective caps: the tighter of the team's charter and the platform walls.
    max_position_pct = limits.max_position_pct
    max_gross = limits.max_gross_exposure
    if charter is not None:
        max_position_pct = min(max_position_pct, charter.max_position_pct)
        max_gross = min(max_gross, charter.max_gross_exposure)

    def _instrument_enabled(name: str, platform_flag: bool) -> bool:
        if not platform_flag:
            return False
        return charter.allows(name) if charter is not None else True

    margin_enabled = _instrument_enabled("margin", limits.allow_margin)
    shorts_enabled = _instrument_enabled("shorts", limits.allow_shorts)
    options_enabled = _instrument_enabled("options", limits.allow_options)

    gross_exposure = sum(p.notional for p in positions)
    open_option_premium = sum(p.notional for p in positions if p.is_option and p.side == "long")
    cash_available = account.cash
    buying_power_left = account.buying_power * BUYING_POWER_SAFETY
    orders_used = orders_today
    notional_used = notional_today
    verdict_by_index = {v.index: v for v in verdicts}
    approved_sides_this_cycle: set[tuple[str, str]] = set()

    decisions: list[RiskDecision] = []
    for index, proposal in enumerate(proposals):
        side = "buy" if proposal.action in ("buy", "cover") else "sell"

        # 1) Risk-analyst verdict (LLM veto/shrink; fail closed when missing).
        verdict = verdict_by_index.get(index)
        if verdict is None:
            decisions.append(_rejected(index, proposal, side, "no risk-analyst verdict (fail closed)"))
            continue
        if verdict.verdict == "reject":
            decisions.append(_rejected(index, proposal, side, f"risk analyst rejected: {verdict.reason}"))
            continue

        reasons: list[str] = []
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

        decision: RiskDecision | None
        if proposal.instrument == "option":
            decision, usage = _evaluate_option(
                index, proposal, side, weight,
                equity=equity, held=held,
                options_enabled=options_enabled,
                limits=limits,
                gross_room=max_gross * equity - gross_exposure,
                open_option_premium=open_option_premium,
                buying_power_left=buying_power_left,
                notional_room=limits.max_daily_notional - notional_used,
                price_of=price_of,
                asset_of=asset_of,
                resolve_option=resolve_option,
                reasons=reasons,
            )
        else:
            decision, usage = _evaluate_stock(
                index, proposal, side, weight,
                equity=equity, held=held,
                shorts_enabled=shorts_enabled,
                margin_enabled=margin_enabled,
                max_position_pct=max_position_pct,
                gross_room=max_gross * equity - gross_exposure,
                cash_available=cash_available,
                buying_power_left=buying_power_left,
                notional_room=limits.max_daily_notional - notional_used,
                price_of=price_of,
                asset_of=asset_of,
                reasons=reasons,
            )

        decisions.append(decision)
        if not decision.approved:
            continue

        # Approved — update running usage so later proposals see the impact.
        orders_used += 1
        notional_used += decision.est_notional
        approved_sides_this_cycle.add((proposal.symbol, side))
        cash_available += usage["cash_delta"]
        buying_power_left += usage["bp_delta"]
        gross_exposure += usage["gross_delta"]
        open_option_premium += usage["premium_delta"]

    return decisions


def _evaluate_stock(
    index: int,
    proposal: Proposal,
    side: str,
    weight: float,
    *,
    equity: float,
    held: dict[str, PositionInfo],
    shorts_enabled: bool,
    margin_enabled: bool,
    max_position_pct: float,
    gross_room: float,
    cash_available: float,
    buying_power_left: float,
    notional_room: float,
    price_of: Callable[[str], float | None],
    asset_of: Callable[[str], AssetInfo | None],
    reasons: list[str],
) -> tuple[RiskDecision, dict]:
    no_usage = {"cash_delta": 0.0, "bp_delta": 0.0, "gross_delta": 0.0, "premium_delta": 0.0}
    position = held.get(proposal.symbol)

    price = price_of(proposal.symbol)
    if not price or price <= 0:
        return _rejected(index, proposal, side, "no live price available for symbol"), no_usage

    asset = asset_of(proposal.symbol)
    if asset is None or not asset.tradable:
        return _rejected(index, proposal, side, "symbol not found or not tradable at broker"), no_usage

    if proposal.action in ("sell", "cover"):
        required_side = "long" if proposal.action == "sell" else "short"
        if position is None or position.side != required_side or position.is_option:
            return (
                _rejected(index, proposal, side, f"no {required_side} stock position in {proposal.symbol} to {proposal.action}"),
                no_usage,
            )
        qty = int(position.qty) if proposal.fraction >= 0.999 else int(position.qty * proposal.fraction)
        if qty < 1:
            return _rejected(index, proposal, side, "computed exit quantity below 1 share"), no_usage
        est_notional = qty * price
        usage = {
            "cash_delta": est_notional if proposal.action == "sell" else -est_notional,
            "bp_delta": 0.0,
            "gross_delta": -est_notional,
            "premium_delta": 0.0,
        }
        return (
            RiskDecision(
                proposal_index=index, symbol=proposal.symbol, action=proposal.action,
                order_side=side, approved=True, qty=qty, est_price=price,
                est_notional=round(est_notional, 2),
                reasons=reasons or ["passed all deterministic checks"],
            ),
            usage,
        )

    # ----- Entries: buy (long) or short.
    if proposal.action == "short":
        if not shorts_enabled:
            return _rejected(index, proposal, side, "shorting not enabled (platform or charter)"), no_usage
        if not asset.shortable:
            return _rejected(index, proposal, side, "asset not shortable at broker"), no_usage
        if position is not None and position.side == "long":
            return _rejected(index, proposal, side, "already long this symbol; sell it instead of shorting"), no_usage
    if proposal.action == "buy" and position is not None and position.side == "short":
        return _rejected(index, proposal, side, "already short this symbol; cover it instead of buying"), no_usage

    weight = min(weight, max_position_pct)
    target_notional = weight * equity

    existing_notional = position.notional if position is not None else 0.0
    max_additional = max_position_pct * equity - existing_notional
    if max_additional <= 0:
        return (
            _rejected(index, proposal, side, f"position already at/above max weight ({max_position_pct:.0%})"),
            no_usage,
        )
    target_notional = min(target_notional, max_additional)

    if gross_room <= 0:
        return _rejected(index, proposal, side, "gross exposure cap reached"), no_usage
    target_notional = min(target_notional, gross_room)

    if proposal.action == "buy":
        spendable = buying_power_left if margin_enabled else max(0.0, cash_available)
        if spendable <= 0:
            what = "buying power" if margin_enabled else "cash (margin not enabled in charter)"
            return _rejected(index, proposal, side, f"insufficient {what}"), no_usage
        target_notional = min(target_notional, spendable)

    if target_notional > buying_power_left:
        target_notional = buying_power_left
    if target_notional <= 0:
        return _rejected(index, proposal, side, "insufficient buying power"), no_usage

    if notional_room <= 0:
        return _rejected(index, proposal, side, "daily notional cap reached"), no_usage
    target_notional = min(target_notional, notional_room)

    qty = int(target_notional / price)
    if qty < 1:
        return _rejected(index, proposal, side, f"sized below 1 share at ${price:,.2f} after caps"), no_usage
    est_notional = qty * price

    usage = {
        "cash_delta": -est_notional if proposal.action == "buy" else 0.0,
        "bp_delta": -est_notional,
        "gross_delta": est_notional,
        "premium_delta": 0.0,
    }
    return (
        RiskDecision(
            proposal_index=index, symbol=proposal.symbol, action=proposal.action,
            order_side=side, approved=True, qty=qty, est_price=price,
            est_notional=round(est_notional, 2),
            reasons=reasons or ["passed all deterministic checks"],
        ),
        usage,
    )


def _evaluate_option(
    index: int,
    proposal: Proposal,
    side: str,
    weight: float,
    *,
    equity: float,
    held: dict[str, PositionInfo],
    options_enabled: bool,
    limits: RiskLimits,
    gross_room: float,
    open_option_premium: float,
    buying_power_left: float,
    notional_room: float,
    price_of: Callable[[str], float | None],
    asset_of: Callable[[str], AssetInfo | None],
    resolve_option: OptionResolver | None,
    reasons: list[str],
) -> tuple[RiskDecision, dict]:
    no_usage = {"cash_delta": 0.0, "bp_delta": 0.0, "gross_delta": 0.0, "premium_delta": 0.0}

    if proposal.action == "sell":
        # Close/reduce a LONG option position we hold (sell-to-close only).
        position = held.get(proposal.symbol)
        if position is None or not position.is_option or position.side != "long":
            return (
                _rejected(index, proposal, side, f"no long option position {proposal.symbol} to sell-to-close"),
                no_usage,
            )
        qty = int(position.qty) if proposal.fraction >= 0.999 else int(position.qty * proposal.fraction)
        if qty < 1:
            return _rejected(index, proposal, side, "computed exit below 1 contract"), no_usage
        premium = position.current_price or (position.notional / max(position.qty, 1) / OPTION_MULTIPLIER)
        est_notional = qty * premium * OPTION_MULTIPLIER
        usage = {
            "cash_delta": est_notional, "bp_delta": 0.0,
            "gross_delta": -est_notional, "premium_delta": -est_notional,
        }
        return (
            RiskDecision(
                proposal_index=index, symbol=proposal.symbol, action=proposal.action,
                order_side="sell", approved=True, qty=qty, est_price=round(premium, 2),
                est_notional=round(est_notional, 2), instrument="option",
                contract_desc=position.describe(),
                reasons=reasons or ["sell-to-close long option"],
            ),
            usage,
        )

    # ----- Option entry: buy a long call/put.
    if not options_enabled:
        return _rejected(index, proposal, side, "options not enabled (platform or charter)"), no_usage
    if resolve_option is None:
        return _rejected(index, proposal, side, "option contract resolver unavailable"), no_usage

    underlying_price = price_of(proposal.symbol)
    if not underlying_price or underlying_price <= 0:
        return _rejected(index, proposal, side, "no live price for underlying"), no_usage
    asset = asset_of(proposal.symbol)
    if asset is None or not asset.tradable:
        return _rejected(index, proposal, side, "underlying not found or not tradable"), no_usage

    contract = resolve_option(
        proposal.symbol,
        proposal.option_type or "call",
        ref_price=underlying_price,
        dte_target=proposal.dte_target,
        moneyness=proposal.moneyness,
    )
    if contract is None or not contract.ask or contract.ask <= 0:
        return _rejected(index, proposal, side, "no suitable option contract/quote available"), no_usage

    per_trade_cap = limits.max_option_premium_pct * equity
    budget = min(weight * equity, per_trade_cap)
    total_room = limits.max_total_option_premium_pct * equity - open_option_premium
    if total_room <= 0:
        return (
            _rejected(index, proposal, side,
                      f"total open option premium cap reached ({limits.max_total_option_premium_pct:.0%} of equity)"),
            no_usage,
        )
    budget = min(budget, total_room, gross_room, buying_power_left, notional_room)
    if budget <= 0:
        return _rejected(index, proposal, side, "no room under premium/gross/BP/daily caps"), no_usage

    contracts = int(budget / (contract.ask * OPTION_MULTIPLIER))
    if contracts < 1:
        return (
            _rejected(index, proposal, side,
                      f"premium ${contract.ask:,.2f}/share (${contract.ask * OPTION_MULTIPLIER:,.0f}/contract) exceeds budget ${budget:,.0f}"),
            no_usage,
        )
    est_notional = contracts * contract.ask * OPTION_MULTIPLIER

    desc = f"{contract.underlying} {contract.expiration} ${contract.strike:g} {contract.option_type}"
    usage = {
        "cash_delta": -est_notional, "bp_delta": -est_notional,
        "gross_delta": est_notional, "premium_delta": est_notional,
    }
    return (
        RiskDecision(
            proposal_index=index, symbol=contract.occ_symbol, action=proposal.action,
            order_side="buy", approved=True, qty=contracts, est_price=contract.ask,
            est_notional=round(est_notional, 2), instrument="option", contract_desc=desc,
            reasons=reasons or [f"long {desc}: max loss = premium ${est_notional:,.0f}"],
        ),
        usage,
    )
