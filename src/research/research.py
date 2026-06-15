"""Allowlisted research engine: planning, Alpaca News, OpenAI web (Tasks 2-4).

* Planner builds team-specific research questions (Alpha=momentum/catalyst,
  Beta=contrarian/risk).
* Providers (alpaca / openai_web / hybrid) fetch results under hard caps.
* No arbitrary scraping: only the Alpaca News API and the OpenAI web-search tool.
* No broker/order access. No secrets in results or logs.
* Unavailable providers return a structured status, never crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.research.research_config import ResearchConfig

# Injected fetcher signatures (kept simple + mockable).
AlpacaNewsFn = Callable[[list[str], datetime, int], list[dict[str, Any]]]
OpenAIWebFn = Callable[[str, str, int], list[dict[str, Any]]]


@dataclass(frozen=True)
class ResearchQuery:
    query: str
    reason: str
    tickers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"query": self.query, "reason": self.reason, "tickers": self.tickers}


@dataclass(frozen=True)
class ResearchResult:
    provider: str
    query: str
    title: str
    summary: str
    url: str | None
    published_at: str | None
    tickers: list[str]
    freshness: str
    source_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "query": self.query,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at,
            "tickers": self.tickers,
            "freshness": self.freshness,
            "source_id": self.source_id,
        }


@dataclass
class ResearchRunResult:
    team_id: str
    provider: str
    available: bool
    queries: list[ResearchQuery] = field(default_factory=list)
    results: list[ResearchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status_message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "provider": self.provider,
            "available": self.available,
            "queries": [q.as_dict() for q in self.queries],
            "results": [r.as_dict() for r in self.results],
            "errors": self.errors,
            "status_message": self.status_message,
        }

    def source_ids(self) -> list[str]:
        return [r.source_id for r in self.results]


# --- Task 4: team-specific research planning ---

_ALPHA_TEMPLATES = [
    ("latest {a} {b} momentum breakout catalyst stock news today", "Alpha momentum/catalyst leadership"),
    ("AI semiconductors mega-cap growth strongest stocks news today", "Alpha AI/semis growth"),
    ("unusual strength high-beta leadership stock market news today", "Alpha unusual strength"),
    ("positive earnings guidance upgrades {a} {b} news", "Alpha positive catalysts"),
]
_BETA_TEMPLATES = [
    ("overbought downgrade negative catalyst {a} {b} stock news today", "Beta contrarian/mean-reversion"),
    ("market fragility risk-off defensive quality stocks news today", "Beta market fragility/hedging"),
    ("{a} {b} bad news profit warning short interest news", "Beta negative catalysts"),
    ("mean reversion stretched valuation pullback risk news today", "Beta overbought risk"),
]


def plan_research(team_id: str, config: ResearchConfig) -> list[ResearchQuery]:
    watch = list(config.watchlist) or ["SPY", "QQQ"]
    templates = _ALPHA_TEMPLATES if team_id == "team_alpha" else _BETA_TEMPLATES
    queries: list[ResearchQuery] = []
    for index, (template, reason) in enumerate(templates):
        a = watch[(index * 2) % len(watch)]
        b = watch[(index * 2 + 1) % len(watch)]
        queries.append(
            ResearchQuery(query=template.format(a=a, b=b), reason=reason, tickers=[a, b])
        )
    return queries[: config.max_queries_per_team]


# --- Provider parsing helpers ---


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _parse_alpaca_item(raw: Any, query: str, source_id: str) -> ResearchResult:
    return ResearchResult(
        provider="alpaca",
        query=query,
        title=str(_get(raw, "headline") or _get(raw, "title") or "").strip() or "(no title)",
        summary=str(_get(raw, "summary") or "").strip(),
        url=(str(_get(raw, "url")) if _get(raw, "url") else None),
        published_at=(str(_get(raw, "published_at") or _get(raw, "created_at") or "") or None),
        tickers=[str(s).upper() for s in (_get(raw, "symbols") or _get(raw, "tickers") or [])],
        freshness="live",
        source_id=source_id,
    )


def _parse_web_item(raw: Any, query: str, source_id: str) -> ResearchResult:
    return ResearchResult(
        provider="openai_web",
        query=query,
        title=str(_get(raw, "title") or "").strip() or "(no title)",
        summary=str(_get(raw, "summary") or _get(raw, "snippet") or "").strip(),
        url=(str(_get(raw, "url")) if _get(raw, "url") else None),
        published_at=(str(_get(raw, "published_at")) if _get(raw, "published_at") else None),
        tickers=[str(s).upper() for s in (_get(raw, "tickers") or [])],
        freshness="live",
        source_id=source_id,
    )


# --- Default (real) fetchers ---


def build_alpaca_news_fn(api_key: str | None, secret_key: str | None) -> AlpacaNewsFn:
    if not (api_key and secret_key):
        raise RuntimeError("Alpaca credentials required for news.")

    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    client = NewsClient(api_key=api_key, secret_key=secret_key)

    def fetch(tickers: list[str], start: datetime, limit: int) -> list[dict[str, Any]]:
        request = NewsRequest(symbols=",".join(tickers) if tickers else None, start=start, limit=limit)
        news_set = client.get_news(request)
        items = getattr(news_set, "data", None)
        if isinstance(items, dict):
            items = items.get("news", [])
        return list(items or [])

    return fetch


def build_openai_web_fn(api_key: str | None) -> OpenAIWebFn:
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY required for web research.")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    def fetch(query: str, model: str, max_results: int) -> list[dict[str, Any]]:
        # Uses the Responses API web-search tool — not arbitrary scraping.
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=(
                f"Search the web for: {query}. Return up to {max_results} recent, relevant "
                "financial news items with title, url, a one-sentence summary, and tickers."
            ),
        )
        text = getattr(response, "output_text", None) or ""
        # Best-effort: return a single summarized item carrying the model's text.
        return [{"title": query, "summary": text[:800], "url": None, "tickers": []}] if text else []

    return fetch


# --- Task 1-3: provider dispatch with caps ---


def run_research(
    team_id: str,
    queries: list[ResearchQuery],
    *,
    config: ResearchConfig,
    alpaca_news_fn: AlpacaNewsFn | None = None,
    openai_web_fn: OpenAIWebFn | None = None,
    now: datetime | None = None,
) -> ResearchRunResult:
    if not config.available:
        return ResearchRunResult(
            team_id=team_id,
            provider=config.provider,
            available=False,
            queries=queries,
            status_message="research unavailable (disabled or no provider configured)",
        )

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=config.lookback_hours)
    capped_queries = queries[: config.max_queries_per_team]
    results: list[ResearchResult] = []
    errors: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"r{counter}"

    for query in capped_queries:
        if config.uses_alpaca and alpaca_news_fn is not None:
            try:
                raw_items = alpaca_news_fn(query.tickers, start, config.max_results_per_query)
                for raw in raw_items[: config.max_results_per_query]:
                    results.append(_parse_alpaca_item(raw, query.query, next_id()))
            except Exception as exc:  # noqa: BLE001 - degrade, never crash the cycle
                errors.append(f"alpaca news failed for {query.tickers}: {exc}")
        if config.uses_openai_web and openai_web_fn is not None:
            try:
                raw_items = openai_web_fn(query.query, config.openai_web_model, config.max_results_per_query)
                for raw in raw_items[: config.max_results_per_query]:
                    results.append(_parse_web_item(raw, query.query, next_id()))
            except Exception as exc:  # noqa: BLE001 - degrade, never crash the cycle
                errors.append(f"openai web failed for query: {exc}")

    if config.provider == "hybrid":
        results = _dedupe(results)

    available = bool(results) or not errors
    status = (
        f"{len(results)} result(s) from provider '{config.provider}'"
        if results
        else "no results returned"
    )
    return ResearchRunResult(
        team_id=team_id,
        provider=config.provider,
        available=available,
        queries=capped_queries,
        results=results,
        errors=errors,
        status_message=status,
    )


def _dedupe(results: list[ResearchResult]) -> list[ResearchResult]:
    seen: set[tuple[str, str | None]] = set()
    deduped: list[ResearchResult] = []
    for result in results:
        key = (result.title.strip().lower(), result.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped
