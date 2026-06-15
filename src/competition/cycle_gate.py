"""Cheap cycle gate (Phase 7N).

Decides whether a full (token-spending) LLM ``run-week-cycle`` is worth running,
using only cheap/local/read-only signals. The goal is cost control: skip full
cycles when nothing material changed and the minimum interval has not elapsed,
but always run when there is a real reason to (major SPY move, urgent portfolio
review, broker rejections to learn from, materially new research).

This module is fully deterministic and never calls a broker, an LLM, or the
network. Callers resolve the cheap signals (from local files / read-only context)
and pass them in; the gate just applies the rules.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.config.permissions import _read_bool, _read_float, _read_int  # reuse parsers


@dataclass(frozen=True)
class CheapCycleGateConfig:
    enabled: bool = False
    min_full_cycle_interval_minutes_alpha: int = 30
    min_full_cycle_interval_minutes_beta: int = 45
    force_full_cycle_on_major_move: bool = True
    major_spy_move_threshold_pct: float = 0.5  # percent (0.5 == 0.5%)
    force_full_cycle_on_low_buying_power: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CheapCycleGateConfig":
        if env is None:
            env = os.environ
        return cls(
            enabled=_read_bool(env, "CHEAP_CYCLE_GATE_ENABLED", False),
            min_full_cycle_interval_minutes_alpha=_read_int(
                env, "MIN_FULL_CYCLE_INTERVAL_MINUTES_ALPHA", 30
            ),
            min_full_cycle_interval_minutes_beta=_read_int(
                env, "MIN_FULL_CYCLE_INTERVAL_MINUTES_BETA", 45
            ),
            force_full_cycle_on_major_move=_read_bool(env, "FORCE_FULL_CYCLE_ON_MAJOR_MOVE", True),
            major_spy_move_threshold_pct=_read_float(env, "MAJOR_SPY_MOVE_THRESHOLD_PCT", 0.5),
            force_full_cycle_on_low_buying_power=_read_bool(
                env, "FORCE_FULL_CYCLE_ON_LOW_BUYING_POWER", False
            ),
        )

    def interval_for(self, team_id: str) -> int:
        if team_id == "team_alpha":
            return self.min_full_cycle_interval_minutes_alpha
        return self.min_full_cycle_interval_minutes_beta


@dataclass
class GateDecision:
    team_id: str
    should_run_full_cycle: bool
    reason: str
    recommended_wait_minutes: int = 0
    trigger_flags: list[str] = field(default_factory=list)
    recommend_review_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _minutes_since(iso_timestamp: str | None, now: datetime) -> float | None:
    if not iso_timestamp:
        return None
    try:
        ts = datetime.fromisoformat(iso_timestamp)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 60.0


def evaluate_cheap_cycle_gate(
    team_id: str,
    *,
    config: CheapCycleGateConfig,
    last_full_cycle_at: str | None = None,
    spy_move_pct: float | None = None,
    low_buying_power: bool = False,
    broker_rejections: int = 0,
    research_changed: bool = False,
    urgent_review: bool = False,
    mode: str = "",
    now: datetime | None = None,
) -> GateDecision:
    """Apply the cheap-gate rules and return a GateDecision.

    ``spy_move_pct`` is in percent (e.g. 0.6 == 0.6%). All inputs are optional and
    degrade safely — missing signals never force or block a cycle on their own.
    """

    now = now or datetime.now(timezone.utc)
    interval = config.interval_for(team_id)
    minutes_since = _minutes_since(last_full_cycle_at, now)
    mode_flag = [f"mode:{mode}"] if mode else []

    # Disabled gate: always allow a full cycle (legacy behavior).
    if not config.enabled:
        return GateDecision(
            team_id=team_id,
            should_run_full_cycle=True,
            reason="Cheap cycle gate disabled; full cycle allowed.",
            recommended_wait_minutes=0,
            trigger_flags=["gate_disabled"],
        )

    # Low buying power / PM urgency recommend a (cheap) REVIEW, never a forced
    # full trading cycle — unless FORCE_FULL_CYCLE_ON_LOW_BUYING_POWER is set.
    review_recommended = bool(urgent_review) or (
        low_buying_power and not config.force_full_cycle_on_low_buying_power
    )

    # --- Hard triggers that force a FULL cycle regardless of interval. ---
    triggers: list[str] = []
    if (
        config.force_full_cycle_on_major_move
        and spy_move_pct is not None
        and abs(spy_move_pct) >= config.major_spy_move_threshold_pct
    ):
        triggers.append("major_spy_move")
    if config.force_full_cycle_on_low_buying_power and low_buying_power:
        triggers.append("low_buying_power_forced")
    if broker_rejections > 0:
        triggers.append("broker_rejections")
    if research_changed:
        triggers.append("research_changed")

    if triggers:
        return GateDecision(
            team_id=team_id,
            should_run_full_cycle=True,
            reason=f"Material change detected ({', '.join(triggers)}); full cycle recommended.",
            recommended_wait_minutes=0,
            trigger_flags=triggers + mode_flag,
            recommend_review_only=review_recommended,
        )

    review_flags = (["low_buying_power_review"] if low_buying_power else []) + (
        ["urgent_review"] if urgent_review else []
    )

    # --- Interval check: never ran -> run; too soon -> skip (review maybe). ---
    if minutes_since is None:
        return GateDecision(
            team_id=team_id,
            should_run_full_cycle=True,
            reason="No prior full cycle recorded; running the first full cycle.",
            recommended_wait_minutes=0,
            trigger_flags=["no_prior_cycle"] + mode_flag,
            recommend_review_only=review_recommended,
        )

    if minutes_since < interval:
        wait = max(1, int(round(interval - minutes_since)))
        review_note = (
            " A portfolio review (run --review-only) is recommended due to "
            f"{', '.join(review_flags)}." if review_recommended else ""
        )
        return GateDecision(
            team_id=team_id,
            should_run_full_cycle=False,
            reason=(
                f"Nothing material changed and only {minutes_since:.0f}m since the last full cycle "
                f"(min {interval}m for {team_id}); staying cheap.{review_note}"
            ),
            recommended_wait_minutes=wait,
            trigger_flags=["interval_not_elapsed"] + review_flags + mode_flag,
            recommend_review_only=review_recommended,
        )

    return GateDecision(
        team_id=team_id,
        should_run_full_cycle=True,
        reason=f"Minimum interval elapsed ({minutes_since:.0f}m >= {interval}m); full cycle recommended.",
        recommended_wait_minutes=0,
        trigger_flags=["interval_elapsed"] + review_flags + mode_flag,
        recommend_review_only=review_recommended,
    )
