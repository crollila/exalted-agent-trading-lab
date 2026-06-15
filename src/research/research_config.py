"""Research provider configuration (Task 1).

Controls the allowlisted research layer: Alpaca News and/or OpenAI web search.
Everything is opt-in and capped. No secrets are stored here beyond what the
provider layer reads from credentials at call time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from dotenv import load_dotenv

SUPPORTED_RESEARCH_PROVIDERS = ("none", "alpaca", "openai_web", "hybrid")
DEFAULT_RESEARCH_WATCHLIST = (
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMD",
    "META",
    "GOOGL",
    "AMZN",
)


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ResearchConfig:
    enable_live_news: bool = False
    provider: str = "none"
    enable_openai_web: bool = False
    openai_web_model: str = "gpt-5.4-mini"
    max_queries_per_team: int = 5
    max_results_per_query: int = 5
    lookback_hours: int = 24
    watchlist: tuple[str, ...] = DEFAULT_RESEARCH_WATCHLIST

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ResearchConfig":
        if env is None:
            load_dotenv()
            env = os.environ
        provider = (env.get("NEWS_PROVIDER", "none") or "none").strip().lower()
        if provider not in SUPPORTED_RESEARCH_PROVIDERS:
            provider = "none"
        watchlist_raw = (env.get("RESEARCH_WATCHLIST") or "").strip()
        watchlist = (
            tuple(s.strip().upper() for s in watchlist_raw.split(",") if s.strip())
            if watchlist_raw
            else DEFAULT_RESEARCH_WATCHLIST
        )
        return cls(
            enable_live_news=_bool(env, "ENABLE_LIVE_NEWS_RESEARCH", False),
            provider=provider,
            enable_openai_web=_bool(env, "ENABLE_OPENAI_WEB_RESEARCH", False),
            openai_web_model=(env.get("OPENAI_WEB_RESEARCH_MODEL") or "gpt-5.4-mini"),
            max_queries_per_team=_int(env, "MAX_RESEARCH_QUERIES_PER_TEAM_PER_CYCLE", 5),
            max_results_per_query=_int(env, "MAX_RESEARCH_RESULTS_PER_QUERY", 5),
            lookback_hours=_int(env, "RESEARCH_LOOKBACK_HOURS", 24),
            watchlist=watchlist,
        )

    @property
    def uses_alpaca(self) -> bool:
        return self.enable_live_news and self.provider in ("alpaca", "hybrid")

    @property
    def uses_openai_web(self) -> bool:
        return (
            self.enable_live_news
            and self.enable_openai_web
            and self.provider in ("openai_web", "hybrid")
        )

    @property
    def available(self) -> bool:
        return self.uses_alpaca or self.uses_openai_web

    def status(self) -> dict[str, object]:
        if not self.available:
            message = "research unavailable (disabled or no provider configured)"
        else:
            parts = []
            if self.uses_alpaca:
                parts.append("alpaca news")
            if self.uses_openai_web:
                parts.append(f"openai web ({self.openai_web_model})")
            message = "research enabled: " + " + ".join(parts)
        return {
            "enabled": self.enable_live_news,
            "provider": self.provider,
            "available": self.available,
            "uses_alpaca": self.uses_alpaca,
            "uses_openai_web": self.uses_openai_web,
            "openai_web_model": self.openai_web_model,
            "max_queries_per_team": self.max_queries_per_team,
            "max_results_per_query": self.max_results_per_query,
            "lookback_hours": self.lookback_hours,
            "message": message,
        }
