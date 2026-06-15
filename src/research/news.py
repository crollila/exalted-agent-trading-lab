"""Allowlisted news/research provider scaffold (Task 7).

Safe by default: there is no arbitrary scraping, no broker access, and no secrets
in prompts. Live news is opt-in via ``ENABLE_LIVE_NEWS_RESEARCH`` + ``NEWS_PROVIDER``.
When no provider is configured (the default), news is reported as unavailable and
agents must treat it as ``unknown`` — never invented.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from dotenv import load_dotenv

from src.competition.proposals import DataProvenance

SUPPORTED_NEWS_PROVIDERS = ("none",)


@dataclass(frozen=True)
class NewsConfig:
    enabled: bool = False
    provider: str = "none"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "NewsConfig":
        if env is None:
            load_dotenv()
            env = os.environ
        enabled_raw = (env.get("ENABLE_LIVE_NEWS_RESEARCH", "false") or "false").strip().lower()
        return cls(
            enabled=enabled_raw == "true",
            provider=(env.get("NEWS_PROVIDER", "none") or "none").strip().lower(),
        )

    @property
    def available(self) -> bool:
        return self.enabled and self.provider not in ("", "none")


def news_provider_status(config: NewsConfig | None = None) -> dict[str, Any]:
    config = config or NewsConfig.from_env()
    if not config.available:
        message = "news research unavailable (disabled or no provider configured)"
    else:
        message = f"news provider '{config.provider}' enabled"
    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "available": config.available,
        "message": message,
    }


def fetch_news(
    symbols: tuple[str, ...],
    *,
    config: NewsConfig | None = None,
    fetcher: Callable[[tuple[str, ...]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return provenance-tagged news context.

    When no provider/fetcher is configured, returns ``unknown`` rather than
    inventing headlines. A ``fetcher`` may be injected for tests.
    """

    config = config or NewsConfig.from_env()
    if not config.available or fetcher is None:
        return {
            "provenance": DataProvenance.UNKNOWN.value,
            "provider": config.provider,
            "items": [],
            "note": "news research unavailable; treat as unknown",
        }
    try:
        items = fetcher(symbols)
    except Exception as exc:  # noqa: BLE001 - degrade to unknown, never invent
        return {
            "provenance": DataProvenance.UNKNOWN.value,
            "provider": config.provider,
            "items": [],
            "note": f"news fetch failed: {exc}",
        }
    return {
        "provenance": DataProvenance.LIVE.value,
        "provider": config.provider,
        "items": items,
        "note": None,
    }
