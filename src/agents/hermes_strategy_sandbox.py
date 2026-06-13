from __future__ import annotations

import json
from collections import Counter
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.risk.options_models import OptionAction, OptionProposal
from src.risk.shorting_models import ShortAction, ShortProposal


PAPER_ELIGIBLE_STOCK_LONG = "paper_eligible_stock_long"
SIMULATION_ONLY_SHORT = "simulation_only_short"
SIMULATION_ONLY_OPTION = "simulation_only_option"
SIMULATION_ONLY_MARGIN = "simulation_only_margin"
REJECTED = "rejected"

KNOWN_PROPOSAL_TYPES = {"stock_long", "short_stock", "option_long", "margin"}
ROUTE_ORDER = (
    PAPER_ELIGIBLE_STOCK_LONG,
    SIMULATION_ONLY_SHORT,
    SIMULATION_ONLY_OPTION,
    SIMULATION_ONLY_MARGIN,
    REJECTED,
)


class HermesSandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    team_id: str
    strategy_id: str
    agent_role: str
    proposals: list[dict[str, Any]]
    strategy_notes: str | None = None
    learning_goal: str | None = None

    @field_validator("agent_id", "team_id", "strategy_id", "agent_role")
    @classmethod
    def required_text_must_not_be_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("strategy_notes", "learning_goal")
    @classmethod
    def optional_text_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("must not be empty when provided")
        return text

    @field_validator("proposals")
    @classmethod
    def proposals_must_not_be_empty(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("must not be empty")
        return value


class _TextValidatedProposal(BaseModel):
    @field_validator("symbol", check_fields=False)
    @classmethod
    def symbol_must_not_be_empty(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be empty")
        return symbol

    @field_validator("thesis", check_fields=False)
    @classmethod
    def thesis_must_not_be_empty(cls, value: str) -> str:
        thesis = value.strip()
        if not thesis:
            raise ValueError("thesis must not be empty")
        return thesis


class StockLongSandboxProposal(_TextValidatedProposal):
    model_config = ConfigDict(extra="forbid")

    proposal_type: Literal["stock_long"]
    symbol: str
    target_weight: float | None = Field(default=None, gt=0.0, le=1.0)
    quantity: float | None = Field(default=None, gt=0.0)
    estimated_price: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def target_or_quantity_is_required(self) -> "StockLongSandboxProposal":
        if self.target_weight is None and self.quantity is None:
            raise ValueError("target_weight or quantity is required")
        return self


class ShortStockSandboxProposal(_TextValidatedProposal):
    model_config = ConfigDict(extra="forbid")

    proposal_type: Literal["short_stock"]
    symbol: str
    target_short_weight: float | None = Field(default=None, gt=0.0, le=1.0)
    notional_exposure: float | None = Field(default=None, gt=0.0)
    estimated_price: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    borrow_available_assumption: bool
    borrow_fee_assumption: float | None = Field(default=None, ge=0.0)
    max_loss_exit_price: float | None = Field(default=None, gt=0.0)
    forced_cover_threshold: float | None = Field(default=None, gt=0.0)


class OptionLongSandboxProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_type: Literal["option_long"]
    contract: dict[str, Any]
    action: OptionAction = OptionAction.BUY_TO_OPEN
    contracts: int = Field(gt=0)
    premium: float = Field(gt=0.0)
    estimated_total_premium: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    liquidity_open_interest_assumption: str
    assignment_exercise_risk_note: str

    @field_validator("thesis", "liquidity_open_interest_assumption", "assignment_exercise_risk_note")
    @classmethod
    def text_must_not_be_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class MarginSandboxProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_type: Literal["margin"]
    requested_gross_exposure: float = Field(gt=0.0)
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    symbols: list[str] = Field(default_factory=list)

    @field_validator("thesis")
    @classmethod
    def thesis_must_not_be_empty(cls, value: str) -> str:
        thesis = value.strip()
        if not thesis:
            raise ValueError("thesis must not be empty")
        return thesis

    @field_validator("symbols")
    @classmethod
    def symbols_must_not_be_empty_strings(cls, value: list[str]) -> list[str]:
        symbols = [symbol.strip().upper() for symbol in value]
        if any(not symbol for symbol in symbols):
            raise ValueError("symbols must not contain empty values")
        return symbols


class RoutedHermesProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_index: int
    proposal_type: str
    route: str
    mapped_proposal: TradeProposal | ShortProposal | OptionProposal | MarginSandboxProposal | None = None
    errors: list[str] = Field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.route != REJECTED


class HermesSandboxResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: HermesSandboxRequest | None = None
    routed_proposals: list[RoutedHermesProposal] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def route_counts(self) -> dict[str, int]:
        counts = Counter(proposal.route for proposal in self.routed_proposals)
        return {route: counts.get(route, 0) for route in ROUTE_ORDER}


def parse_hermes_sandbox_json(raw_json: str) -> HermesSandboxResult:
    try:
        payload = json.loads(raw_json)
    except JSONDecodeError as exc:
        return HermesSandboxResult(errors=[f"Invalid JSON: {exc.msg}."])

    try:
        request = HermesSandboxRequest.model_validate(payload)
    except ValidationError as exc:
        return HermesSandboxResult(errors=_validation_errors(exc, "Invalid Hermes sandbox request"))

    routed = [
        _route_raw_proposal(
            proposal_index=index,
            strategy_id=request.strategy_id,
            raw_proposal=raw_proposal,
        )
        for index, raw_proposal in enumerate(request.proposals)
    ]
    return HermesSandboxResult(request=request, routed_proposals=routed)


def load_hermes_sandbox_file(path: Path | str) -> HermesSandboxResult:
    return parse_hermes_sandbox_json(Path(path).read_text(encoding="utf-8"))


def format_hermes_sandbox_result(result: HermesSandboxResult) -> str:
    lines = ["Hermes Strategy Sandbox Review"]
    if result.request is None:
        lines.append("Request rejected.")
        lines.extend(f"- {error}" for error in result.errors)
        lines.append("Hermes proposals are not execution approval.")
        return "\n".join(lines)

    request = result.request
    lines.extend(
        [
            f"Team ID: {request.team_id}",
            f"Agent ID: {request.agent_id}",
            f"Strategy ID: {request.strategy_id}",
            f"Agent role: {request.agent_role}",
            "Hermes proposals are not execution approval.",
            "Route summary:",
        ]
    )
    counts = result.route_counts()
    lines.extend(f"- {route}: {counts[route]}" for route in ROUTE_ORDER)
    lines.append("Proposal routes:")
    for proposal in result.routed_proposals:
        detail = _proposal_detail(proposal)
        lines.append(f"- proposals.{proposal.proposal_index} {proposal.proposal_type} -> {proposal.route}{detail}")
    return "\n".join(lines)


def _route_raw_proposal(
    proposal_index: int,
    strategy_id: str,
    raw_proposal: dict[str, Any],
) -> RoutedHermesProposal:
    proposal_type = str(raw_proposal.get("proposal_type", "missing"))
    if proposal_type not in KNOWN_PROPOSAL_TYPES:
        return RoutedHermesProposal(
            proposal_index=proposal_index,
            proposal_type=proposal_type,
            route=REJECTED,
            errors=[f"Unknown proposal_type: {proposal_type}."],
        )

    if proposal_type == "stock_long":
        return _route_stock_long(proposal_index, strategy_id, raw_proposal)
    if proposal_type == "short_stock":
        return _route_short_stock(proposal_index, strategy_id, raw_proposal)
    if proposal_type == "option_long":
        return _route_option_long(proposal_index, strategy_id, raw_proposal)
    return _route_margin(proposal_index, raw_proposal)


def _route_stock_long(
    proposal_index: int,
    strategy_id: str,
    raw_proposal: dict[str, Any],
) -> RoutedHermesProposal:
    try:
        proposal = StockLongSandboxProposal.model_validate(raw_proposal)
        mapped = TradeProposal(
            strategy_id=strategy_id,
            symbol=proposal.symbol,
            action=TradeAction.BUY,
            asset_class=AssetClass.STOCK,
            target_weight=proposal.target_weight,
            quantity=proposal.quantity,
            estimated_price=proposal.estimated_price,
            thesis=proposal.thesis,
            confidence=proposal.confidence,
        )
    except ValidationError as exc:
        return _rejected_route(proposal_index, "stock_long", exc, "Invalid stock_long proposal")
    return RoutedHermesProposal(
        proposal_index=proposal_index,
        proposal_type="stock_long",
        route=PAPER_ELIGIBLE_STOCK_LONG,
        mapped_proposal=mapped,
    )


def _route_short_stock(
    proposal_index: int,
    strategy_id: str,
    raw_proposal: dict[str, Any],
) -> RoutedHermesProposal:
    try:
        proposal = ShortStockSandboxProposal.model_validate(raw_proposal)
        mapped = ShortProposal(
            strategy_id=strategy_id,
            symbol=proposal.symbol,
            action=ShortAction.SELL_SHORT,
            asset_class=AssetClass.STOCK,
            target_short_weight=proposal.target_short_weight,
            notional_exposure=proposal.notional_exposure,
            estimated_price=proposal.estimated_price,
            thesis=proposal.thesis,
            confidence=proposal.confidence,
            borrow_available_assumption=proposal.borrow_available_assumption,
            borrow_fee_assumption=proposal.borrow_fee_assumption,
            max_loss_exit_price=proposal.max_loss_exit_price,
            forced_cover_threshold=proposal.forced_cover_threshold,
        )
    except ValidationError as exc:
        return _rejected_route(proposal_index, "short_stock", exc, "Invalid short_stock proposal")
    return RoutedHermesProposal(
        proposal_index=proposal_index,
        proposal_type="short_stock",
        route=SIMULATION_ONLY_SHORT,
        mapped_proposal=mapped,
    )


def _route_option_long(
    proposal_index: int,
    strategy_id: str,
    raw_proposal: dict[str, Any],
) -> RoutedHermesProposal:
    try:
        proposal = OptionLongSandboxProposal.model_validate(raw_proposal)
        mapped = OptionProposal(
            strategy_id=strategy_id,
            contract=proposal.contract,
            action=proposal.action,
            contracts=proposal.contracts,
            premium=proposal.premium,
            estimated_total_premium=proposal.estimated_total_premium,
            thesis=proposal.thesis,
            confidence=proposal.confidence,
            liquidity_open_interest_assumption=proposal.liquidity_open_interest_assumption,
            assignment_exercise_risk_note=proposal.assignment_exercise_risk_note,
        )
    except ValidationError as exc:
        return _rejected_route(proposal_index, "option_long", exc, "Invalid option_long proposal")
    return RoutedHermesProposal(
        proposal_index=proposal_index,
        proposal_type="option_long",
        route=SIMULATION_ONLY_OPTION,
        mapped_proposal=mapped,
    )


def _route_margin(
    proposal_index: int,
    raw_proposal: dict[str, Any],
) -> RoutedHermesProposal:
    try:
        proposal = MarginSandboxProposal.model_validate(raw_proposal)
    except ValidationError as exc:
        return _rejected_route(proposal_index, "margin", exc, "Invalid margin proposal")
    return RoutedHermesProposal(
        proposal_index=proposal_index,
        proposal_type="margin",
        route=SIMULATION_ONLY_MARGIN,
        mapped_proposal=proposal,
    )


def _rejected_route(
    proposal_index: int,
    proposal_type: str,
    exc: ValidationError,
    prefix: str,
) -> RoutedHermesProposal:
    return RoutedHermesProposal(
        proposal_index=proposal_index,
        proposal_type=proposal_type,
        route=REJECTED,
        errors=_validation_errors(exc, prefix),
    )


def _validation_errors(exc: ValidationError, prefix: str) -> list[str]:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        errors.append(f"{prefix} at {location}: {error['msg']}.")
    return errors or [f"{prefix}."]


def _proposal_detail(proposal: RoutedHermesProposal) -> str:
    if proposal.errors:
        return f": {'; '.join(proposal.errors)}"
    mapped = proposal.mapped_proposal
    if isinstance(mapped, TradeProposal):
        return f": {mapped.symbol}"
    if isinstance(mapped, ShortProposal):
        return f": {mapped.symbol}"
    if isinstance(mapped, OptionProposal):
        contract = mapped.contract
        return f": {contract.underlying_symbol} {contract.option_type.value} {contract.expiration} {contract.strike:g}"
    if isinstance(mapped, MarginSandboxProposal):
        return f": requested gross exposure {mapped.requested_gross_exposure:.2f}"
    return ""
