"""Deterministic learning-from-outcomes + playbook promotion gate (Phase 7W).

Generates structured *learning candidates* by linking each decision (entry / hold
/ trim / exit / rejected) to its thesis, confidence, and later realized/unrealized
outcome, then promotes a candidate to the durable playbook ONLY when it clears
deterministic evidence rules:

* non-empty supporting evidence references,
* explicit confidence > 0,
* repeated evidence across multiple decisions, OR a single high-impact, clearly
  documented success/failure,
* not contradicted by newer evidence (the playbook supersedes on contradiction).

An LLM may phrase a candidate, but it can never invent a permanent lesson without
evidence — promotion is pure Python. Nothing here trades or changes settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.competition.playbook import TeamPlaybook
from src.competition.position_review import TeamPortfolioReview

MIN_EVIDENCE_FOR_PROMOTION = 2  # "repeated across multiple decisions"
HIGH_IMPACT_DRAWDOWN = -0.15
HIGH_IMPACT_GAIN = 0.25


@dataclass
class LearningCandidate:
    category: str
    text: str
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    action_type: str | None = None
    regime: str | None = None
    impact: str = "normal"  # normal | high
    supporting_count: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category, "text": self.text, "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs), "symbols": list(self.symbols),
            "action_type": self.action_type, "regime": self.regime,
            "impact": self.impact, "supporting_count": self.supporting_count,
        }


@dataclass
class PromotionResult:
    promoted: list[str] = field(default_factory=list)      # lesson texts promoted/strengthened
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (text, reason)

    def as_dict(self) -> dict[str, Any]:
        return {"promoted": list(self.promoted),
                "skipped": [{"text": t, "reason": r} for t, r in self.skipped]}


def generate_candidates(
    review: TeamPortfolioReview,
    *,
    attribution_entries: list[Any] | None = None,
    regime: str | None = None,
) -> list[LearningCandidate]:
    """Build evidence-grounded learning candidates from the day's review/outcomes.

    Every candidate references concrete evidence (position symbol + thesis source
    proposal id). No candidate is fabricated without a backing decision.
    """

    candidates: list[LearningCandidate] = []

    # 1) High-impact failures: longs held through a deep drawdown / invalidated thesis.
    deep_losers = [
        p for p in review.positions
        if p.side == "long" and p.unrealized_pl_pct is not None
        and p.unrealized_pl_pct <= HIGH_IMPACT_DRAWDOWN
    ]
    for p in deep_losers:
        candidates.append(LearningCandidate(
            category="mistake",
            text=(f"Holding {p.symbol} through a {p.unrealized_pl_pct:.0%} drawdown without a tighter "
                  "invalidation level was a documented mistake; define and act on the stop earlier."),
            confidence=0.7,
            evidence_refs=[r for r in [p.thesis_source_proposal_id, f"pos:{p.symbol}"] if r],
            symbols=[p.symbol], action_type="hold", regime=regime, impact="high",
            supporting_count=1,
        ))

    # 2) Concentration as a recurring risk lesson (repeated across positions).
    over = review.health.concentration_alerts
    if over:
        candidates.append(LearningCandidate(
            category="risk_lesson",
            text=("Concentration repeatedly exceeded the alert threshold; size new and existing "
                  "positions toward the per-position cap to control single-name risk."),
            confidence=0.6,
            evidence_refs=[f"pos:{s}" for s in over],
            symbols=list(over), action_type="trim", regime=regime,
            impact="high" if len(over) >= 3 else "normal",
            supporting_count=len(over),
        ))

    # 3) Capital exhaustion / over-deployment.
    if review.health.negative_cash or review.health.zero_buying_power:
        candidates.append(LearningCandidate(
            category="risk_lesson",
            text=("Buying power was exhausted / cash went negative; reserve capital and free room via "
                  "trims/exits before adding new exposure."),
            confidence=0.65,
            evidence_refs=["account:negative_cash" if review.health.negative_cash else "account:zero_bp"],
            action_type="exit", regime=regime, impact="high", supporting_count=1,
        ))

    # 4) Recurring winners with intact theses (strength), grounded in outcomes.
    strong = [
        p for p in review.positions
        if p.side == "long" and p.thesis_status == "intact"
        and p.unrealized_pl_pct is not None and p.unrealized_pl_pct >= HIGH_IMPACT_GAIN
    ]
    for p in strong:
        candidates.append(LearningCandidate(
            category="strength",
            text=(f"{p.symbol}-style intact-thesis winners (+{p.unrealized_pl_pct:.0%}) rewarded patience; "
                  "let validated winners run within position limits."),
            confidence=0.6,
            evidence_refs=[r for r in [p.thesis_source_proposal_id, f"pos:{p.symbol}"] if r],
            symbols=[p.symbol], action_type="hold", regime=regime, impact="high", supporting_count=1,
        ))

    # 5) Broker rejection patterns from attribution (repeated -> evidence).
    rejections: dict[str, list[str]] = {}
    for e in (attribution_entries or []):
        if getattr(e, "broker_rejected", False):
            cat = str(getattr(e, "failure_category", "") or "broker_error")
            rejections.setdefault(cat, []).append(str(getattr(e, "proposal_id", "") or getattr(e, "symbol", "")))
    for cat, refs in rejections.items():
        candidates.append(LearningCandidate(
            category="failure_mode",
            text=f"Repeated broker rejections of type '{cat}'; pre-check this constraint before submitting.",
            confidence=0.6, evidence_refs=sorted(set(refs)), action_type="entry",
            regime=regime, impact="normal", supporting_count=len(refs),
        ))

    return candidates


def promote_candidates(
    playbook: TeamPlaybook,
    candidates: list[LearningCandidate],
    *,
    min_evidence: int = MIN_EVIDENCE_FOR_PROMOTION,
    now: datetime | None = None,
) -> PromotionResult:
    """Promote only candidates that clear deterministic evidence rules."""

    now = now or datetime.now(timezone.utc)
    result = PromotionResult()
    for c in candidates:
        if not c.evidence_refs:
            result.skipped.append((c.text, "no supporting evidence references"))
            continue
        if c.confidence is None or c.confidence <= 0:
            result.skipped.append((c.text, "no explicit positive confidence"))
            continue
        repeated = c.supporting_count >= min_evidence
        high_impact = c.impact == "high"
        if not (repeated or high_impact):
            result.skipped.append((c.text, f"insufficient evidence (need {min_evidence} or high impact)"))
            continue
        playbook.upsert(
            category=c.category, text=c.text, confidence=c.confidence,
            evidence_refs=c.evidence_refs, symbols=c.symbols,
            action_type=c.action_type, regime=c.regime, now=now,
        )
        result.promoted.append(c.text)
    return result


__all__ = [
    "LearningCandidate", "PromotionResult",
    "generate_candidates", "promote_candidates",
    "MIN_EVIDENCE_FOR_PROMOTION",
]
