from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from src.agents.hermes_team_registry import format_hermes_team_registry, load_hermes_team_registry_file
from src.agents.hermes_strategy_sandbox import format_hermes_sandbox_result, load_hermes_sandbox_file
from src.agents.hermes_tournament_round import (
    format_hermes_tournament_round,
    run_hermes_tournament_round,
    save_hermes_tournament_round_artifacts,
)
from src.agents.llm_provider import LLMProviderConfig
from src.agents.hermes_runtime import (
    HermesGenerationRequest,
    HermesRuntimeConfig,
    format_hermes_generation_result,
    generate_hermes_proposals,
)
from src.brokers.alpaca_client import AlpacaClientWrapper
from src.brokers.options_adapter import OptionsExecutionAdapter
from src.brokers.paper_auth import (
    CREDENTIAL_SOURCES,
    OK,
    client_for_source,
    diagnose_all,
    diagnose_source,
    settings_for_source,
)
from src.agents.llm_provider import LLMProviderError, build_provider
from src.agents.llm_review_agents import (
    LLMReviewFlags,
    build_team_debate,
    generate_daily_review_narrative,
    review_status,
)
from src.agents.model_routing import build_routed_provider, routing_status
from src.competition.llm_cycle import build_llm_proposal_source
from src.competition.portfolio_manager import PortfolioManagerConfig
from src.competition.proposals import DataProvenance
from src.competition.risk_engine import AccountContext
from src.competition.attribution import (
    default_outcome_threshold,
    load_team_attribution,
    performance_feedback,
    refresh_team_attribution,
)
from src.competition.cycle_gate import CheapCycleGateConfig, evaluate_cheap_cycle_gate
from src.competition.daily_review import (
    export_daily_team_review,
    format_daily_spy_attribution,
    format_daily_team_review,
    load_daily_spy_attribution,
    load_latest_daily_team_review,
)
from src.learning.strategy_memory import format_strategy_memory, update_strategy_memory
from src.research.market_data import build_alpaca_price_fn, latest_price, spy_return
from src.research.research import build_alpaca_news_fn, build_openai_web_fn
from src.research.research_config import ResearchConfig
from src.research.research_log import read_latest_research, research_log_count
from src.competition.scorecard import (
    export_scorecards_markdown,
    load_latest_scorecard,
)
from src.competition.week_competition import (
    WEEK_TEAMS,
    competition_status,
    load_competition_state,
    run_week_cycle,
    start_week_competition,
    stop_week_competition,
)
from src.config.permissions import TradingPermissions
from src.config.settings import Settings
from src.db.database import initialize_database
from src.learning.team_memory import TeamLearningLedger
from src.safety.kill_switch import (
    disengage as kill_switch_disengage,
    engage as kill_switch_engage,
    read_kill_switch,
)
from src.execution.local_runner import SIMULATION_FIXTURES, run_strategy_dry_run
from src.reporting.analysis_notes import create_strategy_analysis_note
from src.reporting.fixture_sweep import (
    format_fixture_sweep,
    save_fixture_sweep_artifacts,
    summarize_fixture_sweep,
)
from src.reporting.fixture_sweep_analysis_notes import create_sweep_analysis_note
from src.reporting.fixture_sweep_leaderboard_export import export_fixture_sweep_leaderboard
from src.reporting.leaderboard_export import export_strategy_leaderboard
from src.reporting.report_generator import format_report, generate_daily_report
from src.reporting.research_decisions import (
    ALLOWED_RESEARCH_DECISIONS,
    DEFAULT_DECISION_LEDGER_PATH,
    read_research_decision_ledger,
    record_research_decision,
)
from src.reporting.shorting_simulation_report import (
    DEFAULT_SHORT_SIMULATION_REPORT_PATH,
    export_shorting_simulation_report,
)
from src.reporting.strategy_status import (
    ALLOWED_STRATEGY_STATUSES,
    ALLOWED_STATUS_FILTER_VALUES,
    DEFAULT_STRATEGY_STATUS_PATH,
    StrategyStatusFilter,
    filter_strategy_ids_by_status,
    format_status_filter_summary,
    load_latest_strategy_statuses,
    parse_status_filter_values,
    read_strategy_status_registry,
    set_strategy_status,
    status_filter_to_metadata,
)
from src.reporting.strategy_comparison import (
    format_strategy_comparison,
    rank_strategy_reports,
    save_strategy_comparison_artifacts,
)
from src.reporting.tournament_champion import format_tournament_champion, load_tournament_champion
from src.reporting.tournament_history import format_tournament_history, load_tournament_history
from src.strategies.base import Strategy
from src.strategies.cash_only import CashOnlyStrategy
from src.strategies.hermes_fixtures import (
    HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID,
    HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
    HermesAggressiveFixtureStrategy,
    HermesConservativeFixtureStrategy,
)
from src.strategies.momentum_v1 import MomentumV1Strategy
from src.strategies.spy_buy_hold import SpyBuyHoldStrategy


HERMES_FIXTURE_STRATEGIES = (
    HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID,
    HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID,
)
KNOWN_STRATEGIES = ("cash_only", "spy_buy_hold", "momentum_v1", *HERMES_FIXTURE_STRATEGIES)
DEFAULT_COMPARISON_STRATEGIES = ("cash_only", "spy_buy_hold", "momentum_v1")
COMPARISON_FIXTURES = SIMULATION_FIXTURES
FIXTURE_SWEEP_FIXTURES = tuple(fixture for fixture in COMPARISON_FIXTURES if fixture != "flat")


def run_init_db() -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    print(f"Initialized database at {settings.database_path}")


def build_strategy(strategy_name: str) -> Strategy:
    if strategy_name == "cash_only":
        return CashOnlyStrategy()
    if strategy_name == "spy_buy_hold":
        return SpyBuyHoldStrategy()
    if strategy_name == "momentum_v1":
        return MomentumV1Strategy()
    if strategy_name == HERMES_CONSERVATIVE_FIXTURE_STRATEGY_ID:
        return HermesConservativeFixtureStrategy()
    if strategy_name == HERMES_AGGRESSIVE_FIXTURE_STRATEGY_ID:
        return HermesAggressiveFixtureStrategy()
    raise ValueError(f"Unknown strategy: {strategy_name}")


def run_dry_run(strategy_name: str = "spy_buy_hold") -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    strategy = build_strategy(strategy_name)
    result = run_strategy_dry_run(strategy, settings)

    print(
        f"Dry run complete. Strategy: {result.strategy_id}. "
        f"Run ID: {result.run_id}. Proposals processed: {result.proposal_count}. Daily report logged."
    )


_SOURCE_LABELS = {
    "global": "ALPACA_API_KEY / ALPACA_SECRET_KEY",
    "team_alpha": "TEAM_ALPHA_ALPACA_API_KEY / TEAM_ALPHA_ALPACA_SECRET_KEY",
    "team_beta": "TEAM_BETA_ALPACA_API_KEY / TEAM_BETA_ALPACA_SECRET_KEY",
}


def run_paper_status(team: str = "global") -> None:
    if team not in CREDENTIAL_SOURCES:
        print(f"Unknown --team '{team}'. Use one of: {', '.join(CREDENTIAL_SOURCES)}.")
        raise SystemExit(1)

    permissions = TradingPermissions.from_env()

    print("=== Paper trading permissions (paper-only; no live trading) ===")
    summary = permissions.summary()
    print(f"TRADING_MODE: {summary['trading_mode']} (is_paper={summary['is_paper']})")
    print(f"Level 1 paper stocks: {'ENABLED' if summary['paper_stocks'] else 'disabled'}")
    print(f"Level 2 paper shorting: {'ENABLED' if summary['paper_shorting'] else 'disabled'}")
    print(f"Level 3 paper margin: {'ENABLED' if summary['paper_margin'] else 'disabled'}")
    print(f"Level 4 paper options: {'ENABLED' if summary['paper_options'] else 'disabled'}")
    print(read_kill_switch().describe())
    print("")

    print(f"=== Account status for credential source: {team} ===")
    print(f"Using credential pair: {_SOURCE_LABELS[team]}")
    diagnosis = diagnose_source(team)
    if not diagnosis.auth_ok:
        print(f"Account status: unavailable ({diagnosis.classification}: {diagnosis.message})")
        if team == "global":
            # Reassure the operator: global being blocked does not block the teams.
            for source in ("team_alpha", "team_beta"):
                team_diag = diagnose_source(source)
                state = "OK" if team_diag.auth_ok else f"{team_diag.classification}"
                print(f"  {source} auth: {state}")
            print("Note: only the selected source is shown above; team competition uses team credentials.")
        return

    account = diagnosis.account or {}
    print(f"Account equity: {account.get('equity')}")
    print(f"Cash: {account.get('cash')}")
    print(f"Buying power: {account.get('buying_power')}")


def _account_context_for_source(source: str, settings: Settings) -> "AccountContext":
    """Build a deterministic AccountContext from a specific credential source.

    Team sources never fall back to global keys. If the source's account is
    unavailable, fall back to a deterministic STARTING_EQUITY context (no global
    credentials are ever used for a team).
    """

    diagnosis = diagnose_source(source, base_settings=settings)
    if diagnosis.auth_ok and diagnosis.account:
        try:
            return AccountContext(
                equity=float(diagnosis.account["equity"]),
                cash=float(diagnosis.account["cash"]),
                buying_power=float(diagnosis.account["buying_power"]),
            )
        except (TypeError, ValueError, KeyError):
            pass
    print(
        f"({source} account unavailable: {diagnosis.classification}; "
        "using deterministic STARTING_EQUITY context.)"
    )
    return AccountContext(
        equity=settings.starting_equity,
        cash=settings.starting_equity,
        buying_power=settings.starting_equity * 2.0,
    )


VALID_PROPOSAL_SOURCES = ("default", "llm")


def _options_adapter_from_env() -> OptionsExecutionAdapter:
    """Build the paper options adapter from env.

    Single-leg long calls/puts execute by default. Multileg spreads stay OFF
    unless ENABLE_PAPER_OPTION_SPREADS=true (runtime MLEG paper support uncertain).
    """

    spreads = (os.getenv("ENABLE_PAPER_OPTION_SPREADS", "false") or "false").strip().lower() == "true"
    return OptionsExecutionAdapter(enabled=True, enable_spreads=spreads)


def _resolve_proposal_source_name(cli_value: str | None) -> str:
    name = (cli_value or os.getenv("WEEK_COMPETITION_PROPOSAL_SOURCE") or "default").strip().lower()
    if name not in VALID_PROPOSAL_SOURCES:
        print(f"Unknown --proposal-source '{name}'. Use one of: {', '.join(VALID_PROPOSAL_SOURCES)}.")
        raise SystemExit(1)
    return name


def _market_data_price_fn(settings: Settings):
    """Build a latest-price function from the first working credential source.

    Market data is account-agnostic, so a working team key is fine even if the
    global key is invalid. Returns None if no source can fetch prices.
    """

    for source in ("team_alpha", "team_beta", "global"):
        try:
            price_settings = settings_for_source(source, settings)
            price_fn = build_alpaca_price_fn(price_settings)
            price_fn("SPY")  # validate once
            return price_fn
        except Exception:  # noqa: BLE001 - try the next source; degrade to None
            continue
    return None


def _research_fetchers(team: str, settings: Settings, research_config: ResearchConfig):
    """Build allowlisted research fetchers (Alpaca news / OpenAI web) for a team.

    Alpaca news uses the team's own credentials; OpenAI web uses OPENAI_API_KEY.
    Returns (alpaca_news_fn, openai_web_fn), each None when unused/unavailable.
    """

    news_fn = None
    web_fn = None
    if research_config.uses_alpaca:
        try:
            team_settings = settings_for_source(team, settings)
            news_fn = build_alpaca_news_fn(team_settings.alpaca_api_key, team_settings.alpaca_secret_key)
        except Exception as exc:  # noqa: BLE001 - degrade to no news; never crash
            print(f"(Alpaca news unavailable for {team}: {exc})")
            news_fn = None
    if research_config.uses_openai_web:
        try:
            web_fn = build_openai_web_fn(os.getenv("OPENAI_API_KEY"))
        except Exception as exc:  # noqa: BLE001 - degrade to no web research
            print(f"(OpenAI web research unavailable: {exc})")
            web_fn = None
    return news_fn, web_fn


def _safe_read_client(team: str, settings: Settings):
    try:
        return client_for_source(team, base_settings=settings)
    except Exception:  # noqa: BLE001 - read-only context client is best-effort
        return None


def run_start_week_competition() -> None:
    settings = Settings.from_env()
    price_fn = _market_data_price_fn(settings)
    starting_spy_price, _ = latest_price("SPY", price_fn)
    state = start_week_competition(
        starting_equity=settings.starting_equity,
        starting_spy_price=starting_spy_price,
    )
    print("Started Alpha vs Beta weekly paper competition (paper-only).")
    print(f"Week start: {state.week_start}")
    print(f"Week end: {state.week_end}")
    print(f"Teams: {', '.join(state.teams)}")
    if starting_spy_price is not None:
        print(f"Starting SPY price: {starting_spy_price}")
    else:
        print("Starting SPY price: unknown (market data unavailable; SPY benchmark will be unknown).")


def run_week_cycle_cli(team: str, proposal_source: str | None = None, review_only: bool = False) -> None:
    settings = Settings.from_env()
    permissions = TradingPermissions.from_env()
    source_name = _resolve_proposal_source_name(proposal_source)
    if review_only:
        print("Review-only cycle: portfolio/strategy review + memory only; NO new broker orders.")
    # Account context uses the TEAM's own credentials only — never global.
    account = _account_context_for_source(team, settings)
    ks = read_kill_switch()
    if ks.engaged:
        print(ks.describe())

    # Read-only context client + market data (used for research context + SPY).
    context_client = _safe_read_client(team, settings)
    price_fn = _market_data_price_fn(settings)

    # SPY benchmark (Task 6): compute return vs the recorded starting SPY price.
    state = load_competition_state()
    spy_return_pct = None
    spy_provenance = DataProvenance.UNKNOWN
    if state.starting_spy_price and price_fn is not None:
        current_spy, _ = latest_price("SPY", price_fn)
        spy_return_pct = spy_return(state.starting_spy_price, current_spy)
        if spy_return_pct is None:
            print("(SPY current price unavailable; SPY return unknown.)")
        else:
            spy_provenance = DataProvenance.LIVE
    elif not state.starting_spy_price:
        print("(No starting SPY price recorded; SPY return unknown. Re-run start-week-competition with market data.)")

    # Resolve proposal source. For LLM, fail clearly BEFORE any broker execution.
    week_proposal_source = None
    if source_name == "llm":
        try:
            # Strategy/proposal generation is the high-value path -> strategy model.
            provider = build_routed_provider("strategy")
        except LLMProviderError as exc:
            print(f"LLM proposal source unavailable: {exc}")
            raise SystemExit(1) from exc
        status = routing_status()
        print(f"LLM provider: {status['provider']} | strategy model: {status['strategy_model']}")
        strategy_id = f"{team}_llm_week_competition_v1"
        research_config = ResearchConfig.from_env()
        alpaca_news_fn, openai_web_fn = _research_fetchers(team, settings, research_config)
        week_proposal_source = build_llm_proposal_source(
            team,
            provider=provider,
            strategy_id=strategy_id,
            client=context_client,
            price_fn=price_fn,
            research_config=research_config,
            alpaca_news_fn=alpaca_news_fn,
            openai_web_fn=openai_web_fn,
        )

    # Execution client (gated): only built for genuine non-dry-run paper submission.
    # Review-only never submits, so the broker client is never built.
    client = None
    if not settings.dry_run and not ks.engaged and not review_only:
        try:
            client = client_for_source(
                team,
                base_settings=settings,
                options_adapter=_options_adapter_from_env(),
            )
        except Exception as exc:  # noqa: BLE001 - missing/invalid team creds run without live submission
            print(f"(Team broker unavailable: {exc}; running without live submission.)")
            client = None

    # Portfolio Manager context: current positions (read-only; degrades to empty).
    positions = None
    try:
        from src.research.data_tools import alpaca_positions

        positions = alpaca_positions(context_client).value or []
    except Exception:  # noqa: BLE001 - positions are best-effort context only
        positions = None

    result = run_week_cycle(
        team,
        permissions=permissions,
        account=account,
        proposal_source=week_proposal_source,
        client=client,
        dry_run=settings.dry_run,
        spy_return_pct=spy_return_pct,
        spy_provenance=spy_provenance,
        portfolio_config=PortfolioManagerConfig.from_env(),
        positions=positions,
        review_only=review_only,
    )
    print(
        f"Ran week cycle for {team} (dry_run={settings.dry_run}, proposal_source={source_name}, "
        f"review_only={review_only})."
    )
    for line in result.stage_log:
        print(f"  {line}")
    bundle = result.bundle
    if bundle is not None and bundle.market_summary:
        print(f"LLM market summary: {bundle.market_summary}")
    if bundle is not None and bundle.raw_errors:
        print(f"LLM proposals rejected during parsing: {len(bundle.raw_errors)}")
        for err in bundle.raw_errors:
            print(f"  - rejected: {err}")
    decision = result.portfolio_decision
    if decision is not None:
        print(f"Portfolio manager: {decision.decision_type} (mode={decision.mode})")
        print(f"  rationale: {decision.rationale}")
        print(f"  SPY-relative: {decision.relation_to_spy_performance}")
        print(f"  attribution: {decision.relation_to_recent_attribution}")
        print(f"  buying power: {decision.buying_power_impact}")
        print(
            f"  allowed new orders: {decision.allowed_to_generate_new_orders} "
            f"(max {decision.max_new_proposals_this_cycle})"
        )
        if result.no_trade:
            print(f"No trade decision: {decision.rejected_new_ideas_reason or decision.rationale}")

    # Compact team debate (advisory; only when critique/review agents are enabled).
    review_flags = LLMReviewFlags.from_env()
    if review_flags.critique_agent or review_flags.review_agent:
        try:
            attribution = load_daily_spy_attribution(team)
            feedback = performance_feedback(team)
        except Exception:  # noqa: BLE001 - debate is best-effort advisory context
            attribution, feedback = None, {}
        debate = build_team_debate(
            team_id=team, attribution=attribution, feedback=feedback,
            review=load_latest_daily_team_review(team),
        )
        print(f"=== Team debate ({team}; advisory only; model={debate['model_used']}, source={debate['source']}) ===")
        print(f"  Bull: {debate.get('bull_case', '')}")
        print(f"  Bear: {debate.get('bear_case', '')}")
        print(f"  Disproof: {debate.get('what_would_prove_us_wrong', '')}")
        print(f"  Better than weakest holding? {debate.get('better_than_weakest_holding', '')}")
        print(f"  Trade/hold/observe: {debate.get('trade_hold_or_observe', '')}")
        print(f"  Cost/risk: {debate.get('cost_risk_note', '')}")

    print(f"Routing: {result.routing.summary()}")
    if spy_return_pct is not None:
        print(f"SPY return this cycle: {spy_return_pct:.4f}")
    print(f"Orders submitted: {sum(1 for r in result.execution_records if r.submitted)}")
    broker_rejections = [r for r in result.execution_records if r.broker_rejected]
    if broker_rejections:
        print(f"Broker rejections: {len(broker_rejections)}")
        for record in broker_rejections:
            print(f"  ! {record.symbol}: {record.failure_category} — {record.broker_reject_reason}")
    for record in result.execution_records:
        print(f"  - {record.symbol} [{record.proposal_type}]: {record.detail}")


def run_research_status() -> None:
    config = ResearchConfig.from_env()
    status = config.status()
    print("=== Research status (allowlisted; no scraping; secrets never printed) ===")
    print(f"Provider: {status['provider']} | available: {status['available']}")
    print(f"Alpaca news: {status['uses_alpaca']} | OpenAI web: {status['uses_openai_web']} ({status['openai_web_model']})")
    print(f"Caps: {status['max_queries_per_team']} queries/team, {status['max_results_per_query']} results/query, "
          f"lookback {status['lookback_hours']}h")
    print(f"Watchlist: {', '.join(config.watchlist)}")
    print(f"Research log entries: {research_log_count()}")
    for team in WEEK_TEAMS:
        latest = read_latest_research(team)
        if not latest:
            print(f"  {team}: no research logged yet")
            continue
        results = latest.get("results", [])
        print(f"  {team}: {len(results)} result(s) via {latest.get('provider')} (available={latest.get('available')})")
        for item in results[:3]:
            print(f"    - [{item.get('source_id')}] {item.get('title')} ({item.get('provider')})")
        if latest.get("errors"):
            print(f"    errors: {latest['errors']}")


def run_proposal_attribution(team: str) -> None:
    entries = load_team_attribution(team)
    print(f"=== Proposal attribution: {team} ===")
    if not entries:
        print("No attribution records yet. Run a cycle first.")
        return
    feedback = performance_feedback(team)
    print(f"Total proposals tracked: {len(entries)}")
    print(f"Best symbol: {feedback['best_symbol']} | Worst symbol: {feedback['worst_symbol']}")
    print(f"Best strategy: {feedback['best_strategy']} | Worst strategy: {feedback['worst_strategy']}")
    print(f"Pending outcomes: {feedback['pending_count']}")
    print("Recent winners:", feedback["recent_winners"] or "(none)")
    print("Recent losers:", feedback["recent_losers"] or "(none)")
    refreshed_total = sum(1 for e in entries if e.refreshed_at is not None)
    print(f"Refreshed outcomes: {refreshed_total} (run refresh-proposal-attribution to update)")
    broker_rejected = [e for e in entries if e.broker_rejected]
    if broker_rejected:
        print(f"Broker rejections: {len(broker_rejected)}")
        for entry in broker_rejected[-5:]:
            print(
                f"  ! {entry.symbol} [{entry.asset_type}] "
                f"category={entry.failure_category} reason={entry.broker_reject_reason}"
            )
    print("Recent entries:")
    for entry in entries[-10:]:
        ret = "pending" if entry.return_pct is None else f"{entry.return_pct:.4f}"
        excess = "n/a" if entry.excess_return_pct is None else f"{entry.excess_return_pct:.4f}"
        current = "n/a" if entry.current_price is None else f"{entry.current_price:.2f}"
        refreshed = "never" if entry.refreshed_at is None else entry.refreshed_at
        line = (
            f"  - {entry.symbol} [{entry.asset_type}] routing={entry.routing} "
            f"submitted={entry.broker_submitted} outcome={entry.outcome_status} "
            f"return={ret} excessVsSPY={excess} current={current} refreshed={refreshed} "
            f"sources={entry.research_source_ids}"
        )
        if entry.broker_rejected:
            line += f" broker_rejected={entry.failure_category}:{entry.broker_reject_reason}"
        if entry.refresh_skip_reason:
            line += f" skip={entry.refresh_skip_reason}"
        print(line)


def _refresh_spy_prices(settings: Settings, price_fn) -> tuple[float | None, float | None]:
    """Resolve (spy_start_price, spy_current_price) reusing the recorded benchmark.

    The competition's recorded starting SPY price is the holding-period baseline;
    the current SPY price comes from the same safe market-data wrapper. Either may
    be None (degrades to a pending SPY-relative outcome, never invented).
    """

    state = load_competition_state()
    spy_start_price = state.starting_spy_price
    spy_current_price, _ = latest_price("SPY", price_fn)
    return spy_start_price, spy_current_price


def run_refresh_proposal_attribution(team: str | None = None, threshold: float | None = None) -> None:
    settings = Settings.from_env()
    teams = [team] if team else list(WEEK_TEAMS)
    outcome_threshold = threshold if threshold is not None else default_outcome_threshold()

    price_fn = _market_data_price_fn(settings)
    if price_fn is None:
        print("=== Refresh proposal attribution (paper-only) ===")
        print(
            "Market data unavailable: no working Alpaca credential source could fetch prices. "
            "Outcomes stay pending; no records were changed. (Team credentials are sufficient; "
            "global credentials are not required.)"
        )
        raise SystemExit(1)

    spy_start_price, spy_current_price = _refresh_spy_prices(settings, price_fn)

    print("=== Refresh proposal attribution (paper-only; research feedback only) ===")
    print(f"Outcome threshold (|excess vs SPY|): {outcome_threshold:.4f}")
    if spy_start_price is None:
        print("(No starting SPY price recorded; SPY-relative outcomes stay pending. Run start-week-competition.)")
    elif spy_current_price is None:
        print("(SPY current price unavailable; SPY-relative outcomes stay pending.)")

    for tid in teams:
        summary = refresh_team_attribution(
            tid,
            price_fn=price_fn,
            spy_start_price=spy_start_price,
            spy_current_price=spy_current_price,
            threshold=outcome_threshold,
        )
        print("")
        print(f"--- {tid} ---")
        print(f"Records scanned: {summary.scanned}")
        print(f"Records refreshed (scored): {summary.refreshed}")
        print(f"Records still pending: {summary.pending}")
        print(f"Worked: {summary.worked} | Failed: {summary.failed} | Mixed: {summary.mixed}")
        spy_text = "unknown" if summary.spy_return_pct is None else f"{summary.spy_return_pct:.4f}"
        print(f"SPY return over period: {spy_text}")
        if summary.best is not None:
            print(
                f"Best proposal by excess: {summary.best.symbol} [{summary.best.asset_type}] "
                f"excessVsSPY={summary.best.excess_return_pct:.4f}"
            )
        else:
            print("Best proposal by excess: (none scored yet)")
        if summary.worst is not None:
            print(
                f"Worst proposal by excess: {summary.worst.symbol} [{summary.worst.asset_type}] "
                f"excessVsSPY={summary.worst.excess_return_pct:.4f}"
            )
        else:
            print("Worst proposal by excess: (none scored yet)")
        if summary.skipped:
            print(f"Skipped symbols ({len(summary.skipped)}):")
            for skip in summary.skipped[:10]:
                print(f"  - {skip.symbol} [{skip.proposal_id}]: {skip.reason}")
        else:
            print("Skipped symbols: (none)")


def _low_buying_power_from_scorecard(scorecard, pm_threshold: float) -> bool:
    if scorecard is None or scorecard.buying_power is None:
        return False
    equity = scorecard.starting_equity or 0.0
    if equity <= 0:
        return False
    return (scorecard.buying_power / equity) < pm_threshold


def _evaluate_team_cheap_gate(team: str):
    """Resolve cheap/local signals and return (GateDecision, CheapCycleGateConfig).

    No LLM, no broker, no network — reads local ledger/scorecard/attribution only.
    """

    gate_config = CheapCycleGateConfig.from_env()
    pm_config = PortfolioManagerConfig.from_env()
    ledger = TeamLearningLedger.load(team)
    scorecard = load_latest_scorecard(team)
    try:
        feedback = performance_feedback(team)
    except Exception:  # noqa: BLE001 - missing/old attribution must not crash the gate
        feedback = {}
    outcome = feedback.get("outcome_feedback", {}) if isinstance(feedback, dict) else {}

    if scorecard is not None and scorecard.broker_rejected_count:
        broker_rejections = scorecard.broker_rejected_count
    else:
        broker_rejections = len(outcome.get("recent_broker_rejections", []) or [])

    low_bp = _low_buying_power_from_scorecard(scorecard, pm_config.low_buying_power_review_threshold_pct)

    # SPY recent-move and research-change signals require a per-cycle snapshot we do
    # not persist; the cheap gate stays local and does not fetch live SPY here.
    decision = evaluate_cheap_cycle_gate(
        team,
        config=gate_config,
        last_full_cycle_at=ledger.last_full_cycle_at or None,
        spy_move_pct=None,
        low_buying_power=low_bp,
        broker_rejections=broker_rejections,
        research_changed=False,
        urgent_review=low_bp,
        mode=ledger.mode,
    )
    return decision, gate_config


def run_cheap_cycle_gate(team: str) -> None:
    """Decide cheaply (no LLM, local data only) whether a full cycle is worth it."""

    decision, gate_config = _evaluate_team_cheap_gate(team)

    print(f"=== Cheap cycle gate: {team} (paper-only; no LLM, local data only) ===")
    print(f"Gate enabled: {gate_config.enabled} | min interval: {gate_config.interval_for(team)}m")
    print(f"should_run_full_cycle: {decision.should_run_full_cycle}")
    print(f"reason: {decision.reason}")
    print(f"recommended_wait_minutes: {decision.recommended_wait_minutes}")
    print(f"recommend_review_only: {decision.recommend_review_only}")
    print(f"trigger_flags: {decision.trigger_flags}")
    if decision.should_run_full_cycle:
        print(f"Next: python -m src.main run-week-cycle --team {team} --proposal-source llm")
    elif decision.recommend_review_only:
        print(f"Next: python -m src.main run-week-cycle --team {team} --proposal-source llm --review-only")
    else:
        print("Next: stay cheap (refresh-proposal-attribution / week-competition-status only).")


def run_daily_spy_attribution(team: str | None = None) -> None:
    teams = [team] if team else list(WEEK_TEAMS)
    for tid in teams:
        attribution = load_daily_spy_attribution(tid)
        print(format_daily_spy_attribution(attribution))
        print("")


def run_export_daily_team_review(team: str | None = None) -> None:
    teams = [team] if team else list(WEEK_TEAMS)
    for tid in teams:
        review = export_daily_team_review(tid)
        print(format_daily_team_review(review))
        print(f"(Saved under data/reviews/{tid}_latest.json)")
        print("")


def run_llm_routing_status() -> None:
    """Print task-specific model routing. Model NAMES only — never key contents."""

    status = routing_status()
    print("=== LLM model routing (Phase 7O; model names only, no secrets) ===")
    print(f"Provider: {status['provider']}")
    print(f"Default model: {status['default_model']}")
    print(f"Strategy model: {status['strategy_model']}")
    print(f"Portfolio manager model: {status['portfolio_manager_model']}")
    print(f"Review model: {status['review_model']}")
    print(f"Critique model: {status['critique_model']}")
    print(f"Summary model: {status['summary_model']}")
    print(f"Research synthesis model: {status['research_synthesis_model']}")
    print(f"API key configured: {status['api_key_configured']}")


def run_llm_review_status() -> None:
    """Print which advisory LLM stages are enabled + model per stage. No secrets."""

    status = review_status()
    print("=== LLM advisory review agents (Phase 7P; model names only, no secrets) ===")
    print(f"Provider: {status['provider']}")
    print(f"API key configured: {status['api_key_configured']}")
    print("Advisory stages (advisory only; deterministic risk remains authoritative):")
    for name, info in status["stages"].items():
        print(f"  {name}: enabled={info['enabled']} | model={info['model']}")


def run_llm_daily_review(team: str | None = None) -> None:
    """Daily review with optional LLM narrative + multi-day memory. Never submits orders.

    Loads deterministic daily-spy-attribution, builds/persists the deterministic
    daily review, optionally writes an LLM narrative, and rolls the result into the
    ignored multi-day strategy memory under data/team_memory/.
    """

    teams = [team] if team else list(WEEK_TEAMS)
    flags = LLMReviewFlags.from_env()
    for tid in teams:
        print(f"=== LLM daily review: {tid} (paper-only; advisory; submits NO orders) ===")
        attribution = load_daily_spy_attribution(tid)
        print(format_daily_spy_attribution(attribution))

        # Deterministic daily review artifact (data/reviews/), then optional narrative.
        review = export_daily_team_review(tid)
        narrative = generate_daily_review_narrative(
            team_id=tid, attribution=attribution, review=review, enabled=flags.daily_review
        )
        print(f"Daily review model: {narrative['model_used']} (source={narrative['source']})")
        print(f"Narrative: {narrative.get('narrative', '')}")
        if narrative.get("what_to_do_tomorrow"):
            print(f"Tomorrow: {', '.join(narrative['what_to_do_tomorrow'])}")

        # Roll into compact multi-day strategy memory (LLM-compressed when enabled).
        memory = update_strategy_memory(tid, today_review=review, summary_enabled=flags.summary_agent)
        print(format_strategy_memory(memory))
        print(f"(Saved multi-day memory under data/team_memory/{tid}_strategy_memory.json)")
        print("")


def _cheap_loop_market_open() -> bool | None:
    """Best-effort read-only market-open check. None when undeterminable."""

    settings = Settings.from_env()
    for source in ("team_alpha", "team_beta"):
        client = _safe_read_client(source, settings)
        if client is None:
            continue
        try:
            return bool(client.is_market_open())
        except Exception:  # noqa: BLE001 - degrade to undeterminable; never crash the loop
            continue
    return None


def run_cheap_competition_loop(
    *,
    once: bool = False,
    sleep_seconds: int = 900,
    team: str = "both",
    market_hours_only: bool = True,
    run_review_only_when_skipped: bool = False,
    llm_review_when_skipped: bool = False,
    llm_daily_review_at_close: bool = False,
    dry_run_loop: bool = False,
    sleep_fn=None,
) -> None:
    """Cost-saving all-day runner: refresh + status + gate, full cycle only when the gate says so.

    Never bypasses the kill switch and never submits unless ``run-week-cycle`` is
    actually invoked (which keeps its own deterministic risk + kill-switch gates).
    With ``--dry-run-loop`` it prints intended actions without running full cycles.
    """

    import os
    import time as _time

    sleep_fn = sleep_fn or _time.sleep
    teams = list(WEEK_TEAMS) if team == "both" else [team]
    iteration = 0
    review_only_during_market_hours = os.getenv(
        "REVIEW_ONLY_DURING_MARKET_HOURS", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    discord_update_config = _discord_iteration_update_config()

    while True:
        iteration += 1
        print(f"=== Cheap competition loop iteration {iteration} (paper-only; dry_run_loop={dry_run_loop}) ===")
        ks = read_kill_switch()
        if ks.engaged:
            print(ks.describe())

        market_open = _cheap_loop_market_open() if market_hours_only else None
        market_closed = market_hours_only and market_open is False

        if market_closed:
            print("Market is closed; staying cheap (refresh/status/export only, no full cycles this iteration).")
            if review_only_during_market_hours and (run_review_only_when_skipped or llm_review_when_skipped or llm_daily_review_at_close):
                print("Market closed; LLM/review-only work skipped because REVIEW_ONLY_DURING_MARKET_HOURS=true.")

        # Cheap, read-only steps every iteration.
        if dry_run_loop:
            print("[dry-run] would run: refresh-proposal-attribution")
            print("[dry-run] would run: week-competition-status")
        else:
            try:
                run_refresh_proposal_attribution()
            except SystemExit as exc:  # refresh exits non-zero when market data is unavailable
                print(f"(refresh-proposal-attribution unavailable: exit {exc.code}; continuing loop)")
            except Exception as exc:  # noqa: BLE001 - never let a cheap step kill the loop
                print(f"(refresh-proposal-attribution error: {exc}; continuing loop)")
            run_week_competition_status()

        market_state = "open" if market_open is True else ("closed" if market_open is False else "unknown")

        for tid in teams:
            decision, _gate_config = _evaluate_team_cheap_gate(tid)
            print(
                f"[{tid}] gate: should_run_full_cycle={decision.should_run_full_cycle} "
                f"recommend_review_only={decision.recommend_review_only} reason={decision.reason}"
            )
            allow_full = decision.should_run_full_cycle and not market_closed
            allow_review_when_skipped = (
                (run_review_only_when_skipped or llm_review_when_skipped)
                and not (market_closed and review_only_during_market_hours)
            )

            if allow_full:
                cycle_action = "full_cycle"
                if dry_run_loop:
                    print(f"[dry-run] [{tid}] would run: run-week-cycle --proposal-source llm")
                else:
                    run_week_cycle_cli(team=tid, proposal_source="llm")
            elif allow_review_when_skipped:
                # Gate skipped a full cycle: do advisory review-only + cheap LLM
                # critique/summary stages. NEVER runs the expensive strategy model
                # and NEVER submits orders.
                cycle_action = "review_only"
                if dry_run_loop:
                    print(f"[dry-run] [{tid}] would run: run-week-cycle --proposal-source llm --review-only")
                    if llm_review_when_skipped:
                        print(f"[dry-run] [{tid}] would run: run-llm-daily-review (advisory; no orders)")
                else:
                    run_week_cycle_cli(team=tid, proposal_source="llm", review_only=True)
                    if llm_review_when_skipped:
                        try:
                            run_llm_daily_review(team=tid)
                        except Exception as exc:  # noqa: BLE001 - advisory step must not kill the loop
                            print(f"({tid} llm-daily-review unavailable: {exc}; continuing loop)")
            else:
                cycle_action = "market_closed" if market_closed else "cheap_skip"
                if market_closed and review_only_during_market_hours and (run_review_only_when_skipped or llm_review_when_skipped):
                    print(f"[{tid}] market closed; review-only/LLM review skipped.")
                else:
                    print(f"[{tid}] staying cheap this iteration (no full cycle).")

            # Phase 7S: post a concise team-thought brief to that team's Discord channel.
            _post_discord_iteration_update(
                config=discord_update_config,
                team_id=tid,
                iteration=iteration,
                cycle_action=cycle_action,
                gate_decision=decision,
                market_state=market_state,
                kill_switch_engaged=ks.engaged,
                llm_review_when_skipped=llm_review_when_skipped,
                dry_run=dry_run_loop,
            )

        allow_daily_review_at_close = (
            llm_daily_review_at_close
            and market_hours_only
            and market_open is False
            and not review_only_during_market_hours
        )

        if allow_daily_review_at_close:
            if dry_run_loop:
                print("[dry-run] would run: run-llm-daily-review (market closed; advisory, no orders)")
            else:
                try:
                    run_llm_daily_review(team=None if team == "both" else team)
                except Exception as exc:  # noqa: BLE001 - advisory close step must not kill the loop
                    print(f"(llm-daily-review-at-close unavailable: {exc}; continuing loop)")
        elif llm_daily_review_at_close and market_closed and review_only_during_market_hours:
            print("Market closed; llm-daily-review-at-close skipped because REVIEW_ONLY_DURING_MARKET_HOURS=true.")

        if dry_run_loop:
            print("[dry-run] would run: export-team-scorecards")
        else:
            run_export_team_scorecards()

        # Phase 7S.2: post the Alpha-vs-Beta scoreboard exactly once per iteration,
        # after both teams are processed, and only when both teams ran this loop (a
        # single-team loop has no head-to-head, so it never posts a scoreboard).
        if len(teams) >= 2:
            _post_discord_competition_summary(
                config=discord_update_config,
                iteration=iteration,
                kill_switch_engaged=ks.engaged,
                next_wake_seconds=None if once else sleep_seconds,
                teams=tuple(teams),
                dry_run=dry_run_loop,
            )
        elif discord_update_config is not None and getattr(discord_update_config, "enabled", False):
            print(
                f"[summary] skipped: single-team loop ({teams[0]}); "
                "Alpha-vs-Beta scoreboard needs both teams."
            )

        if once:
            break
        print(f"Sleeping {sleep_seconds}s before next cheap iteration...")
        sleep_fn(sleep_seconds)


def _discord_iteration_update_config():
    """Load the Phase 7S Discord iteration-update config. Never raises."""

    try:
        from src.discord_bot.competition_updates import DiscordIterationUpdateConfig

        return DiscordIterationUpdateConfig.from_env()
    except Exception as exc:  # noqa: BLE001 - Discord config must never crash the loop
        print(f"(Discord iteration updates unavailable: {exc}; continuing loop)")
        return None


def _iteration_llm_model(cycle_action: str, llm_review_when_skipped: bool) -> str | None:
    """Best-effort model NAME (never secrets) for the stage that ran this iteration."""

    try:
        status = routing_status()
    except Exception:  # noqa: BLE001 - routing status is advisory only
        return None
    if cycle_action == "full_cycle":
        return status.get("strategy_model")
    if cycle_action == "review_only" and llm_review_when_skipped:
        return status.get("review_model")
    return None


def _post_discord_iteration_update(
    *,
    config,
    team_id: str,
    iteration: int,
    cycle_action: str,
    gate_decision,
    market_state: str,
    kill_switch_engaged: bool,
    llm_review_when_skipped: bool,
    dry_run: bool,
) -> None:
    """Post a team's Discord iteration brief. Never crashes the loop."""

    if config is None or not getattr(config, "enabled", False):
        return
    try:
        from src.discord_bot.competition_updates import post_team_iteration_update

        post_team_iteration_update(
            team_id,
            iteration=iteration,
            cycle_action=cycle_action,
            gate_decision=gate_decision,
            market_state=market_state,
            kill_switch_engaged=kill_switch_engaged,
            llm_model_used=_iteration_llm_model(cycle_action, llm_review_when_skipped),
            config=config,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - Discord posting must never crash the loop
        print(f"(Discord iteration update failed for {team_id}: {exc}; continuing loop)")


def _post_discord_competition_summary(
    *,
    config,
    iteration: int | None = None,
    kill_switch_engaged: bool,
    next_wake_seconds: int | None,
    teams: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Post the optional Alpha-vs-Beta scoreboard summary. Never crashes the loop.

    Posts at most once per loop ``iteration`` (the summary's own de-dup guard
    skips a repeat in the same iteration), so a scoreboard is never spammed.
    """

    if config is None or not getattr(config, "enabled", False):
        return
    try:
        from src.discord_bot.competition_updates import post_competition_iteration_summary

        post_competition_iteration_summary(
            config=config,
            iteration=iteration,
            kill_switch_engaged=kill_switch_engaged,
            next_wake_seconds=next_wake_seconds,
            teams=teams,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - Discord posting must never crash the loop
        print(f"(Discord competition summary failed: {exc}; continuing loop)")


def run_discord_iteration_update(
    team: str = "both",
    *,
    summary: bool = False,
    dry_run: bool = False,
) -> None:
    """Build and (unless --dry-run) send Phase 7S Discord iteration briefs.

    Reads local artifacts only. With ``--dry-run`` it prints the message(s) that
    would be sent and never calls the Discord API. Secrets are never printed.
    """

    from src.discord_bot.competition_updates import (
        post_competition_iteration_summary,
        post_team_iteration_update,
    )

    config = _discord_iteration_update_config()
    if config is None:
        raise SystemExit(1)

    teams = list(WEEK_TEAMS) if team == "both" else [team]
    ks = read_kill_switch()

    print("=== Discord iteration update (Phase 7S; paper-only; no secrets) ===")
    print(f"Enabled: {config.enabled} | token configured: {config.token is not None} | dry_run: {dry_run}")
    if not config.enabled and not dry_run:
        print(
            f"{'ENABLE_DISCORD_ITERATION_UPDATES'} is false; nothing will be sent. "
            "Use --dry-run to preview, or enable it in .env."
        )

    for tid in teams:
        decision, _gate_config = _evaluate_team_cheap_gate(tid)
        cycle_action = "full_cycle" if decision.should_run_full_cycle else (
            "review_only" if decision.recommend_review_only else "cheap_skip"
        )
        result = post_team_iteration_update(
            tid,
            cycle_action=cycle_action,
            gate_decision=decision,
            market_state="unknown",
            kill_switch_engaged=ks.engaged,
            config=config,
            dry_run=dry_run,
        )
        print(f"[{tid}] result: sent={result['sent']} reason={result['reason']}")

    if summary:
        result = post_competition_iteration_summary(
            config=config,
            kill_switch_engaged=ks.engaged,
            teams=tuple(teams),
            dry_run=dry_run,
        )
        print(f"[summary] result: sent={result['sent']} reason={result['reason']}")

    print("Paper-only. LLMs do not execute trades. Orders require deterministic gates.")


def run_week_competition_status() -> None:
    status = competition_status()
    print("=== Alpha vs Beta weekly competition status (paper-only) ===")
    print(f"Active: {status['active']}")
    print(f"Week start: {status['week_start']}")
    print(f"Week end: {status['week_end']}")
    teams = status["teams"]
    if not teams:
        print("No team scorecards yet. Run: python -m src.main run-week-cycle --team team_alpha")
        return
    for card in teams:
        spy = card.get("spy_benchmark_return")
        excess = card.get("excess_return_vs_spy")
        spy_text = "unknown" if spy is None else f"{spy:.4f}"
        excess_text = "unknown" if excess is None else f"{excess:.4f}"
        starting = card.get("starting_equity") or 0.0
        team_return = (card["current_equity"] - starting) / starting if starting else 0.0
        print(
            f"#{card.get('current_rank')} {card['team_id']}: return={team_return:.4f} "
            f"equity={card['current_equity']:.2f} SPY={spy_text} excessVsSPY={excess_text} "
            f"orders={card['orders_submitted']} approved={card['approved_count']} "
            f"sim_only={card['simulation_only_count']} rejected={card['rejected_count']}"
        )
        pm_type = card.get("portfolio_decision_type")
        if pm_type:
            no_trade = card.get("portfolio_no_trade")
            print(
                f"    portfolio manager: {pm_type} "
                f"(no_trade={no_trade}, max_new={card.get('max_new_proposals')}, "
                f"broker_rejected={card.get('broker_rejected_count', 0)})"
            )
        # Brief attribution outcome summary (refreshed via refresh-proposal-attribution).
        ofb = performance_feedback(card["team_id"]).get("outcome_feedback", {})
        if ofb.get("refreshed_count"):
            avg_excess = ofb.get("avg_excess_return_vs_spy")
            avg_text = "n/a" if avg_excess is None else f"{avg_excess:.4f}"
            print(
                f"    attribution outcomes: worked={ofb.get('worked_count', 0)} "
                f"failed={ofb.get('failed_count', 0)} mixed={ofb.get('mixed_count', 0)} "
                f"pending={ofb.get('pending_count', 0)} avgExcessVsSPY={avg_text}"
            )
        else:
            print("    attribution outcomes: none refreshed yet (run refresh-proposal-attribution)")


def run_stop_week_competition() -> None:
    state = stop_week_competition()
    print(f"Stopped weekly competition. Stopped at: {state.stopped_at}")


def run_team_learning_status(team: str) -> None:
    ledger = TeamLearningLedger.load(team)
    print(f"=== Team learning ledger: {team} ===")
    print(f"Current hypothesis: {ledger.current_hypothesis or '(none)'}")
    print(f"Active strategy: {ledger.active_strategy or '(none)'}")
    print(f"Mode: {ledger.mode or '(none)'}")
    print(f"Watchlist: {', '.join(ledger.watchlist) or '(none)'}")
    if ledger.avoid_next_cycle:
        print(f"Avoid next cycle ({len(ledger.avoid_next_cycle)}):")
        for item in ledger.avoid_next_cycle[-10:]:
            print(f"  - {item}")
    print(f"Lessons learned ({len(ledger.lessons_learned)}):")
    for lesson in ledger.latest_lessons(10):
        print(f"  - {lesson}")
    print(f"Strategy changes ({len(ledger.strategy_changes)}):")
    for change in ledger.strategy_changes[-10:]:
        print(f"  - {change}")
    print(f"Risk notes ({len(ledger.risk_notes)}):")
    for note in ledger.risk_notes[-10:]:
        print(f"  - {note}")
    print(f"Cycles recorded: {len(ledger.reviews)}")


def run_export_team_scorecards(
    report_path: Path | str = Path("data/reports/team_scorecards.md"),
) -> None:
    cards = []
    for team in WEEK_TEAMS:
        card = load_latest_scorecard(team)
        if card is not None:
            cards.append(card)
    if not cards:
        print("No team scorecards to export yet.")
        return
    path = export_scorecards_markdown(cards, report_path)
    print(f"Exported team scorecards to {path}")


def run_kill_switch_on(reason: str | None = None) -> None:
    state = kill_switch_engage(reason=reason)
    print("Kill switch ENGAGED. All new broker submissions are blocked.")
    print(state.describe())


def run_kill_switch_off() -> None:
    kill_switch_disengage()
    print("Kill switch disengaged. Broker submissions follow normal gates.")


def run_kill_switch_status() -> None:
    print(read_kill_switch().describe())


def run_paper_permissions() -> None:
    permissions = TradingPermissions.from_env()
    import json as _json

    print(_json.dumps(permissions.summary(), indent=2))


def run_alpaca_auth_diagnose() -> None:
    from dotenv import find_dotenv

    dotenv_path = find_dotenv(usecwd=True)
    print("=== Alpaca auth diagnostics (paper-only; secrets never printed) ===")
    print(f".env loaded: {'yes' if dotenv_path else 'no'}" + (f" ({dotenv_path})" if dotenv_path else ""))
    print("")

    diagnoses = diagnose_all()
    for source in CREDENTIAL_SOURCES:
        d = diagnoses[source]
        print(f"[{source}] credential pair: {_SOURCE_LABELS[source]}")
        print(f"  api key present: {d.api_key_present} (length {d.api_key_length})")
        print(f"  secret present: {d.secret_present} (length {d.secret_length})")
        print(f"  ALPACA_PAPER valid (true): {d.paper_valid}")
        print(f"  base URL valid (paper endpoint): {d.base_url_valid}")
        print(f"  auth status: {'OK' if d.auth_ok else 'FAILED'}")
        print(f"  classification: {d.classification}")
        print(f"  detail: {d.message}")
        print("")


def _readiness_can_submit(source_diag, ks_engaged: bool, is_paper: bool) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if not source_diag.auth_ok:
        blockers.append(f"{source_diag.source} Alpaca auth failed ({source_diag.classification})")
    if ks_engaged:
        blockers.append("kill switch engaged")
    if not is_paper:
        blockers.append("TRADING_MODE is not paper")
    return (len(blockers) == 0, blockers)


def run_competition_readiness_check() -> None:
    settings = Settings.from_env()
    permissions = TradingPermissions.from_env()
    ks = read_kill_switch()
    summary = permissions.summary()
    provider_config = LLMProviderConfig.from_env()
    provider_key_set = {
        "openai": bool(provider_config.openai_api_key),
        "anthropic": bool(provider_config.anthropic_api_key),
        "ollama": True,  # local, no hosted key required
    }.get(provider_config.provider, False)

    diagnoses = diagnose_all(base_settings=settings)
    options_adapter = _options_adapter_from_env()

    print("=== Competition readiness check (paper-only) ===")
    print(f"Kill switch: {'ENGAGED' if ks.engaged else 'disengaged'}")
    print(f"TRADING_MODE: {summary['trading_mode']} (is_paper={summary['is_paper']})")
    print(f"DRY_RUN: {settings.dry_run}")
    paper_endpoint_valid = settings.alpaca_base_url in ("", "https://paper-api.alpaca.markets")
    print(f"Paper endpoint valid: {settings.alpaca_base_url or '(unset)'}")
    print("")
    print("Paper permissions:")
    print(f"  L1 stocks: {summary['paper_stocks']}")
    print(f"  L2 shorting: {summary['paper_shorting']}")
    print(f"  L3 margin: {summary['paper_margin']}")
    print(f"  L4 options: {summary['paper_options']}")
    print(f"  allow naked options: {summary['allow_naked_options']}")
    print(f"Advanced caps: {summary['caps']}")
    print(f"Options adapter configured: {'yes' if options_adapter.configured else 'no'}")
    print(f"Single-leg long options enabled: {'yes' if options_adapter.single_leg_enabled else 'no'}")
    print(f"Spreads enabled: {'yes' if options_adapter.spreads_enabled else 'no (single-leg only; spreads refuse with a logged reason)'}")
    print(f"LLM provider selected: {provider_config.provider}")
    print(f"LLM provider key configured: {provider_key_set}")
    proposal_source = (os.getenv("WEEK_COMPETITION_PROPOSAL_SOURCE") or "default").strip().lower()
    print(f"Week competition proposal source (default): {proposal_source}")
    research_status = ResearchConfig.from_env().status()
    print(
        f"Research: {research_status['message']} "
        f"(provider={research_status['provider']}, available={research_status['available']}, "
        f"alpaca={research_status['uses_alpaca']}, openai_web={research_status['uses_openai_web']})"
    )
    print("")

    for source in CREDENTIAL_SOURCES:
        d = diagnoses[source]
        print(f"{source} Alpaca auth: {'OK' if d.auth_ok else d.classification}")
    print("")

    is_paper = bool(summary["is_paper"])
    for team in ("team_alpha", "team_beta"):
        can_submit, blockers = _readiness_can_submit(diagnoses[team], ks.engaged, is_paper)
        label = "Team Alpha" if team == "team_alpha" else "Team Beta"
        print(f"{label} can submit paper orders: {can_submit}")
        if blockers:
            print(f"  blockers: {', '.join(blockers)}")
        elif settings.dry_run:
            print("  note: DRY_RUN is true, so cycles route/log orders but do not submit. Set DRY_RUN=false to submit.")

    if not diagnoses["global"].auth_ok:
        print("")
        print(
            "Note: global Alpaca credentials are not authenticated, but this does NOT block "
            "Team Alpha or Team Beta. Each team uses its own credentials."
        )


def _read_value(obj: object, name: str) -> object:
    if isinstance(obj, dict):
        return obj.get(name, "unknown")
    return getattr(obj, name, "unknown")


def run_report(run_id: str | None = None) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    result = generate_daily_report(settings.database_path, run_id=run_id)
    if not result.ok or result.report is None:
        print(f"Report unavailable: {result.message}")
        raise SystemExit(1)

    print(format_report(result.report))


def run_compare_strategies(
    strategy_names: tuple[str, ...] = DEFAULT_COMPARISON_STRATEGIES,
    fixture: str = "multi_day",
    save: bool = False,
    output_dir: Path | str = Path("data/experiments"),
    include_hermes_fixtures: bool = False,
    exclude_retired: bool = False,
    status_values: str | None = None,
    status_registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    selected_strategy_names = _comparison_strategy_names(
        strategy_names=strategy_names,
        include_hermes_fixtures=include_hermes_fixtures,
    )
    filter_result = _apply_status_filter(
        selected_strategy_names,
        exclude_retired=exclude_retired,
        status_values=status_values,
        status_registry_path=status_registry_path,
    )
    selected_strategy_names = filter_result.selected_strategy_ids
    if filter_result.filter.applied:
        print(format_status_filter_summary(filter_result))
        print("")
    if not selected_strategy_names:
        print("Comparison skipped: status filtering excluded every selected strategy.")
        return

    reports: list[dict] = []
    for strategy_name in selected_strategy_names:
        strategy = build_strategy(strategy_name)
        local_result = run_strategy_dry_run(strategy, settings, simulation_fixture=fixture)
        report_result = generate_daily_report(settings.database_path, run_id=local_result.run_id)
        if not report_result.ok or report_result.report is None:
            print(f"Comparison unavailable for {strategy.strategy_id}: {report_result.message}")
            raise SystemExit(1)
        reports.append(report_result.report)

    print(format_strategy_comparison(reports))
    if save:
        artifacts = save_strategy_comparison_artifacts(
            reports=reports,
            fixture_name=fixture,
            output_dir=output_dir,
            status_filter_metadata=status_filter_to_metadata(filter_result),
        )
        print("Saved comparison artifacts:")
        print(f"JSON: {artifacts.json_path}")
        print(f"CSV: {artifacts.csv_path}")
        print(f"Markdown: {artifacts.markdown_path}")


def run_fixture_sweep(
    strategy_names: tuple[str, ...] = DEFAULT_COMPARISON_STRATEGIES,
    include_hermes_fixtures: bool = False,
    save: bool = False,
    output_dir: Path | str = Path("data/experiments"),
    exclude_retired: bool = False,
    status_values: str | None = None,
    status_registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
) -> None:
    settings = Settings.from_env()
    initialize_database(settings.database_path)
    selected_strategy_names = _comparison_strategy_names(
        strategy_names=strategy_names,
        include_hermes_fixtures=include_hermes_fixtures,
    )
    filter_result = _apply_status_filter(
        selected_strategy_names,
        exclude_retired=exclude_retired,
        status_values=status_values,
        status_registry_path=status_registry_path,
    )
    selected_strategy_names = filter_result.selected_strategy_ids
    if not selected_strategy_names:
        if filter_result.filter.applied:
            print(format_status_filter_summary(filter_result))
            print("")
        print("Fixture sweep skipped: status filtering excluded every selected strategy.")
        return

    ranked_results_by_fixture: dict[str, list[dict]] = {}
    for fixture in FIXTURE_SWEEP_FIXTURES:
        reports: list[dict] = []
        for strategy_name in selected_strategy_names:
            strategy = build_strategy(strategy_name)
            local_result = run_strategy_dry_run(strategy, settings, simulation_fixture=fixture)
            report_result = generate_daily_report(settings.database_path, run_id=local_result.run_id)
            if not report_result.ok or report_result.report is None:
                print(f"Fixture sweep unavailable for {fixture}/{strategy.strategy_id}: {report_result.message}")
                raise SystemExit(1)
            reports.append(report_result.report)
        ranked_results_by_fixture[fixture] = rank_strategy_reports(reports)

    summary = summarize_fixture_sweep(ranked_results_by_fixture)
    status_by_strategy = load_latest_strategy_statuses(status_registry_path)
    if filter_result.filter.applied:
        print(format_status_filter_summary(filter_result))
        print("")
    print(
        format_fixture_sweep(
            summary,
            status_by_strategy=status_by_strategy,
        )
    )
    if save:
        artifacts = save_fixture_sweep_artifacts(
            summary=summary,
            output_dir=output_dir,
            status_by_strategy=status_by_strategy,
            status_filter_metadata=status_filter_to_metadata(filter_result),
        )
        print("Saved fixture sweep artifacts:")
        print(f"JSON: {artifacts.json_path}")
        print(f"CSV: {artifacts.csv_path}")
        print(f"Markdown: {artifacts.markdown_path}")


def run_tournament_history(output_dir: Path | str = Path("data/experiments")) -> None:
    history = load_tournament_history(output_dir)
    print(format_tournament_history(history, output_dir=output_dir))


def run_tournament_champion(output_dir: Path | str = Path("data/experiments")) -> None:
    champion = load_tournament_champion(output_dir)
    print(
        format_tournament_champion(
            champion,
            output_dir=output_dir,
            status_by_strategy=load_latest_strategy_statuses(),
        )
    )


def run_export_leaderboard(
    output_dir: Path | str = Path("data/experiments"),
    report_path: Path | str = Path("data/reports/strategy_leaderboard.md"),
) -> None:
    result = export_strategy_leaderboard(output_dir=output_dir, report_path=report_path)
    print(result.message)


def run_export_fixture_sweep_leaderboard(
    output_dir: Path | str = Path("data/experiments"),
    report_path: Path | str = Path("data/reports/fixture_sweep_leaderboard.md"),
) -> None:
    result = export_fixture_sweep_leaderboard(output_dir=output_dir, report_path=report_path)
    print(result.message)


def run_export_short_simulation_report(
    report_path: Path | str = DEFAULT_SHORT_SIMULATION_REPORT_PATH,
) -> None:
    result = export_shorting_simulation_report(report_path=report_path)
    print("simulation only")
    print(result.message)


def run_review_hermes_sandbox(file_path: Path | str) -> None:
    result = load_hermes_sandbox_file(file_path)
    print(format_hermes_sandbox_result(result))
    if not result.ok:
        raise SystemExit(1)


def run_hermes_teams(file_path: Path | str) -> None:
    try:
        registry = load_hermes_team_registry_file(file_path)
    except ValueError as exc:
        print(f"Hermes team registry unavailable: {exc}")
        raise SystemExit(1) from exc

    print(format_hermes_team_registry(registry))


def run_hermes_tournament_round_cli(
    registry_path: Path | str,
    proposal_paths: list[Path | str],
    save: bool = False,
    output_dir: Path | str = Path("data/experiments"),
) -> None:
    try:
        result = run_hermes_tournament_round(
            registry_path=registry_path,
            proposal_paths=proposal_paths,
        )
    except ValueError as exc:
        print(f"Hermes tournament round unavailable: {exc}")
        raise SystemExit(1) from exc

    print(format_hermes_tournament_round(result))
    if save:
        artifacts = save_hermes_tournament_round_artifacts(result, output_dir=output_dir)
        print("Saved Hermes tournament round artifacts:")
        print(f"JSON: {artifacts.json_path}")
        print(f"Markdown: {artifacts.markdown_path}")


def run_hermes_generate_proposals_cli(
    team_id: str,
    agent_id: str,
    agent_role: str,
    strategy_id: str,
    output_file: Path | str,
    learning_goal: str | None = None,
    strategy_notes: str | None = None,
) -> None:
    try:
        result = generate_hermes_proposals(
            config=HermesRuntimeConfig.from_env(),
            request=HermesGenerationRequest(
                team_id=team_id,
                agent_id=agent_id,
                agent_role=agent_role,
                strategy_id=strategy_id,
                learning_goal=learning_goal,
                strategy_notes=strategy_notes,
            ),
            output_file=output_file,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Hermes proposal generation unavailable: {exc}")
        raise SystemExit(1) from exc

    print(format_hermes_generation_result(result))


def run_discord_bot_cli() -> None:
    from src.discord_bot.bot import run_discord_bot

    run_discord_bot()


def run_dashboard_cli() -> None:
    """Launch the local-only Streamlit operator dashboard (paper-only, no live trading)."""

    import subprocess
    import sys

    dashboard_path = Path(__file__).resolve().parent / "ui" / "dashboard.py"
    print("Starting the ExaltedFable local dashboard (paper-only; no live trading).")
    print(f"If the browser does not open automatically, run: streamlit run {dashboard_path}")

    try:
        import streamlit  # noqa: F401
    except ImportError:
        print("Streamlit is not installed. Install it first with: pip install streamlit")
        print(f"Then run: streamlit run {dashboard_path}")
        raise SystemExit(1)

    subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard_path)], check=False)


def run_desktop_app_cli() -> None:
    """Launch the desktop-style local app wrapper around Streamlit."""

    from src.ui.desktop_app import launch_desktop_app

    raise SystemExit(launch_desktop_app())


def run_create_analysis_note(
    output_dir: Path | str = Path("data/experiments"),
    notes_dir: Path | str = Path("data/notes"),
    force: bool = False,
) -> None:
    result = create_strategy_analysis_note(output_dir=output_dir, notes_dir=notes_dir, force=force)
    print(result.message)


def run_create_sweep_analysis_note(
    output_dir: Path | str = Path("data/experiments"),
    notes_dir: Path | str = Path("data/notes"),
    force: bool = False,
) -> None:
    result = create_sweep_analysis_note(output_dir=output_dir, notes_dir=notes_dir, force=force)
    print(result.message)


def run_record_research_decision(
    strategy_id: str,
    decision: str,
    reason: str,
    ledger_path: Path | str = DEFAULT_DECISION_LEDGER_PATH,
    source_note: Path | str | None = None,
    next_action: str | None = None,
) -> None:
    try:
        result = record_research_decision(
            strategy_id=strategy_id,
            decision=decision,
            reason=reason,
            ledger_path=ledger_path,
            source_note=source_note,
            next_action=next_action,
        )
    except ValueError as exc:
        print(f"Research decision unavailable: {exc}")
        raise SystemExit(1) from exc

    print(result.message)


def run_research_decisions(ledger_path: Path | str = DEFAULT_DECISION_LEDGER_PATH) -> None:
    result = read_research_decision_ledger(ledger_path=ledger_path)
    print(result.message)


def run_set_strategy_status(
    strategy_id: str,
    status: str,
    reason: str,
    registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
    source_note: Path | str | None = None,
    next_action: str | None = None,
) -> None:
    try:
        result = set_strategy_status(
            strategy_id=strategy_id,
            status=status,
            reason=reason,
            registry_path=registry_path,
            source_note=source_note,
            next_action=next_action,
        )
    except ValueError as exc:
        print(f"Strategy status unavailable: {exc}")
        raise SystemExit(1) from exc

    print(result.message)


def run_strategy_status(registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH) -> None:
    result = read_strategy_status_registry(registry_path=registry_path)
    print(result.message)


def _comparison_strategy_names(
    strategy_names: tuple[str, ...],
    include_hermes_fixtures: bool,
) -> tuple[str, ...]:
    if not include_hermes_fixtures:
        return strategy_names

    selected = list(strategy_names)
    for strategy_name in HERMES_FIXTURE_STRATEGIES:
        if strategy_name not in selected:
            selected.append(strategy_name)
    return tuple(selected)


def _apply_status_filter(
    strategy_names: tuple[str, ...],
    exclude_retired: bool,
    status_values: str | None,
    status_registry_path: Path | str,
):
    try:
        included_statuses = parse_status_filter_values(status_values)
    except ValueError as exc:
        print(f"Status filter unavailable: {exc}")
        raise SystemExit(1) from exc

    status_filter = StrategyStatusFilter(
        exclude_retired=exclude_retired,
        included_statuses=included_statuses,
    )
    status_by_strategy = load_latest_strategy_statuses(status_registry_path)
    return filter_strategy_ids_by_status(strategy_names, status_by_strategy, status_filter)


def load_cli_dotenv() -> None:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)


def main() -> None:
    load_cli_dotenv()
    parser = argparse.ArgumentParser(description="ExaltedFable Agent Trading Lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Initialize SQLite database")
    dry_run_parser = subparsers.add_parser("dry-run", help="Run a local dry-run strategy cycle")
    dry_run_parser.add_argument(
        "--strategy",
        choices=KNOWN_STRATEGIES,
        default="spy_buy_hold",
        help="Local deterministic strategy to run. Defaults to spy_buy_hold.",
    )
    paper_status_parser = subparsers.add_parser("paper-status", help="Show Alpaca paper account status")
    paper_status_parser.add_argument(
        "--team",
        choices=CREDENTIAL_SOURCES,
        default="global",
        help="Credential source: global, team_alpha, or team_beta. Defaults to global.",
    )
    report_parser = subparsers.add_parser("report", help="Generate a local benchmark report")
    report_parser.add_argument(
        "--run-id",
        help="Generate a report for a specific run ID. Defaults to the latest run.",
    )
    report_parser.add_argument(
        "--latest",
        action="store_true",
        help="Generate a report for the latest run. This is the default.",
    )
    compare_parser = subparsers.add_parser(
        "compare-strategies",
        help="Run local dry-run strategies and print a run-aware comparison",
    )
    compare_parser.add_argument(
        "--strategies",
        nargs="+",
        choices=KNOWN_STRATEGIES,
        default=DEFAULT_COMPARISON_STRATEGIES,
        help="Local strategies to compare. Defaults to cash_only, spy_buy_hold, and momentum_v1.",
    )
    compare_parser.add_argument(
        "--fixture",
        choices=COMPARISON_FIXTURES,
        default="multi_day",
        help="Deterministic local simulation fixture for comparison reports. Defaults to multi_day.",
    )
    compare_parser.add_argument(
        "--save",
        action="store_true",
        help="Save JSON, CSV, and Markdown comparison artifacts to the output directory.",
    )
    compare_parser.add_argument(
        "--include-hermes-fixtures",
        action="store_true",
        help="Include parser-only local Hermes JSON fixture strategies in the comparison.",
    )
    compare_parser.add_argument(
        "--exclude-retired",
        action="store_true",
        help="Opt in to excluding strategies whose latest research status is retired.",
    )
    compare_parser.add_argument(
        "--status",
        help=(
            "Opt in to including only these comma-separated research statuses. "
            f"Allowed: {', '.join(ALLOWED_STATUS_FILTER_VALUES)}."
        ),
    )
    compare_parser.add_argument(
        "--status-registry-path",
        type=Path,
        default=DEFAULT_STRATEGY_STATUS_PATH,
        help="Markdown strategy status registry path. Defaults to data/notes/strategy_status.md.",
    )
    compare_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory for saved comparison artifacts. Defaults to data/experiments.",
    )
    fixture_sweep_parser = subparsers.add_parser(
        "fixture-sweep",
        help="Run local strategy comparison across deterministic non-flat fixtures",
    )
    fixture_sweep_parser.add_argument(
        "--include-hermes-fixtures",
        action="store_true",
        help="Include parser-only local Hermes JSON fixture strategies in the sweep.",
    )
    fixture_sweep_parser.add_argument(
        "--save",
        action="store_true",
        help="Save JSON, CSV, and Markdown fixture sweep artifacts to the output directory.",
    )
    fixture_sweep_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory for saved fixture sweep artifacts. Defaults to data/experiments.",
    )
    fixture_sweep_parser.add_argument(
        "--exclude-retired",
        action="store_true",
        help="Opt in to excluding strategies whose latest research status is retired.",
    )
    fixture_sweep_parser.add_argument(
        "--status",
        help=(
            "Opt in to including only these comma-separated research statuses. "
            f"Allowed: {', '.join(ALLOWED_STATUS_FILTER_VALUES)}."
        ),
    )
    fixture_sweep_parser.add_argument(
        "--status-registry-path",
        type=Path,
        default=DEFAULT_STRATEGY_STATUS_PATH,
        help="Markdown strategy status registry path. Defaults to data/notes/strategy_status.md.",
    )
    history_parser = subparsers.add_parser(
        "tournament-history",
        help="Review saved local compare-strategies JSON artifacts",
    )
    history_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    champion_parser = subparsers.add_parser(
        "tournament-champion",
        help="Summarize the current champion strategy across saved tournaments",
    )
    champion_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    leaderboard_parser = subparsers.add_parser(
        "export-leaderboard",
        help="Export a Markdown strategy leaderboard from saved ranked tournaments",
    )
    leaderboard_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    leaderboard_parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/strategy_leaderboard.md"),
        help="Markdown report path. Defaults to data/reports/strategy_leaderboard.md.",
    )
    fixture_sweep_leaderboard_parser = subparsers.add_parser(
        "export-fixture-sweep-leaderboard",
        help="Export a Markdown fixture sweep robustness leaderboard",
    )
    fixture_sweep_leaderboard_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved fixture sweep JSON artifacts. Defaults to data/experiments.",
    )
    fixture_sweep_leaderboard_parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/fixture_sweep_leaderboard.md"),
        help="Markdown report path. Defaults to data/reports/fixture_sweep_leaderboard.md.",
    )
    short_simulation_report_parser = subparsers.add_parser(
        "export-short-simulation-report",
        help="Export a local-only deterministic shorting simulation report",
    )
    short_simulation_report_parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_SHORT_SIMULATION_REPORT_PATH,
        help="Markdown report path. Defaults to data/reports/shorting_simulation_report.md.",
    )
    hermes_sandbox_parser = subparsers.add_parser(
        "review-hermes-sandbox",
        help="Review strict local Hermes strategy sandbox JSON without execution",
    )
    hermes_sandbox_parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Local Hermes strategy sandbox JSON file to review.",
    )
    hermes_teams_parser = subparsers.add_parser(
        "hermes-teams",
        help="Review a strict local Hermes team registry without runtime calls",
    )
    hermes_teams_parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Local Hermes team registry JSON file to review.",
    )
    hermes_tournament_parser = subparsers.add_parser(
        "hermes-tournament-round",
        help="Run a local-only Hermes team proposal routing tournament",
    )
    hermes_tournament_parser.add_argument(
        "--registry",
        type=Path,
        required=True,
        help="Local Hermes team registry JSON file.",
    )
    hermes_tournament_parser.add_argument(
        "--proposal",
        action="append",
        required=True,
        help="Local Hermes proposal JSON file. Repeat or comma-separate for multiple files.",
    )
    hermes_tournament_parser.add_argument(
        "--save",
        action="store_true",
        help="Save local JSON and Markdown tournament artifacts.",
    )
    hermes_tournament_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory for saved tournament artifacts. Defaults to data/experiments.",
    )
    hermes_generate_parser = subparsers.add_parser(
        "hermes-generate-proposals",
        help="Generate strict local Hermes sandbox proposal JSON through an opt-in runtime endpoint",
    )
    hermes_generate_parser.add_argument("--team-id", required=True, help="Hermes team ID for the generated file.")
    hermes_generate_parser.add_argument("--agent-id", required=True, help="Hermes agent ID for the generated file.")
    hermes_generate_parser.add_argument("--agent-role", required=True, help="Hermes agent role for the generated file.")
    hermes_generate_parser.add_argument("--strategy-id", required=True, help="Strategy ID for the generated file.")
    hermes_generate_parser.add_argument(
        "--output-file",
        type=Path,
        required=True,
        help="Local file path for the raw generated Hermes JSON.",
    )
    hermes_generate_parser.add_argument("--learning-goal", help="Optional runtime learning goal.")
    hermes_generate_parser.add_argument("--strategy-notes", help="Optional runtime strategy notes.")
    analysis_note_parser = subparsers.add_parser(
        "create-analysis-note",
        help="Create a Markdown human review note from the latest saved ranked tournament",
    )
    analysis_note_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved comparison JSON artifacts. Defaults to data/experiments.",
    )
    analysis_note_parser.add_argument(
        "--notes-dir",
        type=Path,
        default=Path("data/notes"),
        help="Directory for analysis note Markdown files. Defaults to data/notes.",
    )
    analysis_note_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the analysis note if the deterministic filename already exists.",
    )
    sweep_analysis_note_parser = subparsers.add_parser(
        "create-sweep-analysis-note",
        help="Create a Markdown human review note from the latest saved fixture sweep",
    )
    sweep_analysis_note_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiments"),
        help="Directory containing saved fixture sweep JSON artifacts. Defaults to data/experiments.",
    )
    sweep_analysis_note_parser.add_argument(
        "--notes-dir",
        type=Path,
        default=Path("data/notes"),
        help="Directory for sweep analysis note Markdown files. Defaults to data/notes.",
    )
    sweep_analysis_note_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the sweep analysis note if the deterministic filename already exists.",
    )
    decision_parser = subparsers.add_parser(
        "record-research-decision",
        help="Append a local strategy research decision to the Markdown ledger",
    )
    decision_parser.add_argument("--strategy-id", required=True, help="Strategy ID the decision applies to.")
    decision_parser.add_argument(
        "--decision",
        choices=ALLOWED_RESEARCH_DECISIONS,
        required=True,
        help="Research decision for the strategy.",
    )
    decision_parser.add_argument("--reason", required=True, help="Human-readable reason for the decision.")
    decision_parser.add_argument(
        "--source-note",
        type=Path,
        help="Optional source analysis note path.",
    )
    decision_parser.add_argument("--next-action", help="Optional follow-up action to test next.")
    decision_parser.add_argument(
        "--ledger-path",
        type=Path,
        default=DEFAULT_DECISION_LEDGER_PATH,
        help="Markdown decision ledger path. Defaults to data/notes/research_decisions.md.",
    )
    read_decisions_parser = subparsers.add_parser(
        "research-decisions",
        help="Print the local strategy research decision ledger",
    )
    read_decisions_parser.add_argument(
        "--ledger-path",
        type=Path,
        default=DEFAULT_DECISION_LEDGER_PATH,
        help="Markdown decision ledger path. Defaults to data/notes/research_decisions.md.",
    )
    status_parser = subparsers.add_parser(
        "set-strategy-status",
        help="Append a local research status for a strategy",
    )
    status_parser.add_argument("--strategy-id", required=True, help="Strategy ID the status applies to.")
    status_parser.add_argument(
        "--status",
        choices=ALLOWED_STRATEGY_STATUSES,
        required=True,
        help="Research status for the strategy.",
    )
    status_parser.add_argument("--reason", required=True, help="Human-readable reason for the status.")
    status_parser.add_argument(
        "--source-note",
        type=Path,
        help="Optional source analysis note path.",
    )
    status_parser.add_argument("--next-action", help="Optional follow-up action to test next.")
    status_parser.add_argument(
        "--registry-path",
        type=Path,
        default=DEFAULT_STRATEGY_STATUS_PATH,
        help="Markdown strategy status registry path. Defaults to data/notes/strategy_status.md.",
    )
    read_status_parser = subparsers.add_parser(
        "strategy-status",
        help="Print the local strategy status registry",
    )
    read_status_parser.add_argument(
        "--registry-path",
        type=Path,
        default=DEFAULT_STRATEGY_STATUS_PATH,
        help="Markdown strategy status registry path. Defaults to data/notes/strategy_status.md.",
    )
    subparsers.add_parser(
        "discord-bot",
        help="Run the safe local Discord command-center bot",
    )
    subparsers.add_parser(
        "dashboard",
        help="Launch the local-only Streamlit operator dashboard (paper-only)",
    )
    subparsers.add_parser(
        "app",
        help="Launch the optional desktop-style app window around the Streamlit dashboard",
    )

    # --- Weekly competition + advanced paper-trading controls (paper-only) ---
    subparsers.add_parser("paper-permissions", help="Show current paper permission levels and risk caps")
    subparsers.add_parser(
        "alpaca-auth-diagnose",
        help="Diagnose global/alpha/beta Alpaca paper auth (never prints secrets)",
    )
    subparsers.add_parser(
        "competition-readiness-check",
        help="Show per-team competition readiness and exact blockers (paper-only)",
    )
    subparsers.add_parser(
        "research-status",
        help="Show research provider config and latest per-team research",
    )
    attribution_parser = subparsers.add_parser(
        "proposal-attribution",
        help="Show proposal/trade effectiveness attribution for a team",
    )
    attribution_parser.add_argument("--team", required=True, choices=WEEK_TEAMS, help="Team to show.")
    refresh_attr_parser = subparsers.add_parser(
        "refresh-proposal-attribution",
        help="Refresh pending proposal outcomes with latest prices + SPY benchmark (paper-only)",
    )
    refresh_attr_parser.add_argument(
        "--team",
        choices=WEEK_TEAMS,
        default=None,
        help="Optional team filter. Defaults to refreshing both teams.",
    )
    refresh_attr_parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "Excess-return threshold for worked/failed/mixed verdicts. "
            "Defaults to env ATTRIBUTION_OUTCOME_THRESHOLD, then 0.005."
        ),
    )
    subparsers.add_parser("start-week-competition", help="Start the Alpha vs Beta weekly paper competition")
    week_cycle_parser = subparsers.add_parser("run-week-cycle", help="Run one gated paper cycle for a team")
    week_cycle_parser.add_argument("--team", required=True, choices=WEEK_TEAMS, help="Team to run a cycle for.")
    week_cycle_parser.add_argument(
        "--proposal-source",
        choices=VALID_PROPOSAL_SOURCES,
        default=None,
        help=(
            "Proposal generator: 'default' (deterministic) or 'llm' (provider). "
            "Defaults to env WEEK_COMPETITION_PROPOSAL_SOURCE, then 'default'."
        ),
    )
    week_cycle_parser.add_argument(
        "--review-only",
        action="store_true",
        help=(
            "Run portfolio/strategy review + update memory/scorecard, but submit NO new broker "
            "orders. Produces advisory hold/trim/close recommendations only."
        ),
    )
    gate_parser = subparsers.add_parser(
        "cheap-cycle-gate",
        help="Decide (cheaply, no LLM) whether a full run-week-cycle is worth running",
    )
    gate_parser.add_argument("--team", required=True, choices=WEEK_TEAMS, help="Team to evaluate.")
    spy_attr_parser = subparsers.add_parser(
        "daily-spy-attribution",
        help="Explain why each team beat or lost to SPY using local data (paper-only)",
    )
    spy_attr_parser.add_argument(
        "--team", choices=WEEK_TEAMS, default=None, help="Optional team filter. Defaults to both teams."
    )
    daily_review_parser = subparsers.add_parser(
        "export-daily-team-review",
        help="Build + persist a compact daily strategy-review artifact under data/reviews/",
    )
    daily_review_parser.add_argument(
        "--team", choices=WEEK_TEAMS, default=None, help="Optional team filter. Defaults to both teams."
    )
    subparsers.add_parser(
        "llm-routing-status",
        help="Show task-specific LLM model routing (model names only; never secrets)",
    )
    subparsers.add_parser(
        "llm-review-status",
        help="Show which advisory LLM review stages are enabled + model per stage (no secrets)",
    )
    llm_daily_review_parser = subparsers.add_parser(
        "run-llm-daily-review",
        help="Advisory LLM daily review + multi-day strategy memory (submits NO orders)",
    )
    llm_daily_review_parser.add_argument(
        "--team", choices=WEEK_TEAMS, default=None, help="Optional team filter. Defaults to both teams."
    )
    discord_iter_parser = subparsers.add_parser(
        "discord-iteration-update",
        help="Build/send Phase 7S Discord team-thought briefs (paper-only; --dry-run to preview)",
    )
    discord_iter_parser.add_argument(
        "--team", choices=("team_alpha", "team_beta", "both"), default="both",
        help="Team(s) to brief. Defaults to both.",
    )
    discord_iter_parser.add_argument(
        "--summary", action="store_true",
        help="Also build/send the Alpha-vs-Beta scoreboard summary.",
    )
    discord_iter_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the message(s) that would be sent without calling the Discord API.",
    )
    cheap_loop_parser = subparsers.add_parser(
        "run-cheap-competition-loop",
        help="Cost-saving runner: refresh + gate every interval; full LLM cycle only when the gate says so",
    )
    cheap_loop_parser.add_argument("--once", action="store_true", help="Run a single iteration and exit.")
    cheap_loop_parser.add_argument(
        "--sleep-seconds", type=int, default=900, help="Seconds to sleep between iterations. Defaults to 900."
    )
    cheap_loop_parser.add_argument(
        "--team", choices=("team_alpha", "team_beta", "both"), default="both",
        help="Team(s) to evaluate each iteration. Defaults to both.",
    )
    cheap_loop_parser.add_argument(
        "--market-hours-only", dest="market_hours_only", action="store_true", default=True,
        help="Only run full cycles when the market is open (best-effort). Default.",
    )
    cheap_loop_parser.add_argument(
        "--no-market-hours-only", dest="market_hours_only", action="store_false",
        help="Run full cycles regardless of market open status.",
    )
    cheap_loop_parser.add_argument(
        "--run-review-only-when-skipped", action="store_true",
        help="When the gate skips a full cycle, run a review-only cycle instead (advisory, no orders).",
    )
    cheap_loop_parser.add_argument(
        "--llm-review-when-skipped", action="store_true",
        help=(
            "When the gate skips a full cycle, run review-only + cheap LLM advisory daily review "
            "(critique/summary if enabled). Never runs the strategy model and never submits orders."
        ),
    )
    cheap_loop_parser.add_argument(
        "--llm-daily-review-at-close", action="store_true",
        help="At market close, run the advisory LLM daily review (no orders). Optional.",
    )
    cheap_loop_parser.add_argument(
        "--dry-run-loop", action="store_true",
        help="Print intended actions each iteration without running full cycles.",
    )
    subparsers.add_parser("week-competition-status", help="Show Alpha vs Beta competition status and ranking")
    subparsers.add_parser("stop-week-competition", help="Stop the weekly paper competition")
    learning_parser = subparsers.add_parser("team-learning-status", help="Show a team's learning ledger")
    learning_parser.add_argument("--team", required=True, choices=WEEK_TEAMS, help="Team to show learning for.")
    scorecards_parser = subparsers.add_parser("export-team-scorecards", help="Export team scorecards to Markdown")
    scorecards_parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("data/reports/team_scorecards.md"),
        help="Markdown report path. Defaults to data/reports/team_scorecards.md.",
    )
    kill_on_parser = subparsers.add_parser("kill-switch-on", help="Engage the global kill switch")
    kill_on_parser.add_argument("--reason", help="Optional reason for engaging the kill switch.")
    subparsers.add_parser("kill-switch-off", help="Disengage the global kill switch")
    subparsers.add_parser("kill-switch-status", help="Show the global kill switch state")

    args = parser.parse_args()

    if args.command == "init-db":
        run_init_db()
    elif args.command == "dry-run":
        run_dry_run(strategy_name=args.strategy)
    elif args.command == "paper-status":
        run_paper_status(team=args.team)
    elif args.command == "report":
        run_report(run_id=args.run_id)
    elif args.command == "compare-strategies":
        run_compare_strategies(
            strategy_names=tuple(args.strategies),
            fixture=args.fixture,
            save=args.save,
            output_dir=args.output_dir,
            include_hermes_fixtures=args.include_hermes_fixtures,
            exclude_retired=args.exclude_retired,
            status_values=args.status,
            status_registry_path=args.status_registry_path,
        )
    elif args.command == "fixture-sweep":
        run_fixture_sweep(
            include_hermes_fixtures=args.include_hermes_fixtures,
            save=args.save,
            output_dir=args.output_dir,
            exclude_retired=args.exclude_retired,
            status_values=args.status,
            status_registry_path=args.status_registry_path,
        )
    elif args.command == "tournament-history":
        run_tournament_history(output_dir=args.output_dir)
    elif args.command == "tournament-champion":
        run_tournament_champion(output_dir=args.output_dir)
    elif args.command == "export-leaderboard":
        run_export_leaderboard(output_dir=args.output_dir, report_path=args.report_path)
    elif args.command == "export-fixture-sweep-leaderboard":
        run_export_fixture_sweep_leaderboard(output_dir=args.output_dir, report_path=args.report_path)
    elif args.command == "export-short-simulation-report":
        run_export_short_simulation_report(report_path=args.report_path)
    elif args.command == "review-hermes-sandbox":
        run_review_hermes_sandbox(file_path=args.file)
    elif args.command == "hermes-teams":
        run_hermes_teams(file_path=args.file)
    elif args.command == "hermes-tournament-round":
        try:
            proposal_paths = _proposal_paths_from_args(args.proposal)
        except ValueError as exc:
            print(f"Hermes tournament round unavailable: {exc}")
            raise SystemExit(1) from exc
        run_hermes_tournament_round_cli(
            registry_path=args.registry,
            proposal_paths=proposal_paths,
            save=args.save,
            output_dir=args.output_dir,
        )
    elif args.command == "hermes-generate-proposals":
        run_hermes_generate_proposals_cli(
            team_id=args.team_id,
            agent_id=args.agent_id,
            agent_role=args.agent_role,
            strategy_id=args.strategy_id,
            output_file=args.output_file,
            learning_goal=args.learning_goal,
            strategy_notes=args.strategy_notes,
        )
    elif args.command == "create-analysis-note":
        run_create_analysis_note(output_dir=args.output_dir, notes_dir=args.notes_dir, force=args.force)
    elif args.command == "create-sweep-analysis-note":
        run_create_sweep_analysis_note(output_dir=args.output_dir, notes_dir=args.notes_dir, force=args.force)
    elif args.command == "record-research-decision":
        run_record_research_decision(
            strategy_id=args.strategy_id,
            decision=args.decision,
            reason=args.reason,
            ledger_path=args.ledger_path,
            source_note=args.source_note,
            next_action=args.next_action,
        )
    elif args.command == "research-decisions":
        run_research_decisions(ledger_path=args.ledger_path)
    elif args.command == "set-strategy-status":
        run_set_strategy_status(
            strategy_id=args.strategy_id,
            status=args.status,
            reason=args.reason,
            registry_path=args.registry_path,
            source_note=args.source_note,
            next_action=args.next_action,
        )
    elif args.command == "strategy-status":
        run_strategy_status(registry_path=args.registry_path)
    elif args.command == "paper-permissions":
        run_paper_permissions()
    elif args.command == "alpaca-auth-diagnose":
        run_alpaca_auth_diagnose()
    elif args.command == "competition-readiness-check":
        run_competition_readiness_check()
    elif args.command == "research-status":
        run_research_status()
    elif args.command == "proposal-attribution":
        run_proposal_attribution(team=args.team)
    elif args.command == "refresh-proposal-attribution":
        run_refresh_proposal_attribution(team=args.team, threshold=args.threshold)
    elif args.command == "start-week-competition":
        run_start_week_competition()
    elif args.command == "run-week-cycle":
        run_week_cycle_cli(
            team=args.team, proposal_source=args.proposal_source, review_only=args.review_only
        )
    elif args.command == "cheap-cycle-gate":
        run_cheap_cycle_gate(team=args.team)
    elif args.command == "daily-spy-attribution":
        run_daily_spy_attribution(team=args.team)
    elif args.command == "export-daily-team-review":
        run_export_daily_team_review(team=args.team)
    elif args.command == "llm-routing-status":
        run_llm_routing_status()
    elif args.command == "llm-review-status":
        run_llm_review_status()
    elif args.command == "run-llm-daily-review":
        run_llm_daily_review(team=args.team)
    elif args.command == "discord-iteration-update":
        run_discord_iteration_update(team=args.team, summary=args.summary, dry_run=args.dry_run)
    elif args.command == "run-cheap-competition-loop":
        run_cheap_competition_loop(
            once=args.once,
            sleep_seconds=args.sleep_seconds,
            team=args.team,
            market_hours_only=args.market_hours_only,
            run_review_only_when_skipped=args.run_review_only_when_skipped,
            llm_review_when_skipped=args.llm_review_when_skipped,
            llm_daily_review_at_close=args.llm_daily_review_at_close,
            dry_run_loop=args.dry_run_loop,
        )
    elif args.command == "week-competition-status":
        run_week_competition_status()
    elif args.command == "stop-week-competition":
        run_stop_week_competition()
    elif args.command == "team-learning-status":
        run_team_learning_status(team=args.team)
    elif args.command == "export-team-scorecards":
        run_export_team_scorecards(report_path=args.report_path)
    elif args.command == "kill-switch-on":
        run_kill_switch_on(reason=args.reason)
    elif args.command == "kill-switch-off":
        run_kill_switch_off()
    elif args.command == "kill-switch-status":
        run_kill_switch_status()
    elif args.command == "discord-bot":
        run_discord_bot_cli()
    elif args.command == "dashboard":
        run_dashboard_cli()
    elif args.command == "app":
        run_desktop_app_cli()
    else:
        raise ValueError(f"Unknown command: {args.command}")


def _proposal_paths_from_args(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        paths.extend(Path(part.strip()) for part in value.split(",") if part.strip())
    if not paths:
        raise ValueError("At least one --proposal path is required.")
    return paths


if __name__ == "__main__":
    main()
