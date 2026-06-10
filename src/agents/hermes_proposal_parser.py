from __future__ import annotations

import json
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Mapping

from pydantic import ValidationError

from src.agents.schemas import HermesProposalBatch
from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.risk.risk_rules import RiskRules


HERMES_WEALTH_ADVISOR_STRATEGY_ID = "hermes_wealth_advisor_v1"


@dataclass(frozen=True)
class HermesProposalParseResult:
    proposals: list[TradeProposal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    strategy_id: str | None = None
    portfolio_notes: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


class HermesProposalParser:
    """
    Converts strict Hermes JSON into TradeProposal objects only.

    This parser intentionally has no broker, Alpaca, order, network, or LLM
    dependency. Execution remains the responsibility of the risk and order layers.
    """

    def __init__(
        self,
        rules: RiskRules | None = None,
        allowed_strategy_ids: tuple[str, ...] = (HERMES_WEALTH_ADVISOR_STRATEGY_ID,),
    ):
        self.rules = rules or RiskRules()
        self.allowed_strategy_ids = allowed_strategy_ids

    def parse(
        self,
        raw_json: str,
        estimated_prices: Mapping[str, float],
    ) -> HermesProposalParseResult:
        try:
            payload = json.loads(raw_json)
        except JSONDecodeError as exc:
            return HermesProposalParseResult(errors=[f"Invalid JSON: {exc.msg}."])

        try:
            batch = HermesProposalBatch.model_validate(payload)
        except ValidationError as exc:
            return HermesProposalParseResult(errors=self._validation_errors(exc))

        errors = self._policy_errors(batch=batch, estimated_prices=estimated_prices)
        if errors:
            return HermesProposalParseResult(
                errors=errors,
                strategy_id=batch.strategy_id,
                portfolio_notes=batch.portfolio_notes,
            )

        proposals = [
            TradeProposal(
                strategy_id=batch.strategy_id,
                symbol=proposal.symbol,
                action=proposal.action,
                asset_class=proposal.asset_class,
                target_weight=proposal.target_weight,
                estimated_price=estimated_prices[proposal.symbol],
                thesis=proposal.thesis,
                confidence=proposal.confidence,
            )
            for proposal in batch.proposals
        ]

        return HermesProposalParseResult(
            proposals=proposals,
            strategy_id=batch.strategy_id,
            portfolio_notes=batch.portfolio_notes,
        )

    def _validation_errors(self, exc: ValidationError) -> list[str]:
        errors = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            errors.append(f"Invalid Hermes proposal at {location}: {error['msg']}.")
        return errors or ["Invalid Hermes proposal payload."]

    def _policy_errors(
        self,
        batch: HermesProposalBatch,
        estimated_prices: Mapping[str, float],
    ) -> list[str]:
        errors: list[str] = []

        if batch.strategy_id not in self.allowed_strategy_ids:
            errors.append(f"Rejected: unsupported Hermes strategy_id '{batch.strategy_id}'.")

        for index, proposal in enumerate(batch.proposals):
            label = f"proposals.{index}.{proposal.symbol}"

            if proposal.action != TradeAction.BUY:
                errors.append(f"Rejected {label}: only buy proposals are allowed in Phase 5.")

            if proposal.asset_class == AssetClass.OPTION:
                errors.append(f"Rejected {label}: options are disabled.")
            elif proposal.asset_class != AssetClass.STOCK:
                errors.append(f"Rejected {label}: only stock proposals are allowed.")

            if proposal.target_weight <= 0:
                errors.append(f"Rejected {label}: target_weight must be greater than 0.")
            if proposal.target_weight > self.rules.max_position_pct:
                errors.append(
                    f"Rejected {label}: target_weight exceeds max position weight "
                    f"{self.rules.max_position_pct:.2f}."
                )

            estimated_price = estimated_prices.get(proposal.symbol)
            if estimated_price is None:
                errors.append(f"Rejected {label}: missing local estimated price.")
            elif estimated_price <= 0:
                errors.append(f"Rejected {label}: local estimated price must be greater than 0.")

        return errors
