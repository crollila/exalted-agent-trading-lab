"""LLM-driven competition proposal agent (Tasks 3, 4, 8).

Turns a team's live (allowlisted) research context into structured proposals via
the provider abstraction. Hard boundaries:

* The LLM only produces proposal JSON. It never calls Alpaca and never submits
  orders. Approved quantities/contracts are computed later by the deterministic
  risk engine — never taken from the model.
* Prompts contain only allowlisted, provenance-tagged context. No secrets, no API
  keys, no Discord token, no raw .env.
* Malformed/invalid model output is rejected and logged, never crashes the cycle.

Alpha and Beta receive deliberately different mandates so they do not mirror each
other.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from src.agents.llm_provider import LLMProvider, LLMProviderError, parse_structured_output
from src.competition.proposals import (
    CompetitionProposal,
    DataProvenance,
    LegSide,
    OptionLeg,
    OptionType,
    ProposalType,
)
from src.competition.week_competition import ProposalBundle

ASSET_TYPE_TO_PROPOSAL_TYPE = {
    "stock_long": ProposalType.STOCK_LONG,
    "stock_short": ProposalType.STOCK_SHORT,
    "margin_stock_long": ProposalType.MARGIN_STOCK_LONG,
    "margin_stock_short": ProposalType.MARGIN_STOCK_SHORT,
    "option_long_call": ProposalType.OPTION_LONG_CALL,
    "option_long_put": ProposalType.OPTION_LONG_PUT,
    "option_debit_spread": ProposalType.OPTION_DEBIT_SPREAD,
    "option_defined_risk_spread": ProposalType.OPTION_DEFINED_RISK_SPREAD,
}

FRESHNESS_TO_PROVENANCE = {
    "live": DataProvenance.LIVE,
    "delayed": DataProvenance.DELAYED,
    "local": DataProvenance.FIXTURE,
    "unknown": DataProvenance.UNKNOWN,
}

_SHORT_TYPES = {ProposalType.STOCK_SHORT, ProposalType.MARGIN_STOCK_SHORT}
_MARGIN_TYPES = {ProposalType.MARGIN_STOCK_LONG, ProposalType.MARGIN_STOCK_SHORT}
_OPTION_TYPES = {
    ProposalType.OPTION_LONG_CALL,
    ProposalType.OPTION_LONG_PUT,
    ProposalType.OPTION_DEBIT_SPREAD,
    ProposalType.OPTION_DEFINED_RISK_SPREAD,
}

_SHARED_RULES = (
    "Output rules:\n"
    "- Respond with a SINGLE valid JSON object only. No prose outside JSON.\n"
    "- You only produce proposal JSON. You never place trades, never call any broker, "
    "and never compute share/contract sizes — a deterministic risk engine does that.\n"
    "- Every proposal must include: thesis, invalidation_condition, risk_notes, "
    "data_sources_used, data_freshness, and confidence (0..1).\n"
    "- The context includes a 'research' block with results; each result has a 'source_id'. "
    "When a proposal relies on research, cite those ids in 'research_source_ids' and set "
    "'research_changed_proposal' (true/false). Do NOT invent news beyond the provided research sources.\n"
    "- Use only the provided context. If a fact is unavailable, say 'unknown'. Never invent prices or news.\n"
    "- Review the prior scorecard, team memory, and performance_feedback (recent winners/losers, "
    "best/worst symbols and strategies), and explain in learning_update what changed from last cycle.\n"
    "- performance_feedback.outcome_feedback reports recent worked/failed proposals, common winning/losing "
    "themes, and SPY-relative performance from actual paper outcomes. Treat it as RESEARCH FEEDBACK ONLY: it "
    "informs your next ideas but never authorizes bypassing risk, position sizing, credentials, or the kill "
    "switch. The deterministic risk engine still gates and sizes every trade.\n"
    "- PORTFOLIO MANAGER FIRST. Before proposing trades, review the current portfolio, buying power, prior "
    "theses, attribution outcomes, and SPY-relative performance, then decide whether to hold, trim, close, "
    "rotate, add, hedge, reduce exposure, or do nothing. Put this in a 'portfolio_decision' object. Briefly "
    "answer in its 'rationale': why are we beating/losing to SPY? which positions/sectors drove it? did the "
    "prior thesis work/fail/stay unproven? what changed since last cycle? is the new idea better than the "
    "weakest current holding? should we hold and observe? Keep it to 2-4 sentences.\n"
    "- Doing NOTHING (decision_type 'no_trade' or 'hold') is a valid, successful outcome. Only propose NEW "
    "trades when an idea clearly beats the weakest current holding. Set 'max_new_proposals_this_cycle' (0-3) "
    "and 'allowed_to_generate_new_orders'. If buying power is low, prefer trim/close/rotate or an explicit "
    "'increase_margin_exposure_request' instead of new-money buys; the platform may still block or downsize "
    "new buys. You may set tactical thresholds, but platform hard caps always win.\n"
    "- Allowed asset_type values: stock_long, stock_short, margin_stock_long, margin_stock_short, "
    "option_long_call, option_long_put, option_debit_spread, option_defined_risk_spread.\n"
    "- Keep proposals within a small, sane size (target_weight <= 0.15).\n"
)

_SCHEMA_HINT = (
    'Return JSON shaped like: {"team_id": "...", "strategy_id": "...", '
    '"market_summary": "...", "research_notes": [{"source": "...", "summary": "...", '
    '"freshness": "live|delayed|local|unknown"}], "proposals": [{"asset_type": "stock_long", '
    '"symbol": "SPY", "action": "buy", "thesis": "...", "confidence": 0.6, "estimated_price": 500.0, '
    '"target_weight": 0.05, "intended_holding_period": "...", "max_loss_thesis": "...", '
    '"invalidation_condition": "...", "risk_notes": "...", "data_sources_used": ["alpaca_quote"], '
    '"data_freshness": "live", "research_source_ids": ["r1"], "research_changed_proposal": true}], '
    '"learning_update": {"what_worked": "...", "what_failed": "...", '
    '"next_adjustment": "..."}, "hypothesis": "...", "watchlist": ["SPY"], '
    '"portfolio_decision": {"decision_type": "hold|no_trade|trim|close|rotate|add|reduce_gross_exposure|'
    'increase_margin_exposure_request|hedge", "affected_symbols": ["SPY"], "rationale": "...", '
    '"allowed_to_generate_new_orders": true, "max_new_proposals_this_cycle": 2, '
    '"proposed_closes_or_trims": [], "rejected_new_ideas_reason": null}}'
)


def build_system_prompt(team_id: str) -> str:
    if team_id == "team_alpha":
        mandate = (
            "You are the lead strategist for TEAM ALPHA in a paper-only trading competition.\n"
            "MANDATE: aggressive growth, EXPLORATION mode. You hunt momentum, breakouts, and catalysts. You are "
            "willing to use shorts, margin ideas, and defined-risk options to maximize risk-adjusted "
            "upside. You are higher-variance: more willing to rotate out of weak holdings into new catalyst/"
            "momentum ideas, and you learn fast from outcomes. You aim to BEAT Team Beta and SPY. "
            "You still obey every risk/exposure/kill-switch rule."
        )
    elif team_id == "team_beta":
        mandate = (
            "You are the lead strategist for TEAM BETA in a paper-only trading competition.\n"
            "MANDATE: contrarian, risk-adjusted, CONSERVATION mode. You favor mean reversion, hedging, and capital "
            "preservation. You are lower-variance: you trade less, prefer hold/trim decisions, prioritize "
            "drawdown control and SPY-relative steadiness, and only add new exposure with strong justification. "
            "You use shorts and defined-risk options to fade extremes and manage drawdown. You aim to BEAT "
            "Team Alpha and SPY with steadier returns."
        )
    else:
        mandate = f"You are the lead strategist for {team_id} in a paper-only trading competition."

    return (
        f"{mandate}\n\n"
        "This is PAPER TRADING ONLY. No live trading exists. You cannot move money.\n\n"
        f"{_SHARED_RULES}\n{_SCHEMA_HINT}"
    )


def build_user_prompt(team_id: str, context: dict[str, Any]) -> str:
    # Context is already allowlisted + provenance-tagged and contains NO secrets.
    return (
        f"Team: {team_id}\n"
        "Here is your current allowlisted research context (each item tagged live/delayed/local/unknown). "
        "Use it to propose trades and to explain what changed from last cycle.\n\n"
        f"{json.dumps(context, indent=2, default=str)}"
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_sources(raw: Any) -> list[str]:
    if isinstance(raw, list):
        cleaned = [str(s).strip() for s in raw if str(s).strip()]
        if cleaned:
            return cleaned
    return ["unknown"]


def _parse_expiration(raw: Any) -> date:
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip()).date()
        except ValueError:
            try:
                return date.fromisoformat(raw.strip())
            except ValueError:
                pass
    return date.today() + timedelta(days=30)


def llm_dict_to_proposal(
    raw: dict[str, Any],
    *,
    team_id: str,
    strategy_id: str,
    agent_id: str,
) -> tuple[CompetitionProposal | None, str | None]:
    """Adapt one LLM proposal dict into a strict CompetitionProposal.

    Missing required fields are filled with safe deterministic defaults. Returns
    ``(proposal, None)`` on success or ``(None, reason)`` when it cannot be built.
    """

    if not isinstance(raw, dict):
        return None, "proposal is not a JSON object"

    asset_type = str(raw.get("asset_type", "")).strip().lower()
    proposal_type = ASSET_TYPE_TO_PROPOSAL_TYPE.get(asset_type)
    if proposal_type is None:
        return None, f"unknown asset_type: {asset_type!r}"

    symbol = str(raw.get("symbol", "")).strip().upper()
    if not symbol:
        return None, "missing symbol"

    try:
        estimated_price = float(raw.get("estimated_price"))
    except (TypeError, ValueError):
        return None, f"{symbol}: missing/invalid estimated_price"
    if estimated_price <= 0:
        return None, f"{symbol}: estimated_price must be > 0"

    thesis = str(raw.get("thesis", "")).strip()
    if not thesis:
        return None, f"{symbol}: missing thesis"

    confidence = _clamp(float(raw.get("confidence", 0.5) or 0.5), 0.0, 1.0)
    target_weight = _clamp(float(raw.get("target_weight", 0.05) or 0.05), 0.001, 1.0)

    common: dict[str, Any] = {
        "team_id": team_id,
        "agent_id": agent_id,
        "strategy_id": strategy_id,
        "proposal_type": proposal_type,
        "symbol": symbol,
        "action": str(raw.get("action", "")).strip() or "open",
        "thesis": thesis,
        "confidence": confidence,
        "estimated_price": estimated_price,
        "quote_reference": (str(raw["quote_reference"]) if raw.get("quote_reference") else None),
        "intended_holding_period": str(raw.get("intended_holding_period", "")).strip() or "1-5 sessions",
        "max_loss_thesis": str(raw.get("max_loss_thesis", "")).strip() or "Limited by position size and stop.",
        "invalidation_condition": str(raw.get("invalidation_condition", "")).strip()
        or "Thesis invalidated by adverse move.",
        "expected_catalyst": str(raw.get("expected_catalyst", "")).strip() or "LLM research catalyst",
        "risk_notes": str(raw.get("risk_notes", "")).strip() or "Standard market risk.",
        "data_sources": _clean_sources(raw.get("data_sources_used")),
        "data_provenance": FRESHNESS_TO_PROVENANCE.get(
            str(raw.get("data_freshness", "unknown")).strip().lower(), DataProvenance.UNKNOWN
        ),
    }

    if proposal_type in _OPTION_TYPES:
        kwargs = _build_option_kwargs(raw, proposal_type, symbol, estimated_price)
    else:
        kwargs = {"target_weight": target_weight}
        if proposal_type in _SHORT_TYPES or proposal_type in _MARGIN_TYPES:
            kwargs["gross_exposure_impact"] = target_weight
            kwargs["net_exposure_impact"] = (
                -target_weight if proposal_type in _SHORT_TYPES else target_weight
            )
        if proposal_type in _SHORT_TYPES:
            kwargs["borrow_availability_assumption"] = (
                str(raw.get("borrow_availability_assumption", "")).strip()
                or "assumed_available (LLM; not broker-verified)"
            )
            kwargs["stop_level"] = float(raw.get("stop_level") or round(estimated_price * 1.15, 2))
            kwargs["max_loss_estimate"] = float(
                raw.get("max_loss_estimate") or round(estimated_price * 0.2, 2)
            )

    try:
        proposal = CompetitionProposal(**common, **kwargs)
    except ValidationError as exc:
        return None, f"{symbol}: schema rejected ({exc.error_count()} error(s))"
    return proposal, None


def _build_option_kwargs(
    raw: dict[str, Any],
    proposal_type: ProposalType,
    symbol: str,
    estimated_price: float,
) -> dict[str, Any]:
    expiry = _parse_expiration(raw.get("expiration"))
    contracts = max(1, int(raw.get("contracts", 1) or 1))
    net_premium = float(raw.get("net_premium_per_contract") or max(0.5, round(estimated_price * 0.01, 2)))
    strike = float(raw.get("strike") or round(estimated_price, 2))
    spread_width = float(raw.get("spread_width") or max(1.0, round(estimated_price * 0.02, 2)))

    is_put = proposal_type == ProposalType.OPTION_LONG_PUT
    option_type = OptionType.PUT if is_put else OptionType.CALL

    if proposal_type in (ProposalType.OPTION_LONG_CALL, ProposalType.OPTION_LONG_PUT):
        legs = [
            OptionLeg(
                side=LegSide.LONG,
                option_type=option_type,
                strike=strike,
                expiration=expiry,
                estimated_premium=round(net_premium, 2) or 0.5,
            )
        ]
        note_default = f"Long {option_type.value}: no assignment obligation; exercise optional."
    else:
        # Defined-risk debit spread: long ATM + short OTM (same type → covered).
        legs = [
            OptionLeg(
                side=LegSide.LONG,
                option_type=OptionType.CALL,
                strike=strike,
                expiration=expiry,
                estimated_premium=round(net_premium * 1.5, 2) + 0.01,
            ),
            OptionLeg(
                side=LegSide.SHORT,
                option_type=OptionType.CALL,
                strike=strike + spread_width,
                expiration=expiry,
                estimated_premium=round(net_premium * 0.5, 2) + 0.01,
            ),
        ]
        note_default = "Defined-risk debit spread: short leg covered by long leg; max loss = net debit."

    max_loss = round(net_premium * contracts * 100, 2)
    return {
        "underlying": symbol,
        "expiration": expiry,
        "contracts": contracts,
        "net_premium_per_contract": round(net_premium, 2) or 0.5,
        "max_premium_at_risk": max_loss,
        "max_loss": max_loss,
        "spread_width": spread_width if proposal_type not in (ProposalType.OPTION_LONG_CALL, ProposalType.OPTION_LONG_PUT) else None,
        "assignment_exercise_risk_note": str(raw.get("assignment_exercise_risk_note", "")).strip()
        or note_default,
        "greeks_available": False,
        "legs": legs,
    }


def generate_llm_proposals(
    team_id: str,
    *,
    provider: LLMProvider,
    context: dict[str, Any],
    strategy_id: str,
) -> ProposalBundle:
    """Call the provider and adapt its JSON into a ProposalBundle.

    Never raises on bad model output: parse/validation failures are captured in
    ``raw_errors`` and the bundle simply contains the proposals that parsed.
    """

    agent_id = f"{team_id}_llm"
    system_prompt = build_system_prompt(team_id)
    user_prompt = build_user_prompt(team_id, context)

    try:
        raw_text = provider.complete_json(system_prompt, user_prompt)
        data = parse_structured_output(raw_text, "proposal")
    except LLMProviderError as exc:
        return ProposalBundle(proposals=[], raw_errors=[f"LLM output rejected: {exc}"])
    except Exception as exc:  # noqa: BLE001 - provider/runtime failure must not crash the cycle
        return ProposalBundle(proposals=[], raw_errors=[f"LLM call failed: {exc}"])

    proposals: list[CompetitionProposal] = []
    errors: list[str] = []
    proposal_source_ids: dict[str, list[str]] = {}
    all_source_ids: set[str] = set()
    for item in data.get("proposals", []) or []:
        proposal, reason = llm_dict_to_proposal(
            item, team_id=team_id, strategy_id=strategy_id, agent_id=agent_id
        )
        if proposal is not None:
            proposals.append(proposal)
            cited = [str(s).strip() for s in (item.get("research_source_ids") or []) if str(s).strip()]
            if cited:
                proposal_source_ids[proposal.proposal_id] = cited
                all_source_ids.update(cited)
        elif reason:
            errors.append(reason)

    learning_update = data.get("learning_update") if isinstance(data.get("learning_update"), dict) else None
    watchlist = data.get("watchlist") if isinstance(data.get("watchlist"), list) else None
    research_notes = data.get("research_notes") if isinstance(data.get("research_notes"), list) else None
    portfolio_decision = (
        data.get("portfolio_decision") if isinstance(data.get("portfolio_decision"), dict) else None
    )

    return ProposalBundle(
        proposals=proposals,
        market_summary=(str(data["market_summary"]) if data.get("market_summary") else None),
        learning_update=learning_update,
        research_notes=research_notes,
        hypothesis=(str(data["hypothesis"]) if data.get("hypothesis") else None),
        watchlist=[str(s).strip().upper() for s in watchlist] if watchlist else None,
        active_strategy=(str(data["strategy_id"]) if data.get("strategy_id") else strategy_id),
        raw_errors=errors,
        proposal_source_ids=proposal_source_ids,
        research_source_ids=sorted(all_source_ids),
        portfolio_decision=portfolio_decision,
    )
