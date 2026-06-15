"""Explicit, paper-only trading permission levels.

This module is the single deterministic source of truth for which advanced
paper-trading surfaces (shorting, margin, options) are unlocked. Every advanced
behavior must be gated on these flags. Defaults are paper-only and conservative:
all advanced permissions are OFF unless explicitly enabled in the environment.

LLMs/agents must never construct or mutate these permissions. They are read from
config only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import load_dotenv


def _read_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _read_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _read_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


class PermissionLevel:
    """Named paper-trading permission levels (documentation/UI labels)."""

    PAPER_STOCKS = 1
    PAPER_SHORTING = 2
    PAPER_MARGIN = 3
    PAPER_OPTIONS = 4


@dataclass(frozen=True)
class TradingPermissions:
    """Deterministic permission + risk-cap configuration.

    All advanced permissions default to ``False`` (fail-closed). ``trading_mode``
    must be ``paper`` for any advanced surface to be considered enabled.
    """

    trading_mode: str = "paper"

    # Level flags.
    enable_paper_stocks: bool = True
    enable_paper_shorting: bool = False
    enable_paper_margin: bool = False
    enable_paper_options: bool = False

    # Per-team activity caps.
    max_daily_orders_per_team: int = 3
    max_daily_loss_pct_per_team: float = 0.02

    # Position / exposure caps.
    max_position_weight: float = 0.20
    max_gross_exposure: float = 1.50
    max_net_exposure: float = 1.20
    max_short_exposure: float = 0.30
    max_single_short_weight: float = 0.10

    # Options caps.
    max_options_premium_at_risk: float = 0.02
    max_options_contracts_per_trade: int = 2
    min_options_dte: int = 7
    allow_naked_options: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TradingPermissions":
        if env is None:
            load_dotenv()
            env = os.environ

        return cls(
            trading_mode=(env.get("TRADING_MODE", "paper").strip().lower() or "paper"),
            enable_paper_stocks=_read_bool(env, "ENABLE_PAPER_STOCKS", True),
            enable_paper_shorting=_read_bool(env, "ENABLE_PAPER_SHORTING", False),
            enable_paper_margin=_read_bool(env, "ENABLE_PAPER_MARGIN", False),
            enable_paper_options=_read_bool(env, "ENABLE_PAPER_OPTIONS", False),
            max_daily_orders_per_team=_read_int(env, "MAX_DAILY_ORDERS_PER_TEAM", 3),
            max_daily_loss_pct_per_team=_read_float(env, "MAX_DAILY_LOSS_PCT_PER_TEAM", 0.02),
            max_position_weight=_read_float(env, "MAX_POSITION_WEIGHT", 0.20),
            max_gross_exposure=_read_float(env, "MAX_GROSS_EXPOSURE", 1.50),
            max_net_exposure=_read_float(env, "MAX_NET_EXPOSURE", 1.20),
            max_short_exposure=_read_float(env, "MAX_SHORT_EXPOSURE", 0.30),
            max_single_short_weight=_read_float(env, "MAX_SINGLE_SHORT_WEIGHT", 0.10),
            max_options_premium_at_risk=_read_float(env, "MAX_OPTIONS_PREMIUM_AT_RISK", 0.02),
            max_options_contracts_per_trade=_read_int(env, "MAX_OPTIONS_CONTRACTS_PER_TRADE", 2),
            min_options_dte=_read_int(env, "MIN_OPTIONS_DTE", 7),
            allow_naked_options=_read_bool(env, "ALLOW_NAKED_OPTIONS", False),
        )

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == "paper"

    # --- Effective level checks (paper-mode gated) ---

    def stocks_enabled(self) -> bool:
        return self.is_paper and self.enable_paper_stocks

    def shorting_enabled(self) -> bool:
        return self.is_paper and self.enable_paper_shorting

    def margin_enabled(self) -> bool:
        return self.is_paper and self.enable_paper_margin

    def options_enabled(self) -> bool:
        return self.is_paper and self.enable_paper_options

    def enabled_levels(self) -> tuple[int, ...]:
        levels: list[int] = []
        if self.stocks_enabled():
            levels.append(PermissionLevel.PAPER_STOCKS)
        if self.shorting_enabled():
            levels.append(PermissionLevel.PAPER_SHORTING)
        if self.margin_enabled():
            levels.append(PermissionLevel.PAPER_MARGIN)
        if self.options_enabled():
            levels.append(PermissionLevel.PAPER_OPTIONS)
        return tuple(levels)

    def summary(self) -> dict[str, object]:
        return {
            "trading_mode": self.trading_mode,
            "is_paper": self.is_paper,
            "paper_stocks": self.stocks_enabled(),
            "paper_shorting": self.shorting_enabled(),
            "paper_margin": self.margin_enabled(),
            "paper_options": self.options_enabled(),
            "allow_naked_options": self.allow_naked_options,
            "caps": {
                "max_daily_orders_per_team": self.max_daily_orders_per_team,
                "max_daily_loss_pct_per_team": self.max_daily_loss_pct_per_team,
                "max_position_weight": self.max_position_weight,
                "max_gross_exposure": self.max_gross_exposure,
                "max_net_exposure": self.max_net_exposure,
                "max_short_exposure": self.max_short_exposure,
                "max_single_short_weight": self.max_single_short_weight,
                "max_options_premium_at_risk": self.max_options_premium_at_risk,
                "max_options_contracts_per_trade": self.max_options_contracts_per_trade,
                "min_options_dte": self.min_options_dte,
            },
        }
