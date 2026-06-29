"""Conservative paper-only portfolio-management limits (Phase 7V).

These bound the new position-management surface (trims / sell-to-close exits /
capital rotation) the same way ``TradingPermissions`` bounds entries. Every limit
is configurable via env with a safe default, and long-entry vs. sell-to-close
permissions are tracked *separately* so diagnostics/config show exactly what is
allowed.

Hard safety properties (unchanged by this module):

* Paper-only. Nothing here enables shorting, options execution, margin execution,
  or live trading.
* Sell-to-close is permitted ONLY to reduce or fully close an EXISTING long stock
  position. It can never open or increase a short.
* "Reduce gross exposure" must be bounded and explainable  -  never an unbounded
  auto-liquidation of the whole book.

No secrets are read or stored here.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from src.config.permissions import _read_bool, _read_float, _read_int


@dataclass(frozen=True)
class PortfolioLimits:
    """Bounds for the paper portfolio-management stage. Conservative defaults."""

    # Concentration / exposure (fractions of equity).
    max_position_pct: float = 0.20
    max_portfolio_gross_exposure_pct: float = 1.0  # 1.0 == fully invested, no leverage

    # Per-day action caps (separate from entry-order caps so trims/exits are bounded).
    max_position_trims_per_day: int = 3
    max_position_exits_per_day: int = 2
    max_capital_rotations_per_day: int = 1

    # Shared daily order/notional caps for the team (entries + exits combined).
    max_daily_orders_per_team: int = 10
    max_daily_notional_per_team: float = 100_000.0

    # Emergency protection: when buying power as a fraction of equity drops below
    # this, the portfolio is flagged for capital-freeing review (trim/exit). This
    # NEVER auto-liquidates  -  it only permits bounded, explainable reductions and
    # blocks new-money buys until room is freed.
    emergency_buying_power_pct: float = 0.05
    # A single position exceeding this fraction of equity is flagged as a
    # concentration risk (advisory; trims must still pass deterministic checks).
    concentration_alert_pct: float = 0.25

    # Separately-visible permission flags. Long entry stays as-is; sell-to-close is
    # OFF by default so enabling position reductions is an explicit operator choice.
    enable_paper_long_entry: bool = True
    enable_paper_sell_to_close: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PortfolioLimits":
        if env is None:
            env = os.environ
        return cls(
            max_position_pct=_read_float(env, "MAX_POSITION_PCT", 0.20),
            max_portfolio_gross_exposure_pct=_read_float(
                env, "MAX_PORTFOLIO_GROSS_EXPOSURE_PCT", 1.0
            ),
            max_position_trims_per_day=_read_int(env, "MAX_POSITION_TRIMS_PER_DAY", 3),
            max_position_exits_per_day=_read_int(env, "MAX_POSITION_EXITS_PER_DAY", 2),
            max_capital_rotations_per_day=_read_int(env, "MAX_CAPITAL_ROTATIONS_PER_DAY", 1),
            max_daily_orders_per_team=_read_int(env, "MAX_DAILY_ORDERS_PER_TEAM", 10),
            max_daily_notional_per_team=_read_float(
                env, "MAX_DAILY_NOTIONAL_PER_TEAM", 100_000.0
            ),
            emergency_buying_power_pct=_read_float(env, "EMERGENCY_BUYING_POWER_PCT", 0.05),
            concentration_alert_pct=_read_float(env, "CONCENTRATION_ALERT_PCT", 0.25),
            enable_paper_long_entry=_read_bool(env, "ENABLE_PAPER_STOCKS", True),
            enable_paper_sell_to_close=_read_bool(env, "ENABLE_PAPER_SELL_TO_CLOSE", False),
        )

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = (
            "Paper-only. Sell-to-close reduces/closes existing long stock only; "
            "no shorting, options, margin, or live trading."
        )
        return data


__all__ = ["PortfolioLimits"]
