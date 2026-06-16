"""Strict off-hours quiet mode for the cheap competition loop (Phase 7T).

Outside trading hours the operator wants the process to *stay alive* but go
quiet: no LLM calls, no Alpaca live-equity refresh, no attribution refresh, no
daily-review export, and no Discord posts. The loop should print one concise
sleep notice per closed-market stretch and then sleep.

This module only carries *configuration* — the deterministic risk engine, team
credentials, and the kill switch remain authoritative regardless of these
flags. Each ``ALLOW_OFF_HOURS_*`` flag defaults to ``False`` so the quiet
default is genuinely quiet; setting one to ``true`` re-enables only that single
action while the rest stay quiet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# Env names (kept here so the CLI status command and the loop agree).
STRICT_ENV = "STRICT_MARKET_HOURS_ONLY"
ALLOW_STATUS_ENV = "ALLOW_OFF_HOURS_STATUS_REFRESH"
ALLOW_ATTRIBUTION_ENV = "ALLOW_OFF_HOURS_ATTRIBUTION_REFRESH"
ALLOW_LIVE_EQUITY_ENV = "ALLOW_OFF_HOURS_LIVE_EQUITY_REFRESH"
ALLOW_DISCORD_ENV = "ALLOW_OFF_HOURS_DISCORD"
ALLOW_LLM_REVIEW_ENV = "ALLOW_OFF_HOURS_LLM_REVIEW"
POST_ONE_SLEEP_NOTICE_ENV = "OFF_HOURS_POST_ONE_SLEEP_NOTICE"

OFF_HOURS_SLEEP_NOTICE = "Market closed; strict quiet mode active. Sleeping until next interval."


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OffHoursQuietConfig:
    """When ``strict_market_hours_only`` is on, the loop goes quiet when closed.

    Every ``allow_off_hours_*`` flag defaults to ``False`` (quiet). Flipping one
    to ``True`` re-enables only that specific action while the market is closed.
    """

    strict_market_hours_only: bool = True
    allow_off_hours_status_refresh: bool = False
    allow_off_hours_attribution_refresh: bool = False
    allow_off_hours_live_equity_refresh: bool = False
    allow_off_hours_discord: bool = False
    allow_off_hours_llm_review: bool = False
    post_one_sleep_notice: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OffHoursQuietConfig":
        env = env if env is not None else os.environ
        return cls(
            strict_market_hours_only=_bool_env(env, STRICT_ENV, True),
            allow_off_hours_status_refresh=_bool_env(env, ALLOW_STATUS_ENV, False),
            allow_off_hours_attribution_refresh=_bool_env(env, ALLOW_ATTRIBUTION_ENV, False),
            allow_off_hours_live_equity_refresh=_bool_env(env, ALLOW_LIVE_EQUITY_ENV, False),
            allow_off_hours_discord=_bool_env(env, ALLOW_DISCORD_ENV, False),
            allow_off_hours_llm_review=_bool_env(env, ALLOW_LLM_REVIEW_ENV, False),
            post_one_sleep_notice=_bool_env(env, POST_ONE_SLEEP_NOTICE_ENV, True),
        )

    def quiet_when_closed(self, market_open: bool | None) -> bool:
        """True when the loop should go quiet this iteration.

        Quiet only kicks in when strict mode is on *and* the market is known to
        be closed (``market_open is False``). An unknown clock never forces
        quiet — the loop keeps its normal best-effort behavior.
        """

        return self.strict_market_hours_only and market_open is False

    def skipped_when_closed(self) -> list[str]:
        """Human-readable list of actions the loop skips when closed + strict.

        Reflects the current allow flags so operators can see exactly what is
        suppressed versus explicitly re-enabled. No secrets.
        """

        if not self.strict_market_hours_only:
            return ["(strict mode off — closed-market behavior unchanged)"]

        skipped: list[str] = []
        if not self.allow_off_hours_attribution_refresh:
            skipped.append("proposal attribution refresh")
        if not self.allow_off_hours_status_refresh:
            skipped.append("week-competition-status / scorecard export")
        if not self.allow_off_hours_live_equity_refresh:
            skipped.append("Alpaca live-equity refresh")
        if not self.allow_off_hours_llm_review:
            skipped.append("LLM review + review-only / full cycles")
        if not self.allow_off_hours_discord:
            skipped.append("team + scoreboard Discord posts")
        return skipped or ["(nothing — all off-hours actions explicitly allowed)"]


__all__ = [
    "OffHoursQuietConfig",
    "OFF_HOURS_SLEEP_NOTICE",
    "STRICT_ENV",
    "ALLOW_STATUS_ENV",
    "ALLOW_ATTRIBUTION_ENV",
    "ALLOW_LIVE_EQUITY_ENV",
    "ALLOW_DISCORD_ENV",
    "ALLOW_LLM_REVIEW_ENV",
    "POST_ONE_SLEEP_NOTICE_ENV",
]
