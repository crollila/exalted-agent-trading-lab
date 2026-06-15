"""Wiring for the LLM-driven weekly competition cycle (Tasks 1, 5, 8).

Builds the allowlisted, provenance-tagged research context for a team and returns
a proposal source callable suitable for ``run_week_cycle``. The context is
assembled only from allowlisted read-only tools and local memory — it never
contains secrets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.llm_provider import LLMProvider
from src.agents.llm_proposal_agent import generate_llm_proposals
from src.brokers.alpaca_client import AlpacaClientWrapper
from src.competition.scorecard import DEFAULT_SCORECARD_DIR, load_latest_scorecard
from src.competition.week_competition import (
    DEFAULT_COMPETITION_DIR,
    ProposalBundle,
    competition_status,
)
from src.learning.team_memory import DEFAULT_LEARNING_DIR, TeamLearningLedger
from src.research.data_tools import (
    alpaca_account_status,
    alpaca_market_clock,
    alpaca_positions,
)
from src.research.market_data import WEEK_COMPETITION_WATCHLIST, PriceFn, latest_prices
from src.research.news import NewsConfig, fetch_news


def build_llm_context(
    team_id: str,
    *,
    client: AlpacaClientWrapper | None = None,
    price_fn: PriceFn | None = None,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    news_config: NewsConfig | None = None,
    watchlist: tuple[str, ...] = WEEK_COMPETITION_WATCHLIST,
) -> dict[str, Any]:
    """Assemble allowlisted, provenance-tagged context for the LLM. No secrets."""

    scorecard = load_latest_scorecard(team_id, scorecard_dir)
    ledger = TeamLearningLedger.load(team_id, learning_dir)

    return {
        "team_id": team_id,
        "account": alpaca_account_status(client).as_dict(),
        "positions": alpaca_positions(client).as_dict(),
        "market_clock": alpaca_market_clock(client).as_dict(),
        "watchlist_prices": latest_prices(watchlist, price_fn),
        "prior_scorecard": scorecard.as_dict() if scorecard else None,
        "team_memory": {
            "current_hypothesis": ledger.current_hypothesis,
            "active_strategy": ledger.active_strategy,
            "watchlist": ledger.watchlist,
            "latest_lessons": ledger.latest_lessons(8),
            "strategy_changes": ledger.strategy_changes[-8:],
            "risk_notes": ledger.risk_notes[-8:],
            "rejected_ideas": ledger.rejected_ideas[-8:],
        },
        "competition_status": competition_status(competition_dir, scorecard_dir),
        "news": fetch_news(watchlist, config=news_config),
    }


def build_llm_proposal_source(
    team_id: str,
    *,
    provider: LLMProvider,
    strategy_id: str,
    client: AlpacaClientWrapper | None = None,
    price_fn: PriceFn | None = None,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    news_config: NewsConfig | None = None,
) -> Callable[[str], ProposalBundle]:
    """Return a ``run_week_cycle``-compatible proposal source backed by the LLM."""

    def source(tid: str) -> ProposalBundle:
        context = build_llm_context(
            tid,
            client=client,
            price_fn=price_fn,
            scorecard_dir=scorecard_dir,
            learning_dir=learning_dir,
            competition_dir=competition_dir,
            news_config=news_config,
        )
        return generate_llm_proposals(
            tid, provider=provider, context=context, strategy_id=strategy_id
        )

    return source
