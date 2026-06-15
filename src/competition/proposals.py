"""Competition proposal schemas (Part 3).

A single strict, ``extra="forbid"`` proposal model covers all eight proposal
types agents may emit. The model is the boundary between LLM-generated text and
the deterministic risk engine: LLMs may fill these fields, but the schema
validates them and the risk engine — never the LLM — decides approval and size.

Proposal types
--------------
1. stock_long
2. stock_short
3. margin_stock_long
4. margin_stock_short
5. option_long_call
6. option_long_put
7. option_debit_spread
8. option_defined_risk_spread
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProposalType(str, Enum):
    STOCK_LONG = "stock_long"
    STOCK_SHORT = "stock_short"
    MARGIN_STOCK_LONG = "margin_stock_long"
    MARGIN_STOCK_SHORT = "margin_stock_short"
    OPTION_LONG_CALL = "option_long_call"
    OPTION_LONG_PUT = "option_long_put"
    OPTION_DEBIT_SPREAD = "option_debit_spread"
    OPTION_DEFINED_RISK_SPREAD = "option_defined_risk_spread"


STOCK_TYPES = frozenset(
    {
        ProposalType.STOCK_LONG,
        ProposalType.STOCK_SHORT,
        ProposalType.MARGIN_STOCK_LONG,
        ProposalType.MARGIN_STOCK_SHORT,
    }
)
SHORT_TYPES = frozenset({ProposalType.STOCK_SHORT, ProposalType.MARGIN_STOCK_SHORT})
MARGIN_TYPES = frozenset({ProposalType.MARGIN_STOCK_LONG, ProposalType.MARGIN_STOCK_SHORT})
OPTION_TYPES = frozenset(
    {
        ProposalType.OPTION_LONG_CALL,
        ProposalType.OPTION_LONG_PUT,
        ProposalType.OPTION_DEBIT_SPREAD,
        ProposalType.OPTION_DEFINED_RISK_SPREAD,
    }
)
SPREAD_TYPES = frozenset({ProposalType.OPTION_DEBIT_SPREAD, ProposalType.OPTION_DEFINED_RISK_SPREAD})


class DataProvenance(str, Enum):
    LIVE = "live"
    DELAYED = "delayed"
    FIXTURE = "fixture"
    UNKNOWN = "unknown"


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class LegSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class OptionLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: LegSide
    option_type: OptionType
    strike: float = Field(gt=0.0)
    expiration: date
    estimated_premium: float = Field(gt=0.0, description="Per-share option premium for this leg.")


class CompetitionProposal(BaseModel):
    """Strict, deterministic-friendly proposal model for all eight types."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: str(uuid4()))

    # Identity / required common fields.
    team_id: str
    agent_id: str
    strategy_id: str
    proposal_type: ProposalType
    symbol: str = Field(description="Stock symbol or option underlying.")
    action: str
    thesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    estimated_price: float = Field(gt=0.0, description="Reference price of the underlying/asset.")
    quote_reference: str | None = None
    intended_holding_period: str
    max_loss_thesis: str
    invalidation_condition: str
    expected_catalyst: str
    risk_notes: str
    data_sources: list[str] = Field(min_length=1)
    data_provenance: DataProvenance

    # Sizing intent (deterministic engine computes the actual approved size).
    target_weight: float | None = Field(default=None, gt=0.0, le=1.0)
    max_loss_estimate: float | None = Field(default=None, gt=0.0)

    # Short / margin fields.
    gross_exposure_impact: float | None = Field(default=None, ge=0.0)
    net_exposure_impact: float | None = Field(default=None)
    borrow_availability_assumption: str | None = None
    stop_level: float | None = Field(default=None, gt=0.0)

    # Options fields.
    underlying: str | None = None
    expiration: date | None = None
    legs: list[OptionLeg] = Field(default_factory=list)
    contracts: int | None = Field(default=None, gt=0)
    contract_multiplier: int = Field(default=100, gt=0)
    net_premium_per_contract: float | None = Field(default=None, gt=0.0)
    max_premium_at_risk: float | None = Field(default=None, gt=0.0)
    max_profit: float | None = None
    max_loss: float | None = Field(default=None, gt=0.0)
    spread_width: float | None = Field(default=None, gt=0.0)
    greeks: dict[str, float] | None = None
    greeks_available: bool = False
    assignment_exercise_risk_note: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- normalizers ---

    @field_validator("team_id", "agent_id", "strategy_id", "action")
    @classmethod
    def _required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("required identity field must not be empty")
        return cleaned

    @field_validator("symbol", "underlying")
    @classmethod
    def _upper_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("symbol must not be empty")
        return cleaned

    @field_validator(
        "thesis",
        "intended_holding_period",
        "max_loss_thesis",
        "invalidation_condition",
        "expected_catalyst",
        "risk_notes",
    )
    @classmethod
    def _required_narrative(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("required narrative field must not be empty")
        return cleaned

    @field_validator("data_sources")
    @classmethod
    def _clean_sources(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("at least one data source is required")
        return cleaned

    # --- structural validation per type ---

    @model_validator(mode="after")
    def _validate_shape(self) -> "CompetitionProposal":
        pt = self.proposal_type

        if pt in OPTION_TYPES:
            if self.expiration is None:
                raise ValueError("options proposals require an expiration")
            if not self.legs:
                raise ValueError("options proposals require at least one leg")
            if self.contracts is None:
                raise ValueError("options proposals require a contracts count")
            if self.net_premium_per_contract is None:
                raise ValueError("options proposals require net_premium_per_contract")
            if not (self.assignment_exercise_risk_note and self.assignment_exercise_risk_note.strip()):
                raise ValueError("options proposals require an assignment/exercise risk note")
            if self.underlying is None:
                object.__setattr__(self, "underlying", self.symbol)
            if pt in SPREAD_TYPES and len(self.legs) < 2:
                raise ValueError("spread proposals require at least two legs")
        else:
            if self.target_weight is None:
                raise ValueError("stock proposals require target_weight for deterministic sizing")

        if pt in SHORT_TYPES:
            if not (self.borrow_availability_assumption and self.borrow_availability_assumption.strip()):
                raise ValueError("short proposals require a borrow/availability assumption")
            if self.stop_level is None:
                raise ValueError("short proposals require a stop/invalidation level")
            if self.max_loss_estimate is None:
                raise ValueError("short proposals require a max loss estimate")
            if self.gross_exposure_impact is None or self.net_exposure_impact is None:
                raise ValueError("short proposals require gross/net exposure impact")

        if pt in MARGIN_TYPES:
            if self.gross_exposure_impact is None or self.net_exposure_impact is None:
                raise ValueError("margin proposals require gross/net exposure impact")

        return self

    # --- helpers ---

    @property
    def is_short(self) -> bool:
        return self.proposal_type in SHORT_TYPES

    @property
    def is_margin(self) -> bool:
        return self.proposal_type in MARGIN_TYPES

    @property
    def is_option(self) -> bool:
        return self.proposal_type in OPTION_TYPES

    @property
    def is_spread(self) -> bool:
        return self.proposal_type in SPREAD_TYPES

    @property
    def has_naked_short_leg(self) -> bool:
        """True if any short option leg is not covered by a long leg of the same type."""

        if not self.is_option:
            return False
        long_calls = sum(1 for leg in self.legs if leg.side == LegSide.LONG and leg.option_type == OptionType.CALL)
        short_calls = sum(1 for leg in self.legs if leg.side == LegSide.SHORT and leg.option_type == OptionType.CALL)
        long_puts = sum(1 for leg in self.legs if leg.side == LegSide.LONG and leg.option_type == OptionType.PUT)
        short_puts = sum(1 for leg in self.legs if leg.side == LegSide.SHORT and leg.option_type == OptionType.PUT)
        return short_calls > long_calls or short_puts > long_puts

    def dte(self, as_of: date | None = None) -> int:
        if self.expiration is None:
            raise ValueError("non-option proposal has no expiration")
        reference = as_of or date.today()
        return (self.expiration - reference).days

    def computed_premium_at_risk(self) -> float:
        """Deterministic total dollars at risk for an options proposal.

        For long options and defined-risk debit spreads, max loss equals the net
        debit paid: net_premium_per_contract * contracts * multiplier.
        """

        if not self.is_option or self.net_premium_per_contract is None or self.contracts is None:
            raise ValueError("computed_premium_at_risk only applies to options proposals")
        return self.net_premium_per_contract * self.contracts * self.contract_multiplier
