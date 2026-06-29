"""Wiring for the LLM-driven weekly competition cycle (Tasks 1, 5, 6, 8).

Builds the allowlisted, provenance-tagged research context for a team — now
including live research results (Alpaca News / OpenAI web) and performance
feedback from prior cycles — and returns a proposal source callable for
``run_week_cycle``. Research runs are logged. No secrets ever enter the context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.agents.llm_provider import LLMProvider
from src.agents.llm_proposal_agent import generate_llm_proposals
from src.agents.llm_review_agents import LLMReviewFlags, team_debate_context
from src.brokers.alpaca_client import AlpacaClientWrapper
from src.competition.attribution import DEFAULT_ATTRIBUTION_DIR, performance_feedback
from src.competition.daily_review import (
    DEFAULT_REVIEWS_DIR,
    daily_review_context,
    load_daily_spy_attribution,
)
from src.learning.strategy_memory import DEFAULT_TEAM_MEMORY_DIR, strategy_memory_context
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
from src.research.market_data import PriceFn, latest_prices
from src.research.research import (
    AlpacaNewsFn,
    OpenAIWebFn,
    ResearchRunResult,
    plan_research,
    run_research,
)
from src.research.research_config import ResearchConfig
from src.research.research_log import DEFAULT_RESEARCH_DIR, log_research


def build_llm_context(
    team_id: str,
    *,
    client: AlpacaClientWrapper | None = None,
    price_fn: PriceFn | None = None,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
    team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR,
    research_run: ResearchRunResult | None = None,
    watchlist: tuple[str, ...] = (),
    review_flags: LLMReviewFlags | None = None,
    env: Any | None = None,
) -> dict[str, Any]:
    """Assemble allowlisted, provenance-tagged context for the LLM. No secrets."""

    scorecard = load_latest_scorecard(team_id, scorecard_dir)
    ledger = TeamLearningLedger.load(team_id, learning_dir)
    watch = watchlist or ("SPY", "QQQ", "AAPL", "MSFT", "NVDA")
    flags = review_flags or LLMReviewFlags.from_env(env)

    feedback = performance_feedback(team_id, attribution_dir=attribution_dir)

    # Compact, deterministic multi-day strategy memory + advisory team debate.
    # Both are RESEARCH FEEDBACK ONLY; neither authorizes bypassing risk.
    strategy_memory = strategy_memory_context(team_id, team_memory_dir=team_memory_dir)
    debate_enabled = flags.critique_agent or flags.review_agent
    try:
        debate_attribution = load_daily_spy_attribution(
            team_id, scorecard_dir=scorecard_dir, attribution_dir=attribution_dir
        )
    except Exception:  # noqa: BLE001 - debate context is best-effort, never crashes the cycle
        debate_attribution = None
    team_debate = team_debate_context(
        team_id,
        attribution=debate_attribution,
        feedback=feedback,
        enabled=debate_enabled,
        env=env,
    )

    research_block: dict[str, Any] = {"available": False, "results": [], "note": "no research run"}
    if research_run is not None:
        research_block = {
            "available": research_run.available,
            "provider": research_run.provider,
            "results": [r.as_dict() for r in research_run.results],
            "errors": research_run.errors,
            "note": research_run.status_message,
        }

    account_block = alpaca_account_status(client).as_dict()
    positions_block = alpaca_positions(client).as_dict()
    market_block = alpaca_market_clock(client).as_dict()

    # Phase 7X: deterministic BOUNDED memory retrieval is the durable-memory channel
    # for the prompt (curated playbook + last-N daily summaries + scorecard snapshot +
    # working memory + constraints). It excludes raw audit JSONL, unbounded agent
    # responses, and old chat history by construction. The legacy ledger fields below
    # remain only as compact, last-8 compatibility context.
    from src.competition.attribution import load_team_attribution
    from src.competition.memory_config import MemoryConfig
    from src.competition.prompt_memory import build_bounded_prompt_memory

    raw_positions = []
    try:
        raw_positions = list(client.get_positions()) if client is not None and client.has_credentials() else []
    except Exception:  # noqa: BLE001 - degrade to no positions; bounded memory still builds
        raw_positions = []
    try:
        attribution_entries = load_team_attribution(team_id, attribution_dir=attribution_dir)
    except Exception:  # noqa: BLE001
        attribution_entries = []
    bounded_memory, memory_metadata = build_bounded_prompt_memory(
        team_id,
        account=(account_block.get("value") if isinstance(account_block, dict) else None),
        raw_positions=raw_positions,
        attribution_entries=attribution_entries,
        market_session=(market_block.get("value") if isinstance(market_block, dict) else None),
        scorecard_snapshot=(scorecard.as_dict() if scorecard else None),
        config=MemoryConfig.from_env(env if isinstance(env, dict) else None),
    )

    return {
        "team_id": team_id,
        "account": account_block,
        "positions": positions_block,
        "market_clock": market_block,
        "watchlist_prices": latest_prices(watch, price_fn),
        "prior_scorecard": scorecard.as_dict() if scorecard else None,
        # Bounded durable memory (Phase 7X) — the authoritative memory channel.
        "bounded_memory": bounded_memory,
        "memory_metadata": memory_metadata,
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
        "performance_feedback": feedback,
        "daily_review": daily_review_context(team_id, reviews_dir=reviews_dir, learning_dir=learning_dir),
        "strategy_memory": strategy_memory,
        "team_debate": team_debate,
        "research": research_block,
    }


def build_llm_proposal_source(
    team_id: str,
    *,
    provider: LLMProvider,
    strategy_id: str,
    client: AlpacaClientWrapper | None = None,
    price_fn: PriceFn | None = None,
    research_config: ResearchConfig | None = None,
    alpaca_news_fn: AlpacaNewsFn | None = None,
    openai_web_fn: OpenAIWebFn | None = None,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
    competition_dir: Path | str = DEFAULT_COMPETITION_DIR,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
    team_memory_dir: Path | str = DEFAULT_TEAM_MEMORY_DIR,
    research_dir: Path | str = DEFAULT_RESEARCH_DIR,
) -> Callable[[str], ProposalBundle]:
    """Return a ``run_week_cycle``-compatible proposal source backed by the LLM."""

    research_config = research_config or ResearchConfig.from_env()

    def source(tid: str) -> ProposalBundle:
        queries = plan_research(tid, research_config)
        research_run = run_research(
            tid,
            queries,
            config=research_config,
            alpaca_news_fn=alpaca_news_fn,
            openai_web_fn=openai_web_fn,
        )
        context = build_llm_context(
            tid,
            client=client,
            price_fn=price_fn,
            scorecard_dir=scorecard_dir,
            learning_dir=learning_dir,
            competition_dir=competition_dir,
            attribution_dir=attribution_dir,
            reviews_dir=reviews_dir,
            team_memory_dir=team_memory_dir,
            research_run=research_run,
            watchlist=research_config.watchlist,
        )
        # Phase 7X: record bounded prompt-memory metadata (no raw prompt text/secrets)
        # so the iteration audit can show what memory the live prompt used.
        try:
            from src.competition.prompt_memory import record_prompt_memory_metadata

            meta = context.get("memory_metadata")
            if isinstance(meta, dict):
                record_prompt_memory_metadata(tid, meta)
        except Exception:  # noqa: BLE001 - metadata is best-effort, never blocks proposals
            pass
        bundle = generate_llm_proposals(
            tid, provider=provider, context=context, strategy_id=strategy_id
        )
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        log_research(
            research_run,
            cycle_id=cycle_id,
            proposal_source="llm",
            proposal_ids=[p.proposal_id for p in bundle.proposals],
            research_dir=research_dir,
        )
        return bundle

    return source
