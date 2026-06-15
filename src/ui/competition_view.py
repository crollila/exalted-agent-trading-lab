"""Streamlit competition pages (Part 11).

Two layers:

* Pure, testable data functions (no Streamlit import) that read the same safe
  functions the CLI uses. These never submit orders.
* Thin ``render_*`` functions that draw the pages with Streamlit.

Hard UI rules enforced here:

* The UI never submits broker orders. The only "actions" exposed are the kill
  switch (a safety control, not a broker call) and running the *gated research
  cycle with no broker client wired* (routing/simulation only).
* Secret fields never prefill saved values — the model provider page is
  status-only and shows whether keys are configured, never their values.
"""

from __future__ import annotations

from typing import Any

from src.agents.llm_provider import LLMProviderConfig
from src.brokers.paper_auth import CREDENTIAL_SOURCES, diagnose_all
from src.competition.risk_engine import AccountContext
from src.competition.week_competition import (
    WEEK_TEAMS,
    competition_status,
    run_week_cycle,
)
from src.config.permissions import TradingPermissions
from src.config.settings import Settings
from src.learning.team_memory import TeamLearningLedger
from src.safety.kill_switch import read_kill_switch


# --- pure data functions (testable without Streamlit) ---


def permissions_levels_data() -> dict[str, Any]:
    return TradingPermissions.from_env().summary()


def kill_switch_state_data() -> dict[str, Any]:
    return read_kill_switch().as_dict()


def competition_scoreboard_data() -> dict[str, Any]:
    return competition_status()


def team_learning_data(team_id: str) -> dict[str, Any]:
    ledger = TeamLearningLedger.load(team_id)
    return ledger.as_dict()


def model_provider_data() -> dict[str, Any]:
    """Model provider status with secrets MASKED (never returns key values)."""

    config = LLMProviderConfig.from_env()
    return {
        "provider": config.provider,
        "openai_model": config.openai_model,
        "openai_api_key_set": bool(config.openai_api_key),
        "anthropic_model": config.anthropic_model,
        "anthropic_api_key_set": bool(config.anthropic_api_key),
        "ollama_base_url": config.ollama_base_url,
        "ollama_model": config.ollama_model,
    }


def research_status_data() -> dict[str, Any]:
    from src.research.research_config import ResearchConfig
    from src.research.research_log import read_latest_research, research_log_count

    status = ResearchConfig.from_env().status()
    status["log_entries"] = research_log_count()
    status["teams"] = {team: read_latest_research(team) for team in WEEK_TEAMS}
    return status


def proposal_attribution_data(team_id: str) -> dict[str, Any]:
    from src.competition.attribution import load_team_attribution, performance_feedback

    entries = load_team_attribution(team_id)
    return {
        "count": len(entries),
        "feedback": performance_feedback(team_id),
        "recent": [e.as_dict() for e in entries[-15:]],
    }


def auth_statuses_data(attempt_auth: bool = True) -> dict[str, dict[str, Any]]:
    """Per-source Alpaca paper auth status (secrets never included)."""

    return {source: diag.as_dict() for source, diag in diagnose_all(attempt_auth=attempt_auth).items()}


def advanced_paper_levels_data() -> list[dict[str, Any]]:
    perms = TradingPermissions.from_env()
    return [
        {"level": 1, "name": "Paper Stocks (long)", "enabled": perms.stocks_enabled()},
        {"level": 2, "name": "Paper Shorting", "enabled": perms.shorting_enabled()},
        {"level": 3, "name": "Paper Margin", "enabled": perms.margin_enabled()},
        {"level": 4, "name": "Paper Options", "enabled": perms.options_enabled()},
    ]


# --- Streamlit render functions ---


def _account(settings: Settings | None) -> AccountContext:
    equity = settings.starting_equity if settings else 1_000_000.0
    return AccountContext(equity=equity, cash=equity, buying_power=equity * 2.0)


def render_permissions(st) -> None:
    st.header("Permissions / Risk Levels")
    st.caption("Paper-only. Advanced levels are unlockable but disabled by default.")
    for level in advanced_paper_levels_data():
        state = "✅ ENABLED" if level["enabled"] else "🔒 disabled"
        st.write(f"**Level {level['level']} — {level['name']}**: {state}")
    st.subheader("Risk caps")
    st.json(permissions_levels_data()["caps"])
    st.info("Change these only via your local (git-ignored) .env. The UI never submits orders.")


def render_advanced_paper(st) -> None:
    st.header("Advanced Paper Trading")
    perms = permissions_levels_data()
    st.write(f"Trading mode: **{perms['trading_mode']}** (paper-only)")
    st.write(f"Paper stocks: {'on' if perms['paper_stocks'] else 'off'}")
    st.write(f"Paper shorting: {'on' if perms['paper_shorting'] else 'off'}")
    st.write(f"Paper margin: {'on' if perms['paper_margin'] else 'off'}")
    st.write(f"Paper options: {'on' if perms['paper_options'] else 'off'}")
    st.write(f"Allow naked options: {'on' if perms['allow_naked_options'] else 'off'}")
    st.warning(
        "Shorting, margin, and options run only through the deterministic risk engine "
        "and the kill-switch-guarded broker bridge. LLMs never place trades."
    )


def render_kill_switch(st) -> None:
    st.header("Kill Switch")
    state = kill_switch_state_data()
    if state["engaged"]:
        st.error("KILL SWITCH ENGAGED — all new broker submissions are blocked.")
        if st.button("Disengage kill switch"):
            from src.safety.kill_switch import disengage

            disengage()
            st.rerun()
    else:
        st.success("Kill switch disengaged.")
        if st.button("ENGAGE kill switch"):
            from src.safety.kill_switch import engage

            engage(reason="UI kill switch")
            st.rerun()


def render_auth_panel(st) -> None:
    st.subheader("Alpaca paper auth (global / alpha / beta)")
    statuses = auth_statuses_data()
    for source in CREDENTIAL_SOURCES:
        d = statuses[source]
        state = "OK" if d["auth_ok"] else d["classification"]
        st.write(f"**{source}**: {state}")
    global_ok = statuses["global"]["auth_ok"]
    teams_ok = statuses["team_alpha"]["auth_ok"] and statuses["team_beta"]["auth_ok"]
    if not global_ok and teams_ok:
        st.info(
            "Global credentials are not authenticated, but the competition is NOT blocked: "
            "each team uses its own credentials."
        )


def render_weekly_competition(st, settings: Settings | None) -> None:
    st.header("Weekly Competition")
    st.caption("Alpha vs Beta — paper-only. The UI runs routing/simulation only; it never submits orders.")
    render_auth_panel(st)
    team = st.selectbox("Team", list(WEEK_TEAMS))
    if st.button("Run gated research cycle (no orders submitted)"):
        result = run_week_cycle(
            team,
            permissions=TradingPermissions.from_env(),
            account=_account(settings),
            client=None,
            dry_run=True,
        )
        st.write(result.routing.summary())
        for line in result.stage_log:
            st.text(line)


def render_scoreboard(st) -> None:
    st.header("Alpha vs Beta Scoreboard")
    data = competition_scoreboard_data()
    st.write(f"Active: {data['active']} | Week start: {data['week_start']} | Week end: {data['week_end']}")
    teams = data["teams"]
    if not teams:
        st.info("No scorecards yet. Run a cycle from the Weekly Competition page or CLI.")
        return
    st.table(
        [
            {
                "rank": card.get("current_rank"),
                "team": card["team_id"],
                "equity": card["current_equity"],
                "excess_vs_spy": card.get("excess_return_vs_spy"),
                "orders": card["orders_submitted"],
                "approved": card["approved_count"],
                "sim_only": card["simulation_only_count"],
            }
            for card in teams
        ]
    )


def render_team_learning(st) -> None:
    st.header("Team Learning")
    team = st.selectbox("Team", list(WEEK_TEAMS), key="learning_team")
    data = team_learning_data(team)
    st.write(f"Active strategy: {data.get('active_strategy') or '(none)'}")
    st.write(f"Current hypothesis: {data.get('current_hypothesis') or '(none)'}")
    st.subheader("Lessons learned")
    st.write(data.get("lessons_learned") or ["(none yet)"])
    st.subheader("Strategy changes")
    st.write(data.get("strategy_changes") or ["(none yet)"])
    st.subheader("Risk notes")
    st.write(data.get("risk_notes") or ["(none yet)"])


def render_research(st) -> None:
    st.header("Research")
    data = research_status_data()
    st.write(f"Provider: **{data['provider']}** | available: {data['available']}")
    st.write(f"Alpaca news: {data['uses_alpaca']} | OpenAI web: {data['uses_openai_web']} ({data['openai_web_model']})")
    st.write(f"Log entries: {data['log_entries']}")
    for team, latest in data["teams"].items():
        st.subheader(team)
        if not latest:
            st.caption("No research logged yet.")
            continue
        results = latest.get("results", [])
        st.caption(f"{len(results)} result(s) via {latest.get('provider')}")
        for item in results[:5]:
            st.write(f"[{item.get('source_id')}] {item.get('title')} — {item.get('summary', '')[:140]}")


def render_attribution(st) -> None:
    st.header("Proposal Attribution")
    team = st.selectbox("Team", list(WEEK_TEAMS), key="attribution_team")
    data = proposal_attribution_data(team)
    if data["count"] == 0:
        st.info("No attribution records yet. Run a cycle first.")
        return
    fb = data["feedback"]
    st.write(f"Tracked: {data['count']} | pending: {fb['pending_count']}")
    st.write(f"Best/worst symbol: {fb['best_symbol']} / {fb['worst_symbol']}")
    st.write(f"Best/worst strategy: {fb['best_strategy']} / {fb['worst_strategy']}")
    st.subheader("Recent winners")
    st.write(fb["recent_winners"] or ["(none)"])
    st.subheader("Recent losers")
    st.write(fb["recent_losers"] or ["(none)"])
    st.subheader("Recent proposals")
    st.table(
        [
            {
                "symbol": e["symbol"],
                "asset_type": e["asset_type"],
                "routing": e["routing"],
                "submitted": e["broker_submitted"],
                "outcome": e["thesis_outcome"],
                "return_pct": e["return_pct"],
                "sources": ",".join(e["research_source_ids"]),
            }
            for e in data["recent"]
        ]
    )


def render_model_provider(st) -> None:
    st.header("Model Provider Setup")
    data = model_provider_data()
    st.write(f"Active provider: **{data['provider']}**")
    st.write(f"OpenAI key configured: {data['openai_api_key_set']} (model: {data['openai_model']})")
    st.write(f"Anthropic key configured: {data['anthropic_api_key_set']} (model: {data['anthropic_model']})")
    st.write(f"Ollama: {data['ollama_base_url']} (model: {data['ollama_model']})")
    st.info(
        "Secret fields are never prefilled. Set provider keys in your local (git-ignored) .env. "
        "The LLM provider only returns proposal/review/learning JSON and never has broker access."
    )
