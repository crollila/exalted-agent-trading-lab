"""Reconcile fresh broker state against historical memory (Phase 7Z).

Historical daily summaries, lessons, prior theses, old scorecards, and playbook
memory are **research feedback only**. They must never override current broker
facts about positions, cash, buying power, exposure, or active holdings.

This module deterministically compares the immutable current-cycle
:class:`BrokerSnapshot` against signals extracted from historical memory and
marks conflicts such as:

* a historical XYZ/SPY holding vs a broker position count of zero,
* a historical low-buying-power claim vs currently healthy buying power,
* historical short exposure vs no current short position.

Old history is preserved for audit but tagged *inactive/stale for the current
cycle*. Nothing here trades, calls an LLM, or prints secrets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from src.competition.broker_snapshot import BrokerSnapshot

# Reconciliation statuses (stable strings used by tests, audit, diagnostics).
CLEAN = "clean"
STALE_CONTEXT_CORRECTED = "stale_context_corrected"
ACCOUNT_STATE_UNAVAILABLE = "account_state_unavailable"
LIVE_PORTFOLIO_HEALTH_BLOCK = "live_portfolio_health_block"

ALL_STATUSES = (CLEAN, STALE_CONTEXT_CORRECTED, ACCOUNT_STATE_UNAVAILABLE, LIVE_PORTFOLIO_HEALTH_BLOCK)

# Conflict kinds.
CONFLICT_STALE_HOLDING = "stale_holding"
CONFLICT_STALE_LOW_BUYING_POWER = "stale_low_buying_power"
CONFLICT_STALE_SHORT_EXPOSURE = "stale_short_exposure"


@dataclass(frozen=True)
class StateConflict:
    """One historical claim that the live broker snapshot refutes."""

    kind: str
    historical_claim: str
    current_fact: str
    resolution: str = "use_current_broker_state"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def warning(self) -> str:
        return f"STALE MEMORY ({self.kind}): {self.historical_claim} -> CURRENT: {self.current_fact}."


@dataclass
class HistoricalSignals:
    """Signals extracted from historical memory (research feedback only)."""

    referenced_symbols: list[str] = field(default_factory=list)
    claims_low_buying_power: bool = False
    claims_short_exposure: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    team_id: str
    status: str
    account_read_ok: bool
    conflicts: list[StateConflict] = field(default_factory=list)
    current_position_count: int | None = None
    current_held_symbols: list[str] = field(default_factory=list)
    current_short_position_count: int | None = None
    current_low_buying_power: bool = False
    health_block_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def warnings(self) -> list[str]:
        """Compact conflict warnings for the bounded prompt (never raw memory)."""

        return [c.warning() for c in self.conflicts]

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "status": self.status,
            "account_read_ok": self.account_read_ok,
            "conflicts": [c.as_dict() for c in self.conflicts],
            "current_position_count": self.current_position_count,
            "current_held_symbols": list(self.current_held_symbols),
            "current_short_position_count": self.current_short_position_count,
            "current_low_buying_power": self.current_low_buying_power,
            "health_block_reason": self.health_block_reason,
            "notes": list(self.notes),
        }


def _norm_symbols(symbols: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for sym in symbols or []:
        s = str(sym or "").strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def reconcile_state(
    snapshot: BrokerSnapshot,
    historical: HistoricalSignals,
    *,
    low_buying_power_threshold_pct: float = 0.15,
    current_health_block: bool = False,
    current_health_reason: str | None = None,
) -> ReconciliationResult:
    """Compare the live snapshot to historical memory signals.

    Returns a :class:`ReconciliationResult`. When the live account could not be
    read the status is ``account_state_unavailable`` and NO stale-correction is
    asserted (we cannot refute history without current facts). When current
    health genuinely blocks new buys the status is ``live_portfolio_health_block``
    (a *current* condition, never derived from stale memory).
    """

    # Account unavailable: never invent a flat/funded book; defer to "unknown".
    if not snapshot.is_available:
        return ReconciliationResult(
            team_id=snapshot.team_id,
            status=ACCOUNT_STATE_UNAVAILABLE,
            account_read_ok=False,
            current_position_count=None,
            current_held_symbols=[],
            current_short_position_count=None,
            current_low_buying_power=False,
            notes=[
                "Live account unavailable; current positions/cash/buying power are UNKNOWN. "
                "Do not treat positions as zero or cash as available."
            ],
        )

    held = _norm_symbols(snapshot.held_symbols)
    bp_ratio = snapshot.buying_power_ratio()
    current_low_bp = bp_ratio is not None and bp_ratio < low_buying_power_threshold_pct

    conflicts: list[StateConflict] = []

    # 1) Historical holdings the live book no longer contains.
    for sym in _norm_symbols(historical.referenced_symbols):
        if sym not in held:
            conflicts.append(
                StateConflict(
                    kind=CONFLICT_STALE_HOLDING,
                    historical_claim=f"memory references holding {sym}",
                    current_fact=(
                        f"broker shows {snapshot.position_count} open position(s); {sym} not held"
                    ),
                )
            )

    # 2) Historical low-buying-power claim vs current healthy buying power.
    if historical.claims_low_buying_power and not current_low_bp:
        bp_txt = "unknown" if snapshot.buying_power is None else f"${snapshot.buying_power:,.0f}"
        conflicts.append(
            StateConflict(
                kind=CONFLICT_STALE_LOW_BUYING_POWER,
                historical_claim="memory claims low buying power",
                current_fact=f"current buying power healthy ({bp_txt})",
            )
        )

    # 3) Historical short exposure vs no current short position.
    if historical.claims_short_exposure and (snapshot.short_position_count or 0) == 0:
        conflicts.append(
            StateConflict(
                kind=CONFLICT_STALE_SHORT_EXPOSURE,
                historical_claim="memory references short exposure",
                current_fact="broker shows no current short position",
            )
        )

    # Current health genuinely blocks new buys -> a live (not stale) condition.
    if current_health_block:
        status = LIVE_PORTFOLIO_HEALTH_BLOCK
    elif conflicts:
        status = STALE_CONTEXT_CORRECTED
    else:
        status = CLEAN

    notes: list[str] = []
    if conflicts:
        notes.append(
            "Historical memory preserved for audit but tagged stale for this cycle; "
            "current broker state is authoritative."
        )

    return ReconciliationResult(
        team_id=snapshot.team_id,
        status=status,
        account_read_ok=True,
        conflicts=conflicts,
        current_position_count=snapshot.position_count,
        current_held_symbols=held,
        current_short_position_count=snapshot.short_position_count,
        current_low_buying_power=current_low_bp,
        health_block_reason=current_health_reason if current_health_block else None,
        notes=notes,
    )


def historical_signals_from_memory(
    *,
    prior_held_symbols: Iterable[str] | None = None,
    ledger_watchlist: Iterable[str] | None = None,
    ledger_lessons: Iterable[str] | None = None,
    scorecard: Any | None = None,
) -> HistoricalSignals:
    """Derive historical signals from common memory sources (best-effort, pure).

    ``prior_held_symbols`` come from prior submitted-entry theses / old position
    reviews. Lessons/scorecard text are scanned only for *claims* of low buying
    power or short exposure — these are never treated as current facts.
    """

    referenced = _norm_symbols(prior_held_symbols)

    lessons_text = " ".join(str(x) for x in (ledger_lessons or [])).lower()
    claims_low_bp = any(
        kw in lessons_text
        for kw in ("low buying power", "insufficient buying power", "buying power exhausted", "no buying power")
    )
    claims_short = "short" in lessons_text

    if scorecard is not None:
        short_exposure = getattr(scorecard, "short_exposure", None)
        try:
            if short_exposure is not None and float(short_exposure) > 0:
                claims_short = True
        except (TypeError, ValueError):
            pass
        bp = getattr(scorecard, "buying_power", None)
        starting = getattr(scorecard, "starting_equity", None)
        try:
            if bp is not None and starting and float(starting) > 0 and (float(bp) / float(starting)) < 0.15:
                claims_low_bp = True
        except (TypeError, ValueError):
            pass

    return HistoricalSignals(
        referenced_symbols=referenced,
        claims_low_buying_power=claims_low_bp,
        claims_short_exposure=claims_short,
    )


__all__ = [
    "CLEAN",
    "STALE_CONTEXT_CORRECTED",
    "ACCOUNT_STATE_UNAVAILABLE",
    "LIVE_PORTFOLIO_HEALTH_BLOCK",
    "ALL_STATUSES",
    "CONFLICT_STALE_HOLDING",
    "CONFLICT_STALE_LOW_BUYING_POWER",
    "CONFLICT_STALE_SHORT_EXPOSURE",
    "StateConflict",
    "HistoricalSignals",
    "ReconciliationResult",
    "reconcile_state",
    "historical_signals_from_memory",
]
