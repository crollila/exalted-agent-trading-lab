"""Streamlit operator console for the ExaltedFable Agent Trading Lab.

Local-only, paper-only monitoring and control surface. This module only renders the pure
helpers from :mod:`src.ui.dashboard_state` and the existing gated builders in
:mod:`src.discord_bot.bot`; it never submits Alpaca orders directly and never bypasses a
safety gate.

Run with either:
    python -m src.main dashboard
    streamlit run src/ui/dashboard.py
"""

from __future__ import annotations

import streamlit as st

from src.config.settings import Settings
from src.discord_bot.bot import (
    DiscordBotConfig,
    build_disable_autonomy_summary,
    build_enable_autonomy_summary,
    build_team_autonomy_status_summary,
    build_team_paper_status_summary,
    build_team_positions_summary,
    build_team_report_summary,
)
from src.ui.dashboard_state import (
    DEFAULT_ASK_TEAM_OUTPUT_DIR,
    DEFAULT_RUN_CYCLE_PROMPT,
    DEFAULT_TEAM_CYCLE_DIR,
    KNOWN_TEAM_IDS,
    TeamStatus,
    collect_team_status,
    list_recent_runtime_files,
    read_safe_text,
    run_team_cycle_via_dashboard,
    team_status_table_rows,
)

PAGE_TITLE = "ExaltedFable Agent Trading Lab"


def _load_config() -> DiscordBotConfig:
    return DiscordBotConfig.from_env()


def _safe_settings() -> Settings | None:
    try:
        return Settings.from_env()
    except Exception:  # pragma: no cover - depends on local env
        return None


def _render_safety_banner() -> None:
    st.warning(
        "**Paper-only operator console — no live trading.**\n\n"
        "- No live trading\n"
        "- No short / margin / options execution\n"
        "- Natural chat and `!ask_team` / `!ask_agent` / `!run_tournament` never trade\n"
        "- Paper orders still require: autonomy enabled, risk agent approval, review agent "
        "approval, deterministic Python risk approval, daily caps, and the Alpaca paper-only "
        "wrapper. This dashboard does not bypass any of those gates."
    )


def _render_alpaca_section(team_id: str, settings: Settings | None) -> None:
    if settings is None:
        st.caption("Alpaca paper status unavailable: settings/credentials not configured.")
        return
    st.text(build_team_paper_status_summary(team_id, settings=settings))
    with st.expander(f"{team_id} positions"):
        st.text(build_team_positions_summary(team_id, settings=settings))


def _render_team_card(status: TeamStatus, settings: Settings | None) -> None:
    st.subheader(status.team_id)
    badge = "🟢 enabled" if status.autonomy_enabled else "⚪ disabled"
    st.markdown(f"**Autonomy:** {badge}")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(
            f"- Mode: `{status.mode}`\n"
            f"- Max paper orders/day: `{status.max_paper_orders_per_day}`\n"
            f"- Max daily notional: `${status.max_daily_notional:,.2f}`\n"
            f"- Natural chat channel: `{status.natural_chat_channel_id or 'not configured'}`"
        )
    with col_b:
        st.markdown(
            f"- Execution-eligible: `{status.execution_eligible_count}`\n"
            f"- Simulation-only: `{status.simulation_only_count}`\n"
            f"- Rejected: `{status.rejected_count}`\n"
            f"- Paper order status: `{status.paper_order_status}`"
        )

    st.markdown(
        f"- Parsed risk approval: **{'yes' if status.risk_approved else 'no'}**\n"
        f"- Parsed review approval: **{'yes' if status.review_approved else 'no'}**\n"
        f"- stock_long subset eligible: **{'yes' if status.stock_long_eligible else 'no'}**"
    )
    st.caption(f"Latest proposal: {status.latest_proposal_path or 'none saved yet'}")
    st.caption(f"Latest risk note: {status.latest_risk_note_path or 'none saved yet'}")
    st.caption(f"Latest review note: {status.latest_review_note_path or 'none saved yet'}")

    _render_alpaca_section(status.team_id, settings)
    with st.expander(f"{status.team_id} autonomy + gate details"):
        st.text(build_team_autonomy_status_summary(status.team_id, _load_config()))


def _render_controls(config: DiscordBotConfig) -> None:
    st.sidebar.header("Controls")
    if st.sidebar.button("🔄 Refresh"):
        st.rerun()

    st.sidebar.subheader("Autonomy")
    for team_id in KNOWN_TEAM_IDS:
        cols = st.sidebar.columns(2)
        if cols[0].button(f"Enable {team_id}", key=f"enable_{team_id}"):
            st.sidebar.success(build_enable_autonomy_summary(team_id, config))
            st.rerun()
        if cols[1].button(f"Disable {team_id}", key=f"disable_{team_id}"):
            st.sidebar.info(build_disable_autonomy_summary(team_id, config))
            st.rerun()

    st.sidebar.subheader("Kill switch")
    if st.sidebar.button("🛑 Disable ALL autonomy"):
        messages = [build_disable_autonomy_summary(team_id, config) for team_id in KNOWN_TEAM_IDS]
        st.sidebar.error("All team autonomy disabled.\n\n" + "\n\n".join(messages))
        st.rerun()


def _render_run_cycle_form(config: DiscordBotConfig, settings: Settings | None) -> None:
    st.header("Run team cycle")
    team_id = st.selectbox("Team", KNOWN_TEAM_IDS)
    autonomy_enabled = config.autonomy_enabled_for(team_id)
    prompt_text = st.text_area("Cycle prompt", value=DEFAULT_RUN_CYCLE_PROMPT, height=120)

    confirmation_checked = False
    if autonomy_enabled:
        st.warning(f"Autonomy is ENABLED for {team_id}.")
        confirmation_checked = st.checkbox(
            "I understand this may attempt Alpaca paper orders if all existing gates pass."
        )

    if st.button("Run cycle"):
        result = run_team_cycle_via_dashboard(
            team_id,
            prompt_text,
            config=config,
            autonomy_enabled=autonomy_enabled,
            confirmation_checked=confirmation_checked,
            settings=settings,
        )
        if result.ran:
            st.success("Cycle completed (subject to all safety gates).")
            st.text(result.message)
        else:
            st.error(result.message)


def _render_status_table(statuses: list[TeamStatus]) -> None:
    st.header("Team status")
    st.table(team_status_table_rows(statuses))


def _render_viewers(statuses: list[TeamStatus], settings: Settings | None) -> None:
    st.header("Read-only viewers")
    for status in statuses:
        with st.expander(f"{status.team_id} latest files"):
            st.markdown("**Latest proposal JSON**")
            st.code(read_safe_text(status.latest_proposal_path) or "none saved yet", language="json")
            st.markdown("**Latest risk note**")
            st.text(read_safe_text(status.latest_risk_note_path) or "none saved yet")
            st.markdown("**Latest review note**")
            st.text(read_safe_text(status.latest_review_note_path) or "none saved yet")
            if settings is not None:
                st.markdown("**Latest daily team report**")
                st.text(build_team_report_summary(status.team_id, settings=settings))

    with st.expander("Recent runtime files (read-only; not committed)"):
        recent = list_recent_runtime_files([DEFAULT_ASK_TEAM_OUTPUT_DIR, DEFAULT_TEAM_CYCLE_DIR])
        if recent:
            for path in recent:
                st.caption(str(path))
        else:
            st.caption("No runtime files found yet.")


def _render_equity_chart_placeholder() -> None:
    st.header("Equity / history")
    st.info("Equity chart pending Phase 7H.1.")


def render() -> None:
    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
    st.title(PAGE_TITLE)
    _render_safety_banner()

    config = _load_config()
    settings = _safe_settings()
    _render_controls(config)

    statuses = [
        collect_team_status(team_id, config, settings=settings)
        for team_id in KNOWN_TEAM_IDS
    ]

    columns = st.columns(len(statuses))
    for column, status in zip(columns, statuses):
        with column:
            _render_team_card(status, settings)

    _render_status_table(statuses)
    _render_run_cycle_form(config, settings)
    _render_viewers(statuses, settings)
    _render_equity_chart_placeholder()


render()
