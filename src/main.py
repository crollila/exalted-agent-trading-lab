from __future__ import annotations

import argparse
import os
import sys
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
from src.competition.market_time import ny_session_start_utc, ny_trading_date, now_utc, to_ny
from src.competition.quiet_mode import OFF_HOURS_SLEEP_NOTICE, OffHoursQuietConfig
from src.competition.tomorrow_plan import (
    export_tomorrow_plan,
    format_tomorrow_plan_terminal,
    post_tomorrow_plan_to_discord,
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
from src.competition.live_equity import (
    CACHED_SOURCE_LABEL,
    LIVE_SOURCE_LABEL,
    refresh_competition_equity,
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


def _orders_today_for_source(source: str, settings: Settings) -> int:
    """Count this team's paper orders for the current ET trading date (read-only).

    Reconciles the deterministic per-team daily-order cap against orders that
    were actually submitted to the Alpaca paper account, scoped to midnight
    America/New_York. Degrades to 0 on any failure (never crashes a cycle and
    never submits anything). A 0 fallback fails *open* only for the daily-order
    cap; every other deterministic risk gate still applies.
    """

    try:
        client = client_for_source(source, base_settings=settings)
    except Exception:  # noqa: BLE001 - missing/invalid creds -> cannot reconcile; degrade
        return 0
    if client is None or not client.has_credentials():
        return 0
    try:
        return int(client.count_orders_since(ny_session_start_utc()))
    except Exception as exc:  # noqa: BLE001 - read-only count must never break the cycle
        print(f"({source} daily-order reconciliation unavailable: {exc}; assuming 0 today.)")
        return 0


def _daily_notional_for_source(source: str, settings: Settings):
    """Reconcile this team's gross paper notional for the current ET trading date.

    Returns a ``NotionalReconciliation`` (used, source, status). Broker submitted
    orders are the authority; locally-persisted attribution records are a safe
    fallback when the broker is unavailable. Never uses LLM output as authority,
    never submits, and degrades to (0.0, unavailable) rather than crashing.
    """

    from src.competition.daily_notional import (
        NotionalReconciliation,
        daily_notional_from_attribution,
    )

    # 1) Broker-authoritative path.
    try:
        client = client_for_source(source, base_settings=settings)
        if client is not None and client.has_credentials():
            used = float(client.daily_notional_since(ny_session_start_utc()))
            return NotionalReconciliation(used=used, source="broker", status="ok")
    except Exception as exc:  # noqa: BLE001 - fall through to the local fallback
        print(f"({source} daily-notional broker reconciliation unavailable: {exc}; trying local records.)")

    # 2) Local persisted attribution fallback (still submitted-only, ET-scoped).
    try:
        entries = load_team_attribution(source)
        used = float(daily_notional_from_attribution(entries))
        return NotionalReconciliation(used=used, source="local_fallback", status="fallback")
    except Exception as exc:  # noqa: BLE001 - last resort: report unavailable, fail safe-open
        print(f"({source} daily-notional local fallback unavailable: {exc}; assuming 0.)")
        return NotionalReconciliation(used=0.0, source="unavailable", status="unavailable")


def _account_context_for_source(
    source: str, settings: Settings, *, reconcile_orders: bool = True
) -> "AccountContext":
    """Build a deterministic AccountContext from a specific credential source.

    Team sources never fall back to global keys. If the source's account is
    unavailable, fall back to a deterministic STARTING_EQUITY context (no global
    credentials are ever used for a team). ``orders_today`` is reconciled against
    the team's actual paper orders for the current ET trading date so the
    per-team daily-order cap is enforced across the whole day (previously it was
    always 0, so the cap never engaged and a single busy session could exhaust
    buying power). Pass ``reconcile_orders=False`` for read-only/no-broker paths.
    """

    orders_today = _orders_today_for_source(source, settings) if reconcile_orders else 0
    daily_notional_today = (
        _daily_notional_for_source(source, settings).used if reconcile_orders else 0.0
    )
    diagnosis = diagnose_source(source, base_settings=settings)
    if diagnosis.auth_ok and diagnosis.account:
        try:
            return AccountContext(
                equity=float(diagnosis.account["equity"]),
                cash=float(diagnosis.account["cash"]),
                buying_power=float(diagnosis.account["buying_power"]),
                orders_today=orders_today,
                daily_notional_today=daily_notional_today,
                as_of=ny_trading_date(),
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
        orders_today=orders_today,
        daily_notional_today=daily_notional_today,
        as_of=ny_trading_date(),
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


def run_export_tomorrow_plan(team: str = "both") -> None:
    """Build + persist the Phase 7T Tomorrow Plan artifact(s) under data/reviews/.

    Paper-only, deterministic, no orders. Optionally posts a compact plan to
    Discord when DISCORD_POST_TOMORROW_PLAN=true (disabled by default).
    """

    teams = list(WEEK_TEAMS) if team == "both" else [team]
    for tid in teams:
        plan, saved = export_tomorrow_plan(tid)
        print(format_tomorrow_plan_terminal(plan, saved_paths=saved))
        try:
            result = post_tomorrow_plan_to_discord(plan)
            if result.get("sent"):
                print("(posted compact Tomorrow Plan to Discord)")
        except Exception as exc:  # noqa: BLE001 - Discord must never crash the export
            print(f"(Discord tomorrow-plan post unavailable: {exc}; continuing)")
        print("")


def run_market_hours_quiet_status() -> None:
    """Show strict off-hours quiet-mode config + what the loop skips. No secrets."""

    config = OffHoursQuietConfig.from_env()
    market_open = _cheap_loop_market_open()
    state = "open" if market_open is True else ("closed" if market_open is False else "unknown")
    print("=== Market-hours quiet-mode status (Phase 7T; paper-only, no secrets) ===")
    print(f"Market: {state}")
    print(f"STRICT_MARKET_HOURS_ONLY: {config.strict_market_hours_only}")
    print(f"ALLOW_OFF_HOURS_STATUS_REFRESH: {config.allow_off_hours_status_refresh}")
    print(f"ALLOW_OFF_HOURS_ATTRIBUTION_REFRESH: {config.allow_off_hours_attribution_refresh}")
    print(f"ALLOW_OFF_HOURS_LIVE_EQUITY_REFRESH: {config.allow_off_hours_live_equity_refresh}")
    print(f"ALLOW_OFF_HOURS_DISCORD: {config.allow_off_hours_discord}")
    print(f"ALLOW_OFF_HOURS_LLM_REVIEW: {config.allow_off_hours_llm_review}")
    print(f"OFF_HOURS_POST_ONE_SLEEP_NOTICE: {config.post_one_sleep_notice}")
    print("When the market is closed and strict mode is on, the loop will skip:")
    for item in config.skipped_when_closed():
        print(f"  - {item}")
    print("Deterministic risk gates and the kill switch remain authoritative; LLMs do not execute orders.")


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


def _cheap_loop_clock_snapshot(settings: Settings) -> dict | None:
    """Best-effort read-only clock snapshot (is_open + next open/close). None on failure."""

    for source in ("team_alpha", "team_beta"):
        client = _safe_read_client(source, settings)
        if client is None:
            continue
        try:
            return client.get_clock_snapshot()
        except Exception:  # noqa: BLE001 - degrade to undeterminable
            continue
    return None


def _gather_team_loop_facts(team: str, *, settings: Settings, clock: dict | None):
    """Collect read-only facts for one team's loop diagnosis. No LLM, no orders.

    Every broker call here is a read-only GET (account, positions, clock, order
    list). Nothing is generated or submitted.
    """

    from src.competition.loop_diagnostics import TeamLoopFacts
    from src.competition.iteration_audit import latest_status_age_seconds
    from src.config.portfolio_limits import PortfolioLimits
    from src.research.data_tools import alpaca_positions

    permissions = TradingPermissions.from_env()
    gate_config = CheapCycleGateConfig.from_env()
    pm_config = PortfolioManagerConfig.from_env()
    quiet_config = OffHoursQuietConfig.from_env()

    now = now_utc()
    market_hours_only = os.getenv("CHEAP_LOOP_MARKET_HOURS_ONLY", "true").strip().lower() not in {"0", "false", "no", "off"}
    review_only = os.getenv("REVIEW_ONLY_DURING_MARKET_HOURS", "true").strip().lower() in {"1", "true", "yes", "on"}

    market_is_open = clock.get("is_open") if clock else None

    # Account (read-only). Never falls back to global creds for a team.
    diagnosis = diagnose_source(team, base_settings=settings)
    equity = cash = bp = None
    account_ok = bool(diagnosis.auth_ok and diagnosis.account)
    if account_ok:
        try:
            equity = float(diagnosis.account["equity"])
            cash = float(diagnosis.account["cash"])
            bp = float(diagnosis.account["buying_power"])
        except (TypeError, ValueError, KeyError):
            account_ok = False

    # Open positions (best-effort, read-only).
    open_positions = None
    read_client = _safe_read_client(team, settings)
    if read_client is not None:
        try:
            open_positions = len(alpaca_positions(read_client).value or [])
        except Exception:  # noqa: BLE001 - positions are best-effort context only
            open_positions = None

    low_bp_threshold = pm_config.low_buying_power_review_threshold_pct
    low_bp = False
    if account_ok and equity and equity > 0:
        bp_for_ratio = bp if bp is not None else (cash or 0.0)
        low_bp = (bp_for_ratio / equity) < low_bp_threshold

    orders_today = _orders_today_for_source(team, settings) if account_ok else None
    notional_recon = _daily_notional_for_source(team, settings) if account_ok else None
    pl_config = PortfolioLimits.from_env()

    decision, _ = _evaluate_team_cheap_gate(team)

    # Latest recorded cycle (scorecard).
    scorecard = load_latest_scorecard(team)
    no_trade_reason = None
    if scorecard is not None:
        if getattr(scorecard, "portfolio_no_trade", False):
            if low_bp or (scorecard.buying_power == 0):
                no_trade_reason = "Low buying power: deterministic gate blocks new-money buys."
            elif (scorecard.proposals_count or 0) == 0:
                no_trade_reason = "No proposal candidates cleared review (model held / proposed nothing)."
            else:
                no_trade_reason = f"Portfolio manager decision: {scorecard.portfolio_decision_type}."

    last_audit_iso, audit_age = latest_status_age_seconds(team, now=now)
    heartbeat_stale = audit_age is not None and audit_age > max(3 * 900, 3 * int(os.getenv("CHEAP_LOOP_SLEEP_SECONDS", "900") or "900"))

    return TeamLoopFacts(
        team_id=team,
        local_iso=now.astimezone().isoformat(),
        ny_iso=to_ny(now).isoformat(),
        market_is_open=market_is_open,
        clock_next_open=(clock or {}).get("next_open"),
        clock_next_close=(clock or {}).get("next_close"),
        clock_note=None if clock else "no working team credential could read the clock",
        kill_switch_engaged=read_kill_switch().engaged,
        dry_run=settings.dry_run,
        trading_mode=permissions.trading_mode,
        stocks_enabled=permissions.stocks_enabled(),
        strict_market_hours_only=quiet_config.strict_market_hours_only,
        market_hours_only=market_hours_only,
        review_only_during_market_hours=review_only,
        sleep_seconds=int(os.getenv("CHEAP_LOOP_SLEEP_SECONDS", "900") or "900"),
        cheap_gate_enabled=gate_config.enabled,
        min_full_cycle_interval_minutes=gate_config.interval_for(team),
        proposal_source=_resolve_proposal_source_name(None),
        account_ok=account_ok,
        account_classification=diagnosis.classification,
        equity=equity,
        cash=cash,
        buying_power=bp,
        open_positions=open_positions,
        low_buying_power=low_bp,
        low_bp_threshold_pct=low_bp_threshold,
        orders_today=orders_today,
        max_daily_orders_per_team=permissions.max_daily_orders_per_team,
        daily_notional_today=(notional_recon.used if notional_recon else None),
        max_daily_notional_per_team=pl_config.max_daily_notional_per_team,
        daily_notional_source=(notional_recon.source if notional_recon else None),
        daily_notional_reconciliation_status=(notional_recon.status if notional_recon else None),
        gate_should_run_full_cycle=decision.should_run_full_cycle,
        gate_recommend_review_only=decision.recommend_review_only,
        gate_reason=decision.reason,
        latest_scorecard_path=f"data/scorecards/{team}_latest.json" if scorecard else None,
        latest_cycle_at=getattr(scorecard, "week_start", None) if scorecard else None,
        proposals_count=getattr(scorecard, "proposals_count", None) if scorecard else None,
        approved_count=getattr(scorecard, "approved_count", None) if scorecard else None,
        rejected_count=getattr(scorecard, "rejected_count", None) if scorecard else None,
        simulation_only_count=getattr(scorecard, "simulation_only_count", None) if scorecard else None,
        orders_submitted=getattr(scorecard, "orders_submitted", None) if scorecard else None,
        broker_rejected_count=getattr(scorecard, "broker_rejected_count", None) if scorecard else None,
        portfolio_decision_type=getattr(scorecard, "portfolio_decision_type", None) if scorecard else None,
        portfolio_no_trade=getattr(scorecard, "portfolio_no_trade", None) if scorecard else None,
        no_trade_reason=no_trade_reason,
        last_audit_iso=last_audit_iso,
        audit_age_seconds=audit_age,
        loop_heartbeat_stale=bool(heartbeat_stale),
    )


def run_diagnose_competition_loop(team: str = "both") -> None:
    """Non-trading diagnostic for the cheap competition loop (Phase 7U).

    Read-only: never generates proposals, never calls an LLM, never submits an
    order. Works while the market is closed. Prints a full per-team report plus a
    final diagnosis enum, and surfaces the tracked loop process/PID/log state.
    """

    from src.competition.loop_diagnostics import classify_diagnosis, format_team_report
    from src.competition.iteration_audit import AUDIT_JSONL_NAME, resolve_audit_dir

    audit_dir = resolve_audit_dir()

    settings = Settings.from_env()
    teams = list(WEEK_TEAMS) if team == "both" else [team]

    print("=== diagnose-competition-loop (Phase 7U; paper-only; READ-ONLY; no LLM, no orders) ===")
    print("This command never generates proposals, never calls an LLM, and never submits orders.")
    print("")

    # Tracked loop process / PID / log state (best-effort; no UI/streamlit import side effects).
    try:
        from src.ui.operator_controls import cheap_loop_log_path, cheap_loop_pid_path
        from src.ui.process_control import is_process_running, read_pid

        pid_path = cheap_loop_pid_path()
        pid = read_pid(pid_path)
        alive = is_process_running(pid) if pid is not None else False
        log_path = cheap_loop_log_path()
        print("Tracked cheap-loop process (data/runtime):")
        print(f"  pid_file={pid_path} pid={pid} running={alive}")
        if pid is not None and not alive:
            print("  WARNING: tracked PID is not alive (stale PID file). The loop may have stopped.")
        print(f"  log_file={log_path} exists={log_path.exists()}")
        print(f"  audit_jsonl={audit_dir / AUDIT_JSONL_NAME} "
              f"exists={(audit_dir / AUDIT_JSONL_NAME).exists()}")
    except Exception as exc:  # noqa: BLE001 - process introspection is best-effort
        print(f"(process/PID introspection unavailable: {exc})")
    print("")

    clock = _cheap_loop_clock_snapshot(settings)

    summary: list[tuple[str, str]] = []
    for tid in teams:
        facts = _gather_team_loop_facts(tid, settings=settings, clock=clock)
        diagnosis = classify_diagnosis(facts)
        print(format_team_report(facts, diagnosis))
        print("")
        summary.append((tid, diagnosis.diagnosis))

    print("=== Summary ===")
    for tid, diag in summary:
        print(f"  {tid}: {diag}")


def _normalize_team_arg(team: str) -> list[str]:
    """Map alpha|beta|team_alpha|team_beta|both to a list of canonical team ids."""

    value = (team or "both").strip().lower()
    if value in {"both", "all", ""}:
        return list(WEEK_TEAMS)
    alias = {"alpha": "team_alpha", "beta": "team_beta"}
    canonical = alias.get(value, value)
    if canonical not in WEEK_TEAMS:
        print(f"Unknown team '{team}'. Use one of: alpha, beta, both (or team_alpha/team_beta).")
        raise SystemExit(1)
    return [canonical]


def _gather_team_positions(team: str, settings: Settings) -> list:
    """Refreshed, read-only Alpaca positions for a team. Degrades to [] safely."""

    client = _safe_read_client(team, settings)
    if client is None or not client.has_credentials():
        return []
    try:
        return list(client.get_positions())
    except Exception as exc:  # noqa: BLE001 - read-only; degrade rather than crash
        print(f"({team} positions unavailable: {exc})")
        return []


def run_review_team_portfolio(team: str = "both") -> None:
    """Read-only position review + portfolio-health report (Phase 7V).

    Refreshes the team's Alpaca account + positions, reviews every long holding
    (P&L, weight, thesis status, recommended hold/trim/exit/watch), flags critical
    portfolio problems, states whether new buys should be blocked, and saves a
    Markdown+JSON report under the ignored runtime path. NEVER calls submit_order.
    """

    from src.competition.position_review import build_team_portfolio_review
    from src.config.portfolio_limits import PortfolioLimits
    from src.reporting.portfolio_review_report import format_review_terminal, save_review

    settings = Settings.from_env()
    limits = PortfolioLimits.from_env()
    teams = _normalize_team_arg(team)

    print("=== review-team-portfolio (Phase 7V; paper-only; READ-ONLY; never submits an order) ===")
    print(
        f"Permissions: long_entry={limits.enable_paper_long_entry} "
        f"sell_to_close={limits.enable_paper_sell_to_close} "
        f"(sell-to-close reduces/closes existing long stock only; no shorting/options/margin/live)."
    )
    print("")

    for tid in teams:
        # Read-only account snapshot (no order-count broker call, no submission).
        account = _account_context_for_source(tid, settings, reconcile_orders=False)
        raw_positions = _gather_team_positions(tid, settings)
        attribution_entries = load_team_attribution(tid)
        review = build_team_portfolio_review(
            tid,
            equity=account.equity,
            cash=account.cash,
            buying_power=account.buying_power,
            raw_positions=raw_positions,
            attribution_entries=attribution_entries,
            limits=limits,
        )
        print(format_review_terminal(review))
        try:
            saved = save_review(review)
            print(f"(saved: {saved['markdown']} / {saved['json']})")
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            print(f"(could not save review for {tid}: {exc})")
        print("")


def _team_portfolio_review_for(team: str, settings: Settings):
    """Build a read-only portfolio review for a team (shared by review/EOD)."""

    from src.competition.position_review import build_team_portfolio_review
    from src.config.portfolio_limits import PortfolioLimits

    account = _account_context_for_source(team, settings, reconcile_orders=False)
    raw_positions = _gather_team_positions(team, settings)
    attribution_entries = load_team_attribution(team)
    return build_team_portfolio_review(
        team,
        equity=account.equity, cash=account.cash, buying_power=account.buying_power,
        raw_positions=raw_positions, attribution_entries=attribution_entries,
        limits=PortfolioLimits.from_env(),
    )


def _todays_submitted_orders(team: str):
    """Summarize today's (ET) submitted paper orders from local attribution."""

    from src.competition.eod_report import OrderLine

    entries = load_team_attribution(team)
    today = ny_trading_date().isoformat()
    lines: list = []
    summaries: list[dict] = []
    for e in entries:
        ts = getattr(e, "timestamp", "") or ""
        if not ts.startswith(today) or not getattr(e, "broker_submitted", False):
            continue
        asset_type = str(getattr(e, "asset_type", "") or "")
        side = "sell" if "short" in asset_type else "buy"
        qty = getattr(e, "quantity", None) or 0
        price = getattr(e, "entry_price", None)
        notional = (qty * price) if (qty and price) else None
        lines.append(OrderLine(
            symbol=str(getattr(e, "symbol", "")), side=side, quantity=float(qty),
            price=price, notional=notional, status="submitted",
            reason=(getattr(e, "thesis", "") or "")[:160],
        ))
        summaries.append({"symbol": getattr(e, "symbol", ""), "side": side, "quantity": qty})
    return lines, summaries


def _build_team_eod(tid: str, settings: Settings, market_open: bool | None):
    """Build + save the EOD report and daily learning for one team. Read-only."""

    from src.competition.eod_report import build_eod_report, render_eod_discord, save_eod_report
    from src.competition.daily_learning import build_daily_learning, save_daily_learning

    review = _team_portfolio_review_for(tid, settings)
    order_lines, order_summaries = _todays_submitted_orders(tid)
    ledger = TeamLearningLedger.load(tid)
    report = build_eod_report(
        review,
        starting_equity=None,
        spy_daily_return_pct=None,
        submitted_orders=order_lines,
        rejected_or_skipped=[p.reason for p in review.positions if p.recommended_action == "exit"],
        learnings=ledger.latest_lessons(5),
        thesis_changes=[
            f"{p.symbol}: thesis {p.thesis_status}"
            for p in review.positions if p.thesis_status in ("weakening", "invalidated")
        ],
        next_day_watchlist=list(ledger.watchlist or []),
        market_is_open=market_open,
    )
    learning = build_daily_learning(review, submitted_orders=order_summaries)
    saved = None
    try:
        saved = save_eod_report(report)
        save_daily_learning(learning)
    except Exception as exc:  # noqa: BLE001 - persistence best-effort
        print(f"(could not save EOD for {tid}: {exc})")
    return report, render_eod_discord(report), saved


def _auto_send_eod_for_team(
    tid: str, settings: Settings, *, clock: dict | None, calendar_day: dict | None,
    force: bool = False, dry_run: bool = False,
) -> dict:
    """Eligibility-gated EOD build + deliver with durable retry-safe state. No orders.

    Persists a delivery record BEFORE and AFTER sending so a restart cannot
    duplicate a successful send, and a failed Discord delivery is retried later.
    """

    from src.competition.eod_report import mark_sent
    from src.competition.eod_delivery import DeliveryRecord, eod_send_eligible, get_record, upsert_record
    from src.competition.market_time import ny_trading_date

    market_open = clock.get("is_open") if clock else None
    trading_date = ny_trading_date().isoformat()
    eligible, reason = eod_send_eligible(
        tid, clock_is_open=market_open, calendar_day=calendar_day, force=force,
    )
    if not eligible:
        return {"team": tid, "trading_date": trading_date, "sent": False, "reason": reason}

    report, message, _saved = _build_team_eod(tid, settings, market_open)
    record = get_record(tid, trading_date)
    record.generated = True
    record.attempts += 1
    record.last_attempt_at = now_utc().isoformat()
    upsert_record(record)  # persist BEFORE send so a crash mid-send is recoverable

    if dry_run:
        return {"team": tid, "trading_date": trading_date, "sent": False,
                "reason": "dry-run: would send", "generated": True}

    sent, channel = _send_eod_to_discord(tid, message)
    if sent:
        record.delivered = True
        record.destination = channel
        record.error = None
        upsert_record(record)
        mark_sent(tid, trading_date)
        return {"team": tid, "trading_date": trading_date, "sent": True, "destination": channel}
    # Delivery failed: keep the saved report, record the error; retry next iteration
    # unless it's a terminal config error (Discord not configured).
    record.delivered = False
    record.destination = channel
    record.error = "discord_not_configured" if channel in ("disabled", "no_channel") else f"send_failed:{channel}"
    upsert_record(record)
    return {"team": tid, "trading_date": trading_date, "sent": False,
            "reason": record.error, "retry_pending": record.retry_pending}


def _maybe_auto_send_eod(teams: list[str], settings: Settings, *, market_open: bool | None,
                         dry_run_loop: bool) -> None:
    """Called each iteration: when the market is closed, deliver any due EOD reports."""

    if market_open is not False:  # only attempt when the market is known CLOSED
        return
    if os.getenv("AUTO_EOD_REPORT", "true").strip().lower() in {"0", "false", "no", "off"}:
        return
    calendar_day = None
    try:
        client = _safe_read_client(teams[0], settings)
        if client is not None and client.has_credentials():
            calendar_day = client.get_calendar_day(ny_trading_date())
    except Exception as exc:  # noqa: BLE001 - calendar best-effort; eligibility handles None
        print(f"(EOD calendar lookup unavailable: {exc})")
    clock = {"is_open": market_open}
    for tid in teams:
        try:
            res = _auto_send_eod_for_team(tid, settings, clock=clock, calendar_day=calendar_day,
                                          dry_run=dry_run_loop)
            if res.get("sent"):
                print(f"[eod] {tid}: delivered to #{res.get('destination')} for {res.get('trading_date')}")
            elif res.get("generated") or res.get("retry_pending"):
                print(f"[eod] {tid}: {res.get('reason')}")
        except Exception as exc:  # noqa: BLE001 - EOD must never crash the loop
            print(f"[eod] {tid}: auto-EOD error: {exc}")


def run_export_eod_report(team: str = "both", *, force: bool = False, send: bool = False) -> None:
    """Build + save the end-of-day report and daily learning artifact (manual).

    Read-only and deterministic: never submits orders, never calls an LLM. Sends to
    Discord only with --send (opt-in), once per team per ET trading date, after the
    regular session closes (or with --force to preview while closed/unknown).
    """

    settings = Settings.from_env()
    teams = _normalize_team_arg(team)
    clock = _cheap_loop_clock_snapshot(settings)
    market_open = clock.get("is_open") if clock else None
    calendar_day = None
    try:
        client = _safe_read_client(teams[0], settings)
        if client is not None and client.has_credentials():
            calendar_day = client.get_calendar_day(ny_trading_date())
    except Exception:  # noqa: BLE001
        calendar_day = None

    print("=== export-eod-report (paper-only; READ-ONLY; never submits an order) ===")
    for tid in teams:
        report, message, saved = _build_team_eod(tid, settings, market_open)
        print("")
        print(message)
        if saved:
            print(f"(saved EOD: {saved['markdown']} / {saved['json']})")
        if send:
            res = _auto_send_eod_for_team(tid, settings, clock=clock, calendar_day=calendar_day, force=force)
            if res.get("sent"):
                print(f"(posted EOD for {tid} to Discord #{res['destination']})")
            else:
                print(f"(not sent: {res.get('reason')})")


def run_eod_report_status(team: str = "both") -> None:
    """Read-only EOD delivery status for the current ET trading date. No secrets."""

    from src.competition.eod_delivery import get_record
    from src.competition.market_time import ny_trading_date

    teams = _normalize_team_arg(team)
    trading_date = ny_trading_date().isoformat()
    settings = Settings.from_env()
    market_open = _cheap_loop_market_open()
    state = "open" if market_open is True else ("closed" if market_open is False else "unknown")
    print("=== eod-report-status (Phase 7X; paper-only; READ-ONLY; no secrets) ===")
    print(f"Current ET trading date: {trading_date} | market: {state}")
    for tid in teams:
        rec = get_record(tid, trading_date)
        print(f"\n-- {tid} --")
        print(f"  generated:   {rec.generated}")
        print(f"  delivered:   {rec.delivered}")
        print(f"  destination: {rec.destination or 'n/a'}")
        print(f"  attempts:    {rec.attempts} (last {rec.last_attempt_at or 'never'})")
        print(f"  error:       {rec.error or 'none'}")
        print(f"  retry_pending: {rec.retry_pending}")


def _send_eod_to_discord(team_id: str, message: str) -> tuple[bool, str]:
    """Best-effort EOD post. Prefers the paper-trading log channel, falls back to
    the team channel. Returns (sent, channel_label). Never crashes."""

    try:
        from src.discord_bot.competition_updates import _http_send
        config = _discord_iteration_update_config()
        if config is None or not getattr(config, "enabled", False):
            return False, "disabled"
        token = getattr(config, "token", None)
        special = getattr(config, "special_channel_ids", None) or {}
        teams = getattr(config, "team_channel_ids", None) or {}
        # Preferred: the configured paper-trading log channel; else the team channel.
        channel_id = special.get("paper_trading_log")
        label = "paper_trading_log"
        if not channel_id:
            channel_id = teams.get(team_id)
            label = f"{team_id}_channel"
        if not channel_id or not token:
            return False, "no_channel"
        _http_send(channel_id, message, token)
        return True, label
    except Exception as exc:  # noqa: BLE001 - Discord must never crash the EOD path
        print(f"(EOD Discord post unavailable for {team_id}: {exc})")
        return False, "error"


# ---------------------------------------------------------------------------
# Phase 7W: bounded memory, maintenance, weekly synthesis, loop health/watchdog
# ---------------------------------------------------------------------------
def run_memory_status(team: str = "both") -> None:
    """Read-only inventory of per-team runtime memory. No secrets, no mutation."""

    from src.competition.memory_config import MemoryConfig
    from src.competition.memory_maintenance import inventory

    config = MemoryConfig.from_env()
    teams = _normalize_team_arg(team)
    print("=== memory-status (Phase 7W; paper-only; READ-ONLY; no secrets) ===")
    print(f"Retention (days): daily_summary={config.daily_summary_retention_days} "
          f"raw_audit={config.raw_audit_retention_days} agent_response={config.agent_response_retention_days} "
          f"proposal={config.proposal_retention_days} | weekly_archives={config.keep_weekly_archives}")
    print(f"Prompt caps: daily_summaries={config.max_daily_summaries_in_prompt} "
          f"lessons={config.max_lessons_in_prompt} | playbook cap/team={config.max_playbook_lessons_per_team}")
    for tid in teams:
        inv = inventory(tid, config)
        print(f"\n================ {tid} ================")
        for c in inv.categories:
            size_kb = c.total_bytes / 1024.0
            print(f"  [{c.category}] {c.path}")
            print(f"     files={c.file_count} size={size_kb:,.1f}KB oldest={c.oldest} newest={c.newest} "
                  f"retention={c.retention_days}d eligible_for_cleanup={c.eligible_for_cleanup}")
            if c.malformed:
                print(f"     MALFORMED ({len(c.malformed)}): {', '.join(Path(m).name for m in c.malformed)}")
        print(f"  Playbook: total={inv.playbook_total} active={inv.playbook_active} retired={inv.playbook_retired}")
        print(f"  Scorecard available: {inv.scorecard_available}")
        print(f"  Next cleanup: {inv.next_cleanup_note}")


def run_memory_maintenance(team: str = "both", *, apply: bool = False) -> None:
    """Archive+delete eligible old runtime memory (dry-run unless --apply)."""

    from src.competition.memory_config import MemoryConfig
    from src.competition.memory_maintenance import run_maintenance

    config = MemoryConfig.from_env()
    teams = _normalize_team_arg(team)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== memory-maintenance [{mode}] (Phase 7W; paper-only) ===")
    if not apply:
        print("Dry-run: no files are archived or deleted. Re-run with --apply to act.")
    for tid in teams:
        report = run_maintenance(tid, config, apply=apply)
        t = report.as_dict()["totals"]
        print(f"\n-- {tid}: archived={t['archived']} deleted={t['deleted']} skipped={t['skipped']} --")
        for a in report.actions[:25]:
            print(f"   [{a.category}] {a.action}: {Path(a.path).name} ({a.reason})")
        if len(report.actions) > 25:
            print(f"   ... and {len(report.actions) - 25} more (see saved report)")
    print("\nGuards: never deletes today's data, current/latest summary, current position-thesis records, "
          "or durable playbook lessons; never touches .env/source/DB/Git/user notes.")


def _recent_daily_learnings(team_id: str, max_n: int):
    """Load the newest ``max_n`` daily-learning artifacts for a team (compact)."""

    from src.competition.daily_learning import DEFAULT_LEARNING_DIR

    directory = DEFAULT_LEARNING_DIR
    if not directory.exists():
        return []
    files = sorted(directory.glob(f"{team_id}_*.json"), key=lambda p: p.name, reverse=True)[:max_n]
    out = []
    for path in files:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - skip corrupt artifact
            continue
    return out


def _build_and_save_weekly(tid: str, settings: Settings):
    """Build the weekly review, apply deterministic playbook updates, and save. No orders."""

    from src.competition.memory_config import MemoryConfig
    from src.competition.memory_retrieval import load_recent_daily_summaries
    from src.competition.playbook import TeamPlaybook
    from src.competition.weekly_synthesis import (
        build_weekly_review, render_weekly_discord, render_weekly_markdown, save_weekly_review,
    )

    config = MemoryConfig.from_env()
    review = _team_portfolio_review_for(tid, settings)
    playbook = TeamPlaybook.load(tid)
    weekly = build_weekly_review(
        tid, review=review,
        recent_daily=load_recent_daily_summaries(tid, max_n=7),
        recent_learnings=_recent_daily_learnings(tid, 7),
        playbook=playbook, config=config, attribution_entries=load_team_attribution(tid),
    )
    playbook.save()  # promotions/supersessions/cap applied deterministically
    saved = save_weekly_review(weekly)
    return weekly, render_weekly_markdown(weekly), render_weekly_discord(weekly), saved


def _auto_run_weekly_for_team(
    tid: str, settings: Settings, *, clock: dict | None, calendar_day: dict | None,
    force: bool = False, dry_run: bool = False,
) -> dict:
    """Eligibility-gated weekly synthesis with retry-safe delivery state. Never trades."""

    from src.competition.weekly_delivery import (
        WeeklyRecord, get_weekly_record, upsert_weekly_record, weekly_run_eligible,
    )
    from src.competition.weekly_synthesis import iso_week_tag

    week_tag = iso_week_tag()
    market_open = clock.get("is_open") if clock else None
    next_open = clock.get("next_open") if clock else None
    eligible, reason = weekly_run_eligible(
        tid, clock_is_open=market_open, calendar_day=calendar_day,
        next_open_iso=next_open, force=force,
    )
    if not eligible:
        return {"team": tid, "week": week_tag, "ran": False, "reason": reason}
    if dry_run:
        return {"team": tid, "week": week_tag, "ran": False, "reason": "dry-run: would run weekly"}

    _weekly, _md, discord_msg, _saved = _build_and_save_weekly(tid, settings)
    record = get_weekly_record(tid, week_tag)
    record.generated = True
    record.attempts += 1
    record.last_attempt_at = now_utc().isoformat()
    upsert_weekly_record(record)

    sent, channel = _send_eod_to_discord(tid, discord_msg)
    record.delivered = bool(sent)
    record.destination = channel
    record.error = None if sent else (
        "discord_not_configured" if channel in ("disabled", "no_channel") else f"send_failed:{channel}"
    )
    upsert_weekly_record(record)
    return {"team": tid, "week": week_tag, "ran": True, "delivered": sent, "destination": channel}


def _maybe_run_weekly(teams: list[str], settings: Settings, *, market_open: bool | None,
                      dry_run_loop: bool) -> None:
    """Called each iteration: after the last regular session of the week, run weekly synthesis."""

    if market_open is not False:
        return
    if os.getenv("AUTO_WEEKLY_REVIEW", "true").strip().lower() in {"0", "false", "no", "off"}:
        return
    clock = _cheap_loop_clock_snapshot(settings)
    calendar_day = None
    try:
        client = _safe_read_client(teams[0], settings)
        if client is not None and client.has_credentials():
            calendar_day = client.get_calendar_day(ny_trading_date())
    except Exception:  # noqa: BLE001
        calendar_day = None
    for tid in teams:
        try:
            res = _auto_run_weekly_for_team(tid, settings, clock=clock, calendar_day=calendar_day,
                                            dry_run=dry_run_loop)
            if res.get("ran"):
                print(f"[weekly] {tid}: ran for {res['week']} (delivered={res.get('delivered')} #{res.get('destination')})")
        except Exception as exc:  # noqa: BLE001 - weekly must never crash the loop
            print(f"[weekly] {tid}: auto-weekly error: {exc}")


def run_weekly_team_review(team: str = "both", *, send: bool = False) -> None:
    """Non-trading weekly synthesis: summarize the week + update the durable playbook
    through deterministic evidence gates. Never trades or changes settings."""

    settings = Settings.from_env()
    teams = _normalize_team_arg(team)
    print("=== weekly-team-review (paper-only; READ-ONLY; submits NO orders) ===")
    for tid in teams:
        weekly, md, discord_msg, saved = _build_and_save_weekly(tid, settings)
        print("")
        print(md)
        print(f"\n(saved: {saved['markdown']} / {saved['json']}; playbook now {weekly.playbook_active_after} active lessons)")
        if send:
            sent, channel = _send_eod_to_discord(tid, discord_msg)
            print(f"(weekly Discord post: {'sent #' + channel if sent else 'not sent [' + channel + ']'})")


def run_weekly_review_status(team: str = "both") -> None:
    """Read-only weekly review delivery status for the current ISO week. No secrets."""

    from src.competition.weekly_delivery import get_weekly_record
    from src.competition.weekly_synthesis import iso_week_tag

    teams = _normalize_team_arg(team)
    week_tag = iso_week_tag()
    print("=== weekly-review-status (Phase 7X; paper-only; READ-ONLY; no secrets) ===")
    print(f"Current ISO week: {week_tag}")
    for tid in teams:
        rec = get_weekly_record(tid, week_tag)
        print(f"\n-- {tid} --")
        print(f"  generated:   {rec.generated}")
        print(f"  delivered:   {rec.delivered}")
        print(f"  destination: {rec.destination or 'n/a'}")
        print(f"  attempts:    {rec.attempts} (last {rec.last_attempt_at or 'never'})")
        print(f"  error:       {rec.error or 'none'}")
        print(f"  retry_pending: {rec.retry_pending}")


def _gather_loop_health(stale_threshold_seconds: int):
    """Assemble LoopHealth from the tracked PID + heartbeat + per-team audit."""

    from src.competition.loop_heartbeat import heartbeat_age_seconds, read_heartbeat
    from src.competition.loop_watchdog import TeamLoopStatus, assess_loop_health
    from src.competition.iteration_audit import latest_status_age_seconds, load_latest_status

    pid, alive = _tracked_loop_pid()
    heartbeat = read_heartbeat()
    age = heartbeat_age_seconds(heartbeat)
    per_team = []
    for tid in WEEK_TEAMS:
        iso, t_age = latest_status_age_seconds(tid)
        status = load_latest_status(tid) or {}
        per_team.append(TeamLoopStatus(
            team_id=tid, last_iteration_at=iso, last_iteration_age_seconds=t_age,
            last_cycle_action=status.get("cycle_action"),
            last_exception=status.get("exception_text"),
        ))
    return assess_loop_health(
        pid=pid, process_alive=alive, heartbeat=heartbeat,
        heartbeat_age_seconds=age, per_team=per_team,
        stale_threshold_seconds=stale_threshold_seconds,
    )


def _tracked_loop_pid():
    """(pid, alive) for the tracked cheap loop. Degrades to (None, False)."""

    try:
        from src.ui.operator_controls import cheap_loop_pid_path
        from src.ui.process_control import is_process_running, read_pid

        pid = read_pid(cheap_loop_pid_path())
        return pid, (is_process_running(pid) if pid is not None else False)
    except Exception:  # noqa: BLE001
        return None, False


def run_loop_health(stale_threshold_seconds: int = 1800) -> None:
    """Read-only loop liveness report (PID + heartbeat + per-team status)."""

    health = _gather_loop_health(stale_threshold_seconds)
    print("=== loop-health (Phase 7W; paper-only; READ-ONLY) ===")
    print(f"PID: {health.pid} | process_alive: {health.process_alive}")
    print(f"Last heartbeat: {health.last_heartbeat_at} | age: "
          f"{'n/a' if health.heartbeat_age_seconds is None else f'{health.heartbeat_age_seconds:.0f}s'}")
    print(f"Market state (heartbeat): {health.market_state} | graceful_shutdown: {health.graceful_shutdown}")
    for t in health.teams:
        age = "n/a" if t.last_iteration_age_seconds is None else f"{t.last_iteration_age_seconds:.0f}s"
        print(f"  [{t.team_id}] last_iteration={t.last_iteration_at} ({age}) "
              f"action={t.last_cycle_action} last_exception={t.last_exception or 'none'}")
    print(f"Restart recommended: {health.restart_recommended} - {health.reason}")


def _watchdog_log(message: str) -> None:
    from src.competition.loop_heartbeat import heartbeat_path  # reuse runtime dir

    log_path = heartbeat_path().parent / "watchdog.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_utc().isoformat()} {message}\n")
    except Exception:  # noqa: BLE001 - logging must never crash the watchdog
        pass


def run_loop_watchdog(
    *, team: str = "both", sleep_seconds: int = 900, stale_threshold_seconds: int = 1800,
    once: bool = False, dry_run: bool = False,
) -> None:
    """Keep the competition loop alive: restart it when dead/stale. Never trades.

    Reuses the gated ``start_cheap_loop`` spawner (same project Python; refuses
    duplicates). Will not start while the kill switch is engaged or during a known
    graceful shutdown. With --dry-run it assesses + logs but never spawns.
    """

    import time as _time
    from src.competition.loop_watchdog import run_watchdog_once
    from src.ui.operator_controls import detect_cheap_loop_processes, start_cheap_loop

    print(f"=== loop-watchdog (Phase 7W; paper-only) team={team} sleep={sleep_seconds}s "
          f"stale_threshold={stale_threshold_seconds}s dry_run={dry_run} ===")
    while True:
        health = _gather_loop_health(stale_threshold_seconds)
        ks = read_kill_switch()
        duplicates = []
        try:
            duplicates = detect_cheap_loop_processes()
        except Exception:  # noqa: BLE001 - detection best-effort
            duplicates = []

        def _starter():
            if dry_run:
                from types import SimpleNamespace
                return SimpleNamespace(success=False, message="dry-run: restart suppressed")
            return start_cheap_loop(sleep_seconds=sleep_seconds, team=team,
                                    python_executable=sys.executable)

        result = run_watchdog_once(
            health=health, kill_switch_engaged=ks.engaged,
            detected_duplicates=duplicates, starter=_starter,
        )
        line = f"action={result.action} restarted={result.restarted} detail={result.detail}"
        print(line)
        _watchdog_log(line)
        if once:
            break
        _time.sleep(sleep_seconds)


def _write_iteration_audit(
    *,
    iteration: int,
    team_id: str,
    started_at: str,
    market_state: str,
    cycle_action: str,
    gate_decision,
    kill_switch_engaged: bool,
    exception_text: str | None,
    settings: Settings,
    portfolio_result: dict | None = None,
) -> None:
    """Append one durable per-iteration audit record (no secrets, broker-free).

    Counts/account come from the freshly-written post-cycle scorecard; bounded
    prompt-memory metadata and the portfolio-action state come from the runtime
    metadata file + the loop's portfolio step. Best-effort: an audit failure
    prints a notice but never crashes the loop, and an exception during the cycle
    is always recorded here so a failure can never pass silently.
    """

    try:
        from src.competition.iteration_audit import (
            IterationAuditRecord,
            append_iteration_record,
            new_iteration_id,
        )
        from src.competition.prompt_memory import load_prompt_memory_metadata

        scorecard = load_latest_scorecard(team_id)
        permissions = TradingPermissions.from_env()
        mem = load_prompt_memory_metadata(team_id) or {}
        pr = portfolio_result or {}
        record = IterationAuditRecord(
            iteration_id=new_iteration_id(iteration),
            iteration=iteration,
            team_id=team_id,
            started_at=started_at,
            finished_at=now_utc().isoformat(),
            market_state=market_state,
            cycle_action=cycle_action,
            kill_switch_engaged=kill_switch_engaged,
            gate_should_run_full_cycle=getattr(gate_decision, "should_run_full_cycle", None),
            gate_recommend_review_only=getattr(gate_decision, "recommend_review_only", None),
            gate_reason=getattr(gate_decision, "reason", "") or "",
            proposals_count=getattr(scorecard, "proposals_count", None) if scorecard else None,
            approved_count=getattr(scorecard, "approved_count", None) if scorecard else None,
            simulation_only_count=getattr(scorecard, "simulation_only_count", None) if scorecard else None,
            rejected_count=getattr(scorecard, "rejected_count", None) if scorecard else None,
            orders_submitted=getattr(scorecard, "orders_submitted", None) if scorecard else None,
            broker_rejected_count=getattr(scorecard, "broker_rejected_count", None) if scorecard else None,
            portfolio_decision_type=getattr(scorecard, "portfolio_decision_type", None) if scorecard else None,
            portfolio_no_trade=getattr(scorecard, "portfolio_no_trade", None) if scorecard else None,
            equity=getattr(scorecard, "current_equity", None) if scorecard else None,
            cash=getattr(scorecard, "cash", None) if scorecard else None,
            buying_power=getattr(scorecard, "buying_power", None) if scorecard else None,
            max_daily_orders_per_team=permissions.max_daily_orders_per_team,
            memory_daily_summaries_included=mem.get("daily_summaries_included"),
            memory_lesson_ids_included=mem.get("lesson_ids_included"),
            memory_scorecard_included=mem.get("scorecard_included"),
            memory_bounded_context_chars=mem.get("bounded_context_chars"),
            memory_malformed_sources=mem.get("malformed_or_unavailable"),
            portfolio_action_recommended=pr.get("recommended"),
            portfolio_action_eligible=pr.get("eligible"),
            portfolio_action_submitted=pr.get("submitted"),
            portfolio_action_rejected_reason=pr.get("rejected_reason"),
            new_buys_blocked_reason=pr.get("new_buys_blocked_reason"),
            exception_text=exception_text,
        )
        append_iteration_record(record)
    except Exception as exc:  # noqa: BLE001 - audit must never crash the loop
        print(f"(iteration audit unavailable for {team_id}: {exc}; continuing loop)")


def _run_portfolio_management(
    tid: str, settings: Settings, *, dry_run_loop: bool, kill_switch_engaged: bool,
) -> dict:
    """Deterministic portfolio review + safe long-only sell-to-close BEFORE new buys.

    Reviews refreshed positions, produces hold/trim/exit/watch recommendations, and
    executes only eligible long trims/exits (capped to refreshed held qty; never
    shorts) when ``ENABLE_PAPER_SELL_TO_CLOSE`` is on. Returns the portfolio-action
    audit fields and whether new buys must be blocked. Never submits a new buy.
    """

    from src.competition.position_review import ACTION_EXIT, ACTION_TRIM, build_team_portfolio_review
    from src.competition.position_execution import PositionActionProposal, execute_sell_to_close
    from src.config.portfolio_limits import PortfolioLimits

    result = {
        "recommended": "none", "eligible": False, "submitted": 0,
        "rejected_reason": None, "new_buys_blocked": False, "new_buys_blocked_reason": None,
        "proceed_new_buys": True,
    }
    limits = PortfolioLimits.from_env()
    account = _account_context_for_source(tid, settings, reconcile_orders=False)
    raw_positions = _gather_team_positions(tid, settings)
    attribution = load_team_attribution(tid)
    review = build_team_portfolio_review(
        tid, equity=account.equity, cash=account.cash, buying_power=account.buying_power,
        raw_positions=raw_positions, attribution_entries=attribution, limits=limits,
    )

    blocked = review.health.block_new_buys
    result["new_buys_blocked"] = blocked
    result["proceed_new_buys"] = not blocked
    if blocked:
        result["new_buys_blocked_reason"] = review.health.block_new_buys_reason

    recs = [
        (p.symbol, p.recommended_action, p.reason)
        for p in review.positions if p.recommended_action in (ACTION_TRIM, ACTION_EXIT)
    ]
    result["recommended"] = ";".join(f"{a}:{s}" for s, a, _ in recs) or "none"
    if not recs:
        return result

    proposals = [PositionActionProposal(symbol=s, action=a, reason=r) for s, a, r in recs]
    submit_dry = settings.dry_run or dry_run_loop or kill_switch_engaged
    client = None
    if not submit_dry and limits.enable_paper_sell_to_close:
        try:
            client = client_for_source(tid, base_settings=settings, options_adapter=_options_adapter_from_env())
        except Exception as exc:  # noqa: BLE001 - missing creds -> validate-only, never submit
            print(f"({tid} sell-to-close broker unavailable: {exc}; recommendations only)")
            client = None

    # Seed the daily-notional gate with today's reconciled usage so sell-to-close
    # submissions count toward MAX_DAILY_NOTIONAL_PER_TEAM (same policy as entries).
    notional_used = _daily_notional_for_source(tid, settings).used if not submit_dry else 0.0
    records = execute_sell_to_close(
        proposals, client=client, dry_run=submit_dry, limits=limits,
        refresh_positions=lambda: _gather_team_positions(tid, settings),
        daily_notional_used=notional_used,
    )
    result["submitted"] = sum(1 for r in records if r.submitted)
    result["eligible"] = any(not r.detail.startswith("Rejected by deterministic risk") for r in records)
    rejects = [r for r in records if r.detail.startswith("Rejected by deterministic risk") or r.broker_rejected]
    if rejects:
        result["rejected_reason"] = rejects[0].broker_reject_reason or rejects[0].detail
    for r in records:
        print(f"[{tid}] sell-to-close {r.action} {r.symbol}: submitted={r.submitted} {r.detail}")
    return result


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

    from src.competition.loop_heartbeat import mark_graceful_shutdown, write_heartbeat

    sleep_fn = sleep_fn or _time.sleep
    teams = list(WEEK_TEAMS) if team == "both" else [team]
    iteration = 0
    _loop_pid = os.getpid()
    _loop_started_at = now_utc().isoformat()
    review_only_during_market_hours = os.getenv(
        "REVIEW_ONLY_DURING_MARKET_HOURS", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    discord_update_config = _discord_iteration_update_config()
    # Phase 7T: strict off-hours quiet mode (quiet by default when closed).
    quiet_config = OffHoursQuietConfig.from_env()
    off_hours_notice_shown = False

    try:
      while True:
        iteration += 1
        print(f"=== Cheap competition loop iteration {iteration} (paper-only; dry_run_loop={dry_run_loop}) ===")
        ks = read_kill_switch()
        if ks.engaged:
            print(ks.describe())

        market_open = _cheap_loop_market_open() if market_hours_only else None
        market_closed = market_hours_only and market_open is False
        # Phase 7W: heartbeat every iteration so liveness needs PID *and* freshness.
        _hb_state = "open" if market_open is True else ("closed" if market_open is False else "unknown")
        write_heartbeat(pid=_loop_pid, iteration=iteration, market_state=_hb_state,
                        started_at=_loop_started_at)

        # Phase 7X: automatic once-per-team/trading-date EOD delivery after the
        # regular close. Runs even under strict off-hours quiet mode (it is an
        # explicitly bounded post-close action), is Alpaca clock/calendar gated
        # (no weekends/holidays/pre-open), retry-safe, and never submits orders.
        try:
            _maybe_auto_send_eod(teams, Settings.from_env(), market_open=market_open,
                                 dry_run_loop=dry_run_loop)
        except Exception as exc:  # noqa: BLE001 - EOD must never crash the loop
            print(f"(auto-EOD step error: {exc}; continuing loop)")
        # Phase 7X: once-per-week synthesis after the last regular session of the week.
        try:
            _maybe_run_weekly(teams, Settings.from_env(), market_open=market_open,
                              dry_run_loop=dry_run_loop)
        except Exception as exc:  # noqa: BLE001 - weekly must never crash the loop
            print(f"(auto-weekly step error: {exc}; continuing loop)")

        strict_quiet = quiet_config.quiet_when_closed(market_open)
        if market_open is not False:
            # Reset the once-per-stretch notice whenever the market is not closed.
            off_hours_notice_shown = False

        if strict_quiet:
            # Phase 7T: stay alive but quiet — only explicitly allowed off-hours
            # actions run; everything else is skipped. The loop never dies here.
            off_hours_notice_shown = _run_quiet_off_hours_iteration(
                teams=teams,
                iteration=iteration,
                quiet_config=quiet_config,
                discord_update_config=discord_update_config,
                kill_switch_engaged=ks.engaged,
                run_review_only_when_skipped=run_review_only_when_skipped,
                llm_review_when_skipped=llm_review_when_skipped,
                llm_daily_review_at_close=llm_daily_review_at_close,
                team=team,
                dry_run_loop=dry_run_loop,
                once=once,
                sleep_seconds=sleep_seconds,
                notice_shown=off_hours_notice_shown,
            )
            if once:
                break
            print(f"Sleeping {sleep_seconds}s before next cheap iteration...")
            sleep_fn(sleep_seconds)
            continue

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
            iteration_started_at = now_utc().isoformat()
            cycle_action = "cheap_skip"
            exception_text: str | None = None
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

            # Phase 7U: the cycle path is wrapped so a transient failure (e.g. the
            # LLM provider raising / SystemExit) can NEVER silently kill the loop
            # or pass without a visible audit record + console line.
            portfolio_result: dict | None = None
            try:
                if allow_full:
                    if dry_run_loop:
                        cycle_action = "full_cycle"
                        print(f"[dry-run] [{tid}] would run: portfolio review + safe sell-to-close (reductions before new buys)")
                        print(f"[dry-run] [{tid}] would run: run-week-cycle --proposal-source llm")
                    else:
                        # Phase 7X: (a) refresh + health, (b/c/d) review existing positions,
                        # (e/f/g) execute eligible long trims/exits BEFORE any new buys.
                        portfolio_result = _run_portfolio_management(
                            tid, Settings.from_env(), dry_run_loop=dry_run_loop,
                            kill_switch_engaged=ks.engaged,
                        )
                        if portfolio_result.get("new_buys_blocked"):
                            # No new stock-long buy when deterministic health requires reduction;
                            # still run review/learning (no new orders).
                            cycle_action = "managed_review_only"
                            print(f"[{tid}] new buys BLOCKED: {portfolio_result.get('new_buys_blocked_reason')}")
                            run_week_cycle_cli(team=tid, proposal_source="llm", review_only=True)
                        else:
                            cycle_action = "full_cycle"
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
            except SystemExit as exc:
                exception_text = f"SystemExit({exc.code}) during {cycle_action}"
                print(f"!! [{tid}] cycle aborted: {exception_text}; continuing loop (logged to audit).")
                cycle_action = "error"
            except Exception as exc:  # noqa: BLE001 - a cycle failure must not kill the loop
                exception_text = f"{type(exc).__name__}: {exc}"
                print(f"!! [{tid}] cycle FAILED: {exception_text}; continuing loop (logged to audit).")
                cycle_action = "error"

            # Phase 7U: durable per-iteration audit record (always written; no secrets).
            _write_iteration_audit(
                iteration=iteration,
                team_id=tid,
                started_at=iteration_started_at,
                market_state=market_state,
                cycle_action=cycle_action,
                gate_decision=decision,
                kill_switch_engaged=ks.engaged,
                exception_text=exception_text,
                settings=Settings.from_env(),
                portfolio_result=portfolio_result,
            )

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
      # Phase 7W: normal loop exit (e.g. --once) is an intentional shutdown.
      mark_graceful_shutdown()
    except KeyboardInterrupt:
        print("Cheap loop interrupted by operator; marking graceful shutdown (watchdog will not restart).")
        mark_graceful_shutdown()
        raise
    # Any other exception propagates WITHOUT a graceful marker, so the watchdog
    # treats it as a crash and may restart the loop.


def _run_quiet_off_hours_iteration(
    *,
    teams: list[str],
    iteration: int,
    quiet_config,
    discord_update_config,
    kill_switch_engaged: bool,
    run_review_only_when_skipped: bool,
    llm_review_when_skipped: bool,
    llm_daily_review_at_close: bool,
    team: str,
    dry_run_loop: bool,
    once: bool,
    sleep_seconds: int,
    notice_shown: bool,
) -> bool:
    """Run only the explicitly-allowed off-hours actions, then return the notice flag.

    Phase 7T strict quiet mode: when the market is closed and
    ``STRICT_MARKET_HOURS_ONLY=true``, the loop stays alive but quiet. Each
    ``ALLOW_OFF_HOURS_*`` flag re-enables exactly one action; everything else is
    skipped silently to avoid console spam. Never submits orders; never bypasses
    risk or the kill switch; Discord failures never crash the loop.
    """

    # One concise sleep notice per closed-market stretch (or every loop when the
    # operator has disabled the single-notice behavior).
    if quiet_config.post_one_sleep_notice:
        if not notice_shown:
            print(OFF_HOURS_SLEEP_NOTICE)
            notice_shown = True
    else:
        print(OFF_HOURS_SLEEP_NOTICE)

    # Attribution refresh — off by default when closed.
    if quiet_config.allow_off_hours_attribution_refresh:
        if dry_run_loop:
            print("[dry-run] (off-hours allowed) would run: refresh-proposal-attribution")
        else:
            try:
                run_refresh_proposal_attribution()
            except SystemExit as exc:
                print(f"(refresh-proposal-attribution unavailable: exit {exc.code}; continuing loop)")
            except Exception as exc:  # noqa: BLE001 - never let a cheap step kill the loop
                print(f"(refresh-proposal-attribution error: {exc}; continuing loop)")

    # Week status (which also performs the live-equity refresh) — off by default.
    if quiet_config.allow_off_hours_status_refresh:
        if dry_run_loop:
            print("[dry-run] (off-hours allowed) would run: week-competition-status")
        else:
            run_week_competition_status()

    # LLM review-only / advisory daily review — off by default when closed.
    if quiet_config.allow_off_hours_llm_review and (
        run_review_only_when_skipped or llm_review_when_skipped
    ):
        for tid in teams:
            decision, _gate_config = _evaluate_team_cheap_gate(tid)
            print(
                f"[{tid}] (off-hours allowed) gate: "
                f"should_run_full_cycle={decision.should_run_full_cycle} "
                f"recommend_review_only={decision.recommend_review_only} reason={decision.reason}"
            )
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

    if llm_daily_review_at_close and quiet_config.allow_off_hours_llm_review:
        if dry_run_loop:
            print("[dry-run] (off-hours allowed) would run: run-llm-daily-review (market closed; no orders)")
        else:
            try:
                run_llm_daily_review(team=None if team == "both" else team)
            except Exception as exc:  # noqa: BLE001 - advisory close step must not kill the loop
                print(f"(llm-daily-review-at-close unavailable: {exc}; continuing loop)")

    # Discord posts (team briefs + scoreboard) — off by default when closed.
    if quiet_config.allow_off_hours_discord:
        for tid in teams:
            _post_discord_iteration_update(
                config=discord_update_config,
                team_id=tid,
                iteration=iteration,
                cycle_action="market_closed",
                gate_decision=None,
                market_state="closed",
                kill_switch_engaged=kill_switch_engaged,
                llm_review_when_skipped=llm_review_when_skipped,
                dry_run=dry_run_loop,
            )
        if len(teams) >= 2:
            _post_discord_competition_summary(
                config=discord_update_config,
                iteration=iteration,
                kill_switch_engaged=kill_switch_engaged,
                next_wake_seconds=None if once else sleep_seconds,
                teams=tuple(teams),
                dry_run=dry_run_loop,
            )

    return notice_shown


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


def _competition_equity_view(teams: tuple[str, ...]):
    """Refresh current team paper-account equity for the scoreboard. Never raises.

    Uses each team's OWN Alpaca paper credentials (never the global key). Any
    failure (missing creds, 401, network) falls back to that team's cached
    weekly equity, labelled accordingly. Returns ``None`` only if the whole
    refresh helper is unavailable.
    """

    try:
        cards = {team_id: load_latest_scorecard(team_id) for team_id in teams}
        return refresh_competition_equity(tuple(teams), cards=cards)
    except Exception as exc:  # noqa: BLE001 - scoreboard refresh must never crash the loop
        print(f"(team paper equity refresh unavailable: {exc}; using cached weekly state)")
        return None


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

    Refreshes current team paper-account equity first (live snapshot when
    reachable, cached weekly state otherwise). Posts at most once per loop
    ``iteration`` (the summary's own de-dup guard skips a repeat in the same
    iteration), and skips entirely when the scoreboard is unchanged.
    """

    if config is None or not getattr(config, "enabled", False):
        return
    try:
        from src.discord_bot.competition_updates import post_competition_iteration_summary

        equity_view = _competition_equity_view(teams)
        post_competition_iteration_summary(
            config=config,
            iteration=iteration,
            kill_switch_engaged=kill_switch_engaged,
            next_wake_seconds=next_wake_seconds,
            teams=teams,
            equity_view=equity_view,
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
        equity_view = _competition_equity_view(tuple(teams))
        if equity_view is not None:
            print(f"[summary] equity source: {equity_view.source_label}")
        result = post_competition_iteration_summary(
            config=config,
            kill_switch_engaged=ks.engaged,
            teams=tuple(teams),
            equity_view=equity_view,
            dry_run=dry_run,
        )
        print(f"[summary] result: sent={result['sent']} reason={result['reason']}")

    print("Paper-only. LLMs do not execute trades. Orders require deterministic gates.")


def run_week_competition_status(*, client_factory=None, env=None) -> None:
    status = competition_status()
    print("=== Alpha vs Beta weekly competition status (paper-only) ===")
    print(f"Active: {status['active']}")
    print(f"Week start: {status['week_start']}")
    print(f"Week end: {status['week_end']}")
    teams = status["teams"]
    if not teams:
        print("No team scorecards yet. Run: python -m src.main run-week-cycle --team team_alpha")
        return

    # Phase 7S.3: refresh each team's CURRENT paper-account equity using its own
    # credentials (never the global key). Falls back per team to cached weekly
    # state, labelled as such, so a failure for one team can't blank the board.
    team_ids = tuple(card["team_id"] for card in teams)
    equity_view = refresh_competition_equity(
        team_ids,
        cards={card["team_id"]: card for card in teams},
        client_factory=client_factory,
        env=env,
    )
    print(f"source: {equity_view.source_label}")
    print(f"snapshot_time: {equity_view.snapshot_time}")

    # When both teams read live, recompute return/excess (and therefore the
    # leaderboard) from the live snapshots; otherwise keep the cached ranking.
    both_live = equity_view.all_live

    def _display(card: dict) -> dict:
        snap = equity_view.get(card["team_id"])
        spy = card.get("spy_benchmark_return")
        if snap is not None and snap.is_live and snap.team_return is not None:
            equity = snap.equity
            team_return = snap.team_return
            excess = snap.excess_return_vs_spy(spy)
            source = "live"
            error = None
        else:
            starting = card.get("starting_equity") or 0.0
            equity = card.get("current_equity")
            team_return = (
                (equity - starting) / starting if starting and equity is not None else 0.0
            )
            excess = card.get("excess_return_vs_spy")
            source = "cached"
            error = snap.error if snap is not None else None
        return {
            "equity": equity,
            "team_return": team_return,
            "excess": excess,
            "spy": spy,
            "source": source,
            "error": error,
        }

    rows = [(card, _display(card)) for card in teams]
    if both_live:
        rows.sort(
            key=lambda item: (
                item[1]["excess"] if item[1]["excess"] is not None else item[1]["team_return"]
            ),
            reverse=True,
        )

    for rank, (card, view) in enumerate(rows, start=1):
        display_rank = rank if both_live else card.get("current_rank")
        spy_text = "unknown" if view["spy"] is None else f"{view['spy']:.4f}"
        excess_text = "unknown" if view["excess"] is None else f"{view['excess']:.4f}"
        equity_text = "unknown" if view["equity"] is None else f"{view['equity']:.2f}"
        if view["source"] == "live":
            tag = " [live]"
        elif view["error"]:
            tag = f" [cached: {view['error']}]"
        else:
            tag = " [cached]"
        print(
            f"#{display_rank} {card['team_id']}: return={view['team_return']:.4f} "
            f"equity={equity_text} SPY={spy_text} excessVsSPY={excess_text} "
            f"orders={card['orders_submitted']} approved={card['approved_count']} "
            f"sim_only={card['simulation_only_count']} rejected={card['rejected_count']}{tag}"
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


def _configure_utf8_runtime_output() -> None:
    """Force stdout/stderr to UTF-8 so redirected CLI output can never crash.

    When the loop runs in the background its stdout/stderr are redirected to
    ``data/runtime/cheap_loop.log``. Off a console, CPython picks the locale code
    page (cp1252 on Windows), which cannot encode characters like ``≈`` and raises
    ``UnicodeEncodeError`` mid-report. Reconfiguring both streams to UTF-8 with a
    ``backslashreplace`` error policy keeps every Unicode symbol in reports while
    guaranteeing output can never crash the loop. Idempotent and defensive; runs
    before any other CLI output so all of it is protected.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - non-reconfigurable stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover - detached/closed stream
            pass


def main() -> None:
    _configure_utf8_runtime_output()
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
    tomorrow_plan_parser = subparsers.add_parser(
        "export-tomorrow-plan",
        help="Build + persist the Phase 7T Tomorrow Plan artifact(s) under data/reviews/ (paper-only; no orders)",
    )
    tomorrow_plan_parser.add_argument(
        "--team", choices=("team_alpha", "team_beta", "both"), default="both",
        help="Team(s) to plan for. Defaults to both.",
    )
    subparsers.add_parser(
        "market-hours-quiet-status",
        help="Show strict off-hours quiet-mode config + what the cheap loop skips when closed (no secrets)",
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
    diagnose_loop_parser = subparsers.add_parser(
        "diagnose-competition-loop",
        help="Read-only diagnosis of why the cheap competition loop is/ isn't trading (no LLM, no orders).",
    )
    diagnose_loop_parser.add_argument(
        "--team", choices=("team_alpha", "team_beta", "both"), default="both",
        help="Team(s) to diagnose. Defaults to both.",
    )
    review_portfolio_parser = subparsers.add_parser(
        "review-team-portfolio",
        help="Read-only position review + portfolio health for a team (no LLM submit; never places orders).",
    )
    review_portfolio_parser.add_argument(
        "--team", default="both",
        help="Team(s) to review: alpha, beta, both (team_alpha/team_beta also accepted).",
    )
    eod_parser = subparsers.add_parser(
        "export-eod-report",
        help="Build+save the end-of-day report and daily learning artifact (read-only; --send to post).",
    )
    eod_parser.add_argument("--team", default="both", help="Team(s): alpha, beta, both.")
    eod_parser.add_argument(
        "--force", action="store_true",
        help="Build even if the market clock is open/unknown (preview). Still respects the once-per-day guard for --send.",
    )
    eod_parser.add_argument(
        "--send", action="store_true",
        help="Post the concise report to the team's Discord channel (once per ET trading date). Default: off.",
    )
    mem_status_parser = subparsers.add_parser(
        "memory-status", help="Read-only inventory of per-team runtime memory (no secrets).",
    )
    mem_status_parser.add_argument("--team", default="both", help="Team(s): alpha, beta, both.")
    mem_maint_parser = subparsers.add_parser(
        "memory-maintenance", help="Archive+delete eligible old runtime memory (dry-run unless --apply).",
    )
    mem_maint_parser.add_argument("--team", default="both", help="Team(s): alpha, beta, both.")
    mem_maint_parser.add_argument("--apply", action="store_true", help="Actually archive/delete (default: dry-run).")
    mem_maint_parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default behavior).")
    weekly_parser = subparsers.add_parser(
        "weekly-team-review", help="Non-trading weekly synthesis + deterministic playbook update.",
    )
    weekly_parser.add_argument("--team", default="both", help="Team(s): alpha, beta, both.")
    weekly_parser.add_argument("--send", action="store_true", help="Post a short weekly summary to Discord.")
    eod_status_parser = subparsers.add_parser(
        "eod-report-status", help="Read-only EOD report delivery status for today (no secrets).",
    )
    eod_status_parser.add_argument("--team", default="both", help="Team(s): alpha, beta, both.")
    weekly_status_parser = subparsers.add_parser(
        "weekly-review-status", help="Read-only weekly review delivery status (no secrets).",
    )
    weekly_status_parser.add_argument("--team", default="both", help="Team(s): alpha, beta, both.")
    loop_health_parser = subparsers.add_parser(
        "loop-health", help="Read-only loop liveness (PID + heartbeat + per-team status).",
    )
    loop_health_parser.add_argument(
        "--stale-threshold-seconds", type=int, default=1800,
        help="Heartbeat age (s) beyond which the loop is considered stale. Default 1800.",
    )
    watchdog_parser = subparsers.add_parser(
        "loop-watchdog", help="Keep the competition loop alive (restart on dead/stale). Never trades.",
    )
    watchdog_parser.add_argument("--team", default="both", help="Team(s) the loop should run: alpha, beta, both.")
    watchdog_parser.add_argument("--sleep-seconds", type=int, default=900, help="Watchdog check interval. Default 900.")
    watchdog_parser.add_argument("--stale-threshold-seconds", type=int, default=1800,
                                 help="Heartbeat staleness threshold for restart. Default 1800.")
    watchdog_parser.add_argument("--once", action="store_true", help="Run a single check and exit (testing/manual).")
    watchdog_parser.add_argument("--dry-run", action="store_true", help="Assess + log but never restart.")
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
    elif args.command == "export-tomorrow-plan":
        run_export_tomorrow_plan(team=args.team)
    elif args.command == "market-hours-quiet-status":
        run_market_hours_quiet_status()
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
    elif args.command == "diagnose-competition-loop":
        run_diagnose_competition_loop(team=args.team)
    elif args.command == "review-team-portfolio":
        run_review_team_portfolio(team=args.team)
    elif args.command == "export-eod-report":
        run_export_eod_report(team=args.team, force=args.force, send=args.send)
    elif args.command == "memory-status":
        run_memory_status(team=args.team)
    elif args.command == "memory-maintenance":
        run_memory_maintenance(team=args.team, apply=args.apply)
    elif args.command == "weekly-team-review":
        run_weekly_team_review(team=args.team, send=args.send)
    elif args.command == "eod-report-status":
        run_eod_report_status(team=args.team)
    elif args.command == "weekly-review-status":
        run_weekly_review_status(team=args.team)
    elif args.command == "loop-health":
        run_loop_health(stale_threshold_seconds=args.stale_threshold_seconds)
    elif args.command == "loop-watchdog":
        run_loop_watchdog(
            team=args.team, sleep_seconds=args.sleep_seconds,
            stale_threshold_seconds=args.stale_threshold_seconds,
            once=args.once, dry_run=args.dry_run,
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
