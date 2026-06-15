"""Streamlit operator console for the ExaltedFable Agent Trading Lab (Phase 7H.1).

A full local, paper-only operator console with sidebar navigation. It only renders the pure
helpers in :mod:`src.ui.dashboard_state` / :mod:`src.ui.env_config` and the existing gated
builders in :mod:`src.discord_bot.bot`. It never submits Alpaca orders directly, never
bypasses a safety gate, and never displays secret values.

Run with either:
    python -m src.main dashboard
    streamlit run src/ui/dashboard.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.config.settings import Settings
from src.discord_bot.bot import (
    DiscordBotConfig,
    build_disable_autonomy_summary,
    build_enable_autonomy_summary,
    build_latest_team_cycle_summary,
    build_team_autonomy_status_summary,
    build_team_paper_status_summary,
    build_team_positions_summary,
    build_team_report_summary,
)
from src.ui.agent_hub import (
    agent_chat_reply,
    build_agent_hub_evidence_context,
    render_evidence_context,
    team_chat_reply,
)
from src.ui.dashboard_state import (
    AGENT_CHAT_MODE,
    AGENT_HUB_AGENT_IDS,
    ASK_AGENT_MODE,
    ASK_TEAM_MODE,
    TEAM_CHAT_MODE,
    DEFAULT_ASK_TEAM_OUTPUT_DIR,
    DEFAULT_RUN_CYCLE_PROMPT,
    DEFAULT_TEAM_CYCLE_DIR,
    KNOWN_TEAM_IDS,
    QUICK_PROMPTS,
    TeamStatus,
    active_notifications,
    agent_hub_ask_agent,
    agent_hub_ask_team,
    agent_hub_history_key,
    agent_hub_transcript_path,
    append_chat_message,
    clear_chat_history,
    collect_agent_stats,
    collect_team_status,
    default_research_agent,
    dismiss_notifications,
    disable_all_autonomy,
    get_chat_history,
    validate_proposal_prompt,
    list_recent_runtime_files,
    push_notification,
    read_safe_text,
    reset_team_to_safe_defaults,
    run_team_cycle_via_dashboard,
    save_agent_hub_transcript,
    team_status_table_rows,
    update_team_runtime_config,
)
from src.ui.process_control import (
    bot_log_path,
    build_bot_process_report,
    read_tail,
    restart_discord_bot,
    start_discord_bot,
    stop_all_bot_processes,
    stop_discord_bot,
)
from src.ui.env_config import (
    DEFAULT_ENV_PATH,
    SETUP_DISCORD_KEYS,
    build_env_updates,
    env_setup_status,
    read_env_file,
    recommended_first_test_env_text,
    secret_field_status_label,
    write_env_updates,
)
from src.ui.daily_lab import (
    AgentGoal,
    DEFAULT_LEARNING_LEDGER_PATH,
    LearningLedgerEntry,
    append_learning_ledger_entry,
    build_improvement_score,
    build_strategy_scorecards,
    default_agent_goal,
    goals_memory_context,
    latest_lesson_summary,
    learning_memory_context,
    morning_checklist_lines,
    no_automatic_changes_notice,
    read_agent_goal,
    read_learning_ledger,
    working_on_summary,
    write_agent_goal,
)
from src.ui.data_tools import (
    agent_market_data_rules,
    build_data_source_statuses,
    data_source_rows,
    market_snapshot_context,
)
from src.ui.portfolio_view import (
    allocation_rows,
    collect_team_portfolio_snapshot,
    compare_team_portfolios,
    portfolio_history_message,
    position_table_rows,
)
from src.ui.setup_wizard import (
    build_setup_checks,
    first_run_step_labels,
    recommended_safe_updates,
    setup_progress_percent,
    setup_secret_status_rows,
)
from src.ui.ui_templates import (
    DEFAULT_TEMPLATE_CONFIG_PATH,
    UI_TEMPLATES,
    reset_template_selection,
    save_template_selection,
    selected_template_id,
    template_landing_metadata,
    template_options,
)

PAGE_TITLE = "ExaltedFable Agent Trading Lab"
RUNTIME_BROWSE_DIRS = [
    DEFAULT_ASK_TEAM_OUTPUT_DIR,
    DEFAULT_TEAM_CYCLE_DIR,
    "data/reports",
    "data/experiments",
]

_BADGE_COLORS = {"green": "#1f9d55", "yellow": "#b7791f", "red": "#c53030", "gray": "#4a5568"}


# ---------------------------------------------------------------------------
# Small render helpers
# ---------------------------------------------------------------------------
def _badge(label: str, color: str) -> str:
    bg = _BADGE_COLORS.get(color, _BADGE_COLORS["gray"])
    return (
        f"<span style='background:{bg};color:white;padding:2px 10px;border-radius:8px;"
        f"font-size:0.8rem;font-weight:600'>{label}</span>"
    )


def _yes_no_badge(value: bool) -> str:
    return _badge("yes", "green") if value else _badge("no", "gray")


def _notify(message: str, level: str = "success") -> None:
    """Push a persistent notification and rerun so it renders at the top of the page."""

    push_notification(st.session_state, message, level=level)
    st.rerun()


def _render_notifications() -> None:
    items = active_notifications(st.session_state)
    if not items:
        return
    with st.container(border=True):
        for item in items:
            renderer = getattr(st, item.get("level", "info"), st.info)
            renderer(item["message"])
        if st.button("Dismiss notifications"):
            dismiss_notifications(st.session_state)
            st.rerun()


def _load_config() -> DiscordBotConfig:
    return DiscordBotConfig.from_env()


def _safe_settings() -> Settings | None:
    try:
        return Settings.from_env()
    except Exception:  # pragma: no cover - depends on local env
        return None


def _hermes_configured() -> bool:
    base = os.environ.get("HERMES_BASE_URL", "").strip()
    model = os.environ.get("HERMES_MODEL", "").strip()
    return bool(base and model)


def _discord_configured() -> bool:
    return bool(os.environ.get("DISCORD_BOT_TOKEN", "").strip())


def _safety_banner() -> None:
    st.error(
        "**PAPER-ONLY — LIVE TRADING DISABLED.** No short / margin / options execution. "
        "Natural chat and `!ask_team` / `!ask_agent` / `!run_tournament` never trade. "
        "Paper orders require autonomy + risk approval + review approval + deterministic "
        "Python risk + daily caps + the Alpaca paper-only wrapper. This console does not "
        "bypass any gate and never submits orders directly."
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _apply_template_style(template_id: str) -> None:
    palette = {
        "portfolio_cockpit": {
            "bg": "#f6f8fb",
            "panel": "#ffffff",
            "accent": "#2563eb",
            "text": "#172033",
        },
        "ai_team_room": {
            "bg": "#101820",
            "panel": "#16242f",
            "accent": "#2fbf71",
            "text": "#f4f8f6",
        },
        "command_center": {
            "bg": "#050608",
            "panel": "#101318",
            "accent": "#f59e0b",
            "text": "#f5f5f0",
        },
    }.get(template_id, {})
    if palette:
        st.markdown(
            f"""
<style>
.stApp {{
    background: {palette["bg"]};
    color: {palette["text"]};
}}
div[data-testid="stSidebar"] {{
    border-right: 1px solid rgba(120, 120, 120, 0.22);
}}
.phase7j-panel {{
    background: {palette["panel"]};
    border: 1px solid rgba(120, 120, 120, 0.22);
    border-left: 4px solid {palette["accent"]};
    border-radius: 8px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.75rem;
}}
.phase7j-kicker {{
    color: {palette["accent"]};
    font-weight: 700;
    text-transform: uppercase;
    font-size: 0.76rem;
}}
</style>
""",
            unsafe_allow_html=True,
        )
    st.markdown(
        """
<style>
div[data-testid="stMetric"] {
    background: rgba(255, 255, 255, 0.045);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 0.75rem;
}
.template-note {
    color: rgba(250, 250, 250, 0.72);
    font-size: 0.9rem;
}
</style>
""",
        unsafe_allow_html=True,
    )
    if template_id == "portfolio_cockpit":
        st.markdown("<style>.block-container{padding-top:1.5rem;}</style>", unsafe_allow_html=True)
    if template_id == "command_center":
        st.markdown(
            "<style>body, .stMarkdown, .stCode {font-family: Consolas, 'Courier New', monospace;}</style>",
            unsafe_allow_html=True,
        )


def _safety_banner(*, compact: bool = False) -> None:
    message = (
        "**PAPER-ONLY - LIVE TRADING DISABLED.** No short / margin / options execution. "
        "Paper orders require autonomy + risk approval + review approval + deterministic "
        "Python risk + daily caps + the Alpaca paper-only wrapper. This console never "
        "submits orders directly."
    )
    if compact:
        st.info(message)
    else:
        st.error(
            message
            + " Natural chat and `!ask_team` / `!ask_agent` / `!run_tournament` never trade."
        )


def _metric_value(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def _next_safe_action(statuses: list[TeamStatus]) -> str:
    if any(status.autonomy_enabled for status in statuses):
        return "Review approvals and caps, then disable autonomy when the controlled paper test is complete."
    if any(status.execution_eligible_count for status in statuses):
        return "Review latest risk/review notes before enabling a single Alpha paper test."
    return "Run a disabled-autonomy Alpha cycle, then record the lesson in Daily Lab."


def _render_template_home(
    template_id: str,
    config: DiscordBotConfig,
    settings: Settings | None,
    statuses: list[TeamStatus],
) -> None:
    if template_id == "command_center":
        _render_command_center_home(config, settings, statuses)
    elif template_id == "ai_team_room":
        _render_ai_team_room_home(config, settings, statuses)
    else:
        _render_portfolio_home(config, settings, statuses)


def _render_portfolio_home(
    config: DiscordBotConfig,
    settings: Settings | None,
    statuses: list[TeamStatus],
) -> None:
    metadata = template_landing_metadata("portfolio_cockpit")
    st.header(metadata["landing_title"])
    st.caption("Portfolio-first paper-account cockpit. Advanced runtime details stay tucked away.")
    snapshots = {
        team_id: collect_team_portfolio_snapshot(team_id, base_settings=settings)
        for team_id in KNOWN_TEAM_IDS
    }
    alpha = snapshots["team_alpha"]
    beta = snapshots["team_beta"]
    cols = st.columns(4)
    cols[0].metric("Alpha equity", _metric_value(alpha.equity))
    cols[1].metric("Alpha cash", _metric_value(alpha.cash))
    cols[2].metric("Beta equity", _metric_value(beta.equity))
    cols[3].metric("Beta cash", _metric_value(beta.cash))

    cols = st.columns(4)
    cols[0].metric("Alpha buying power", _metric_value(alpha.buying_power))
    cols[1].metric("Alpha positions", alpha.positions_count)
    cols[2].metric("Beta buying power", _metric_value(beta.buying_power))
    cols[3].metric("Beta positions", beta.positions_count)

    market_values = [snapshot.market_open for snapshot in snapshots.values() if snapshot.market_open is not None]
    market_label = "open" if any(market_values) else "closed/unknown"
    latest_status = statuses[0] if statuses else None
    st.markdown(
        f"""
<div class="phase7j-panel">
<div class="phase7j-kicker">Daily Status</div>
Market: <strong>{market_label}</strong><br>
Latest cycle: <strong>{latest_status.paper_order_status if latest_status else 'unknown'}</strong><br>
Next safe action: <strong>{_next_safe_action(statuses)}</strong>
</div>
""",
        unsafe_allow_html=True,
    )

    st.subheader("Approvals")
    st.dataframe(team_status_table_rows(statuses), use_container_width=True, hide_index=True)
    st.subheader("Positions")
    position_rows = []
    for snapshot in snapshots.values():
        for row in position_table_rows(snapshot):
            row["team"] = snapshot.team_id
            position_rows.append(row)
    if position_rows:
        st.dataframe(position_rows, use_container_width=True, hide_index=True)
        alloc_rows = []
        for snapshot in snapshots.values():
            for row in allocation_rows(snapshot):
                row["team"] = snapshot.team_id
                alloc_rows.append(row)
        if alloc_rows:
            st.bar_chart(pd.DataFrame(alloc_rows), x="symbol", y="weight_pct")
    else:
        st.info("No positions available yet.")

    with st.expander("Advanced paper-account details", expanded=False):
        for status in statuses:
            st.text(build_team_autonomy_status_summary(status.team_id, config))


def _render_command_center_home(
    config: DiscordBotConfig,
    settings: Settings | None,
    statuses: list[TeamStatus],
) -> None:
    metadata = template_landing_metadata("command_center")
    st.header(metadata["landing_title"])
    st.caption("Operator console: process state, runtime evidence, logs, and kill switch up front.")
    report = build_bot_process_report()
    cols = st.columns(4)
    cols[0].metric("UI PID running", "yes" if report.pid_file_running else "no")
    cols[1].metric("Detected bot PIDs", len(report.detected_pids))
    cols[2].metric("Autonomy enabled", sum(1 for status in statuses if status.autonomy_enabled))
    cols[3].metric("Runtime files", len(list_recent_runtime_files(RUNTIME_BROWSE_DIRS, limit=50)))
    if st.button("Disable ALL autonomy", type="primary", key="home_kill_switch"):
        disable_all_autonomy(config)
        _notify("Kill switch: all team autonomy disabled.", "warning")

    st.subheader("Process and warnings")
    st.code(
        "\n".join(
            [
                f"pid_file_running={report.pid_file_running}",
                f"pid_file_pid={report.pid_file_pid}",
                f"detected_pids={report.detected_pids}",
                f"untracked_running={report.untracked_running}",
                f"log_path={bot_log_path()}",
            ]
        ),
        language="text",
    )
    st.subheader("Agent status")
    st.dataframe(team_status_table_rows(statuses), use_container_width=True, hide_index=True)
    st.subheader("Latest runtime files")
    for path in list_recent_runtime_files(RUNTIME_BROWSE_DIRS, limit=10):
        st.caption(str(path))
    st.subheader("Bot log tail")
    st.code(read_tail(bot_log_path(), max_chars=6000) or "(no log yet)", language="text")


def _render_ai_team_room_home(
    config: DiscordBotConfig,
    settings: Settings | None,
    statuses: list[TeamStatus],
) -> None:
    metadata = template_landing_metadata("ai_team_room")
    st.header(metadata["landing_title"])
    st.caption("Team-chat-first view. Current work is grounded in saved proposals, notes, goals, and lessons.")
    lesson = latest_lesson_summary()
    rooms = st.columns(2)
    for index, team_id in enumerate(KNOWN_TEAM_IDS):
        status = next(status for status in statuses if status.team_id == team_id)
        goal = read_agent_goal(team_id)
        with rooms[index]:
            st.subheader(team_id.replace("_", " ").title())
            st.markdown(
                f"""
<div class="phase7j-panel">
<div class="phase7j-kicker">Current Focus</div>
{goal.current_agent_focus or 'No focus saved yet.'}<br>
Hypothesis: <strong>{goal.hypothesis or 'none'}</strong>
</div>
""",
                unsafe_allow_html=True,
            )
            for agent_id in AGENT_HUB_AGENT_IDS.get(team_id, ()):
                role = agent_id.split("_")[1] if "_" in agent_id else "agent"
                st.markdown(f"**{agent_id}**  \n{role} - no direct trade permissions")
            with st.expander("What are we working on?", expanded=True):
                for row in working_on_summary(status, goal, latest_lesson=lesson):
                    st.markdown(f"**{row['label']}**: {row['value']}")

    st.subheader("Learning / Goals / Current Tasks")
    st.text(goals_memory_context(KNOWN_TEAM_IDS))
    st.text(learning_memory_context(limit=2))
    st.subheader("Chat")
    st.info("Use Agent Hub for a live grounded team chat. This home view shows the evidence first.")


def _render_overview(config: DiscordBotConfig, settings: Settings | None, statuses: list[TeamStatus]) -> None:
    st.header("Overview")
    cols = st.columns(4)
    cols[0].markdown("Mode<br>" + _badge("PAPER-ONLY", "green"), unsafe_allow_html=True)
    cols[1].markdown("Live trading<br>" + _badge("DISABLED", "green"), unsafe_allow_html=True)
    cols[2].markdown(
        "Hermes/Ollama<br>" + (_badge("configured", "green") if _hermes_configured() else _badge("missing", "yellow")),
        unsafe_allow_html=True,
    )
    cols[3].markdown(
        "Discord<br>" + (_badge("configured", "green") if _discord_configured() else _badge("missing", "yellow")),
        unsafe_allow_html=True,
    )

    warnings: list[str] = []
    st.subheader("Teams at a glance")
    for status in statuses:
        with st.container(border=True):
            top = st.columns([1, 1, 1, 1])
            top[0].markdown(
                f"**{status.team_id}**<br>autonomy "
                + (_badge("enabled", "red") if status.autonomy_enabled else _badge("disabled", "green")),
                unsafe_allow_html=True,
            )
            top[1].markdown(
                "latest risk<br>" + _yes_no_badge(status.risk_approved), unsafe_allow_html=True
            )
            top[2].markdown(
                "latest review<br>" + _yes_no_badge(status.review_approved), unsafe_allow_html=True
            )
            top[3].markdown(
                "stock_long eligible<br>" + _yes_no_badge(status.stock_long_eligible),
                unsafe_allow_html=True,
            )
            st.caption(
                f"Exec-eligible {status.execution_eligible_count} · sim-only "
                f"{status.simulation_only_count} · rejected {status.rejected_count} · "
                f"paper orders: {status.paper_order_status}"
            )
            if settings is not None:
                st.text(build_team_paper_status_summary(status.team_id, settings=settings))

        if status.autonomy_enabled:
            warnings.append(f"{status.team_id}: autonomy is ENABLED.")
        if status.max_daily_notional >= 1_000_000:
            warnings.append(f"{status.team_id}: high daily notional cap ${status.max_daily_notional:,.0f}.")
        if status.execution_eligible_count >= 1 and not status.risk_approved:
            warnings.append(f"{status.team_id}: latest cycle has a risk rejection.")
        if status.execution_eligible_count >= 1 and not status.review_approved:
            warnings.append(f"{status.team_id}: latest cycle has a review rejection.")

    if settings is None:
        warnings.append("Local settings/credentials are missing — Alpaca paper status unavailable.")
    recent_runtime = list_recent_runtime_files(RUNTIME_BROWSE_DIRS, limit=1)
    if recent_runtime:
        warnings.append("Untracked runtime state is present under data/ (never committed).")

    st.subheader("Warnings")
    if warnings:
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("No active warnings.")

    st.subheader("Kill switch")
    if st.button("🛑 Disable ALL autonomy", type="primary"):
        disable_all_autonomy(config)
        _notify("Kill switch: all team autonomy disabled.", "warning")


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
def _render_teams(config: DiscordBotConfig, settings: Settings | None, statuses: list[TeamStatus]) -> None:
    st.header("Teams")
    for status in statuses:
        with st.container(border=True):
            st.subheader(status.team_id)
            autonomy = config.autonomy_for(status.team_id)
            meta = st.columns(2)
            meta[0].markdown(
                f"- Mode: `{status.mode}`\n"
                f"- Natural chat channel: `{status.natural_chat_channel_id or 'not configured'}`\n"
                f"- Latest proposal: `{status.latest_proposal_path or 'none'}`"
            )
            meta[1].markdown(
                f"- Risk note: `{status.latest_risk_note_path or 'none'}`\n"
                f"- Review note: `{status.latest_review_note_path or 'none'}`\n"
                f"- Paper orders: `{status.paper_order_status}`"
            )

            if settings is not None:
                st.text(build_team_paper_status_summary(status.team_id, settings=settings))
                with st.expander(f"{status.team_id} positions"):
                    st.text(build_team_positions_summary(status.team_id, settings=settings))

            st.markdown("**Autonomy controls**")
            ctrl = st.columns(3)
            if ctrl[0].button("Enable", key=f"enable_{status.team_id}"):
                build_enable_autonomy_summary(status.team_id, config)
                _notify(f"{status.team_id} autonomy enabled.", "warning")
            if ctrl[1].button("Disable", key=f"disable_{status.team_id}"):
                build_disable_autonomy_summary(status.team_id, config)
                _notify(f"{status.team_id} autonomy disabled.", "success")
            if ctrl[2].button("Reset to safe defaults", key=f"reset_{status.team_id}"):
                reset_team_to_safe_defaults(status.team_id, config)
                _notify(f"{status.team_id} reset to safe first-test defaults.", "success")

            with st.form(f"team_cfg_{status.team_id}"):
                max_orders = st.number_input(
                    "Max paper orders/day",
                    min_value=0,
                    value=int(autonomy.max_paper_orders_per_day),
                    key=f"orders_{status.team_id}",
                )
                max_notional = st.number_input(
                    "Max daily notional ($)",
                    min_value=0.0,
                    value=float(autonomy.max_daily_notional),
                    step=1000.0,
                    key=f"notional_{status.team_id}",
                )
                require_risk = st.checkbox(
                    "Require risk approval (recommended: on)",
                    value=autonomy.require_risk_agent_approval,
                    key=f"req_risk_{status.team_id}",
                )
                require_review = st.checkbox(
                    "Require review approval (recommended: on)",
                    value=autonomy.require_review_agent_approval,
                    key=f"req_review_{status.team_id}",
                )
                if not require_risk or not require_review:
                    st.warning("Turning off an approval requirement weakens safety. Keep both on.")
                if st.form_submit_button("Save team runtime config"):
                    update_team_runtime_config(
                        status.team_id,
                        config,
                        max_paper_orders_per_day=int(max_orders),
                        max_daily_notional=float(max_notional),
                        require_risk_agent_approval=bool(require_risk),
                        require_review_agent_approval=bool(require_review),
                    )
                    _notify(f"{status.team_id} runtime config saved.", "success")

            with st.expander(f"{status.team_id} autonomy + gate details"):
                st.text(build_team_autonomy_status_summary(status.team_id, config))


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
def _render_agents(config: DiscordBotConfig) -> None:
    st.header("Agents")
    st.caption("Stats below are runtime-derived estimates from saved files. Agents never have direct trade permissions.")
    role_descriptions = {
        "research_agent": "proposes ideas and research",
        "risk_agent": "critiques risk before any paper path can proceed",
        "review_agent": "final sanity/review before deterministic risk",
    }
    current_focus = learning_memory_context(limit=2)
    for team_id in KNOWN_TEAM_IDS:
        st.subheader(team_id)
        with st.container(border=True):
            st.markdown(
                f"**{team_id} team room**  \n"
                "No direct trade permissions. All agents are proposal/review roles only."
            )
            with st.expander("Current focus from local learning ledger", expanded=False):
                st.text(current_focus)
        try:
            stats = collect_agent_stats(team_id, registry_path=config.default_registry_path)
        except Exception as exc:  # pragma: no cover - depends on registry file
            st.warning(f"Agent stats unavailable for {team_id}: {exc}")
            continue
        for stat in stats:
            with st.container(border=True):
                st.markdown(f"**{stat.agent_id}** · `{stat.role}` · {stat.team_id}")
                cols = st.columns(4)
                st.caption(role_descriptions.get(stat.role, "agent"))
                st.markdown(_badge("no direct trade permissions", "green"), unsafe_allow_html=True)
                cols[0].metric("Proposal files", stat.proposal_files_generated)
                cols[1].metric("Exec-eligible", stat.execution_eligible_count)
                cols[2].metric("Sim-only", stat.simulation_only_count)
                cols[3].metric("Rejected", stat.rejected_count)
                st.caption(
                    f"Cycles participated: {stat.cycles_participated} · "
                    f"latest action: {stat.latest_action_time or 'n/a'}"
                )
                if stat.role == "risk_agent":
                    st.markdown("Latest risk approval: " + _yes_no_badge(bool(stat.risk_approved)), unsafe_allow_html=True)
                if stat.role == "review_agent":
                    st.markdown("Latest review approval: " + _yes_no_badge(bool(stat.review_approved)), unsafe_allow_html=True)
                st.caption(f"Latest run: {stat.latest_run_path or 'none'}")
                st.caption(f"Latest note: {stat.latest_note_path or 'none'}")
                if stat.latest_note_path is not None:
                    with st.expander("Recent note"):
                        st.text(read_safe_text(stat.latest_note_path) or "(empty)")
                if stat.latest_run_path is not None:
                    with st.expander("Latest proposal file"):
                        st.code(read_safe_text(stat.latest_run_path) or "{}", language="json")


# ---------------------------------------------------------------------------
# Run cycle
# ---------------------------------------------------------------------------
def _render_run_cycle(config: DiscordBotConfig, settings: Settings | None) -> None:
    st.header("Run cycle")
    team_id = st.selectbox("Team", KNOWN_TEAM_IDS)
    autonomy_enabled = config.autonomy_enabled_for(team_id)

    if "run_cycle_prompt" not in st.session_state:
        st.session_state["run_cycle_prompt"] = DEFAULT_RUN_CYCLE_PROMPT
    st.markdown("**Quick prompts**")
    quick_cols = st.columns(len(QUICK_PROMPTS))
    for column, (label, prompt) in zip(quick_cols, QUICK_PROMPTS.items()):
        if column.button(label):
            st.session_state["run_cycle_prompt"] = prompt
    prompt_text = st.text_area("Cycle prompt", key="run_cycle_prompt", height=140)

    if autonomy_enabled:
        st.warning(f"Autonomy is ENABLED for {team_id}. A run may attempt gated paper orders.")
        confirmation_checked = st.checkbox(
            "I understand this may attempt Alpaca paper orders if all existing gates pass."
        )
    else:
        st.info(f"Autonomy is disabled for {team_id}. No paper orders will be submitted.")
        confirmation_checked = False

    if st.button("Run cycle", type="primary"):
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
            st.error("Run blocked: " + result.message)

    st.divider()
    st.caption("After a run, see the latest split, approvals, and saved paths below.")
    status = collect_team_status(team_id, config, settings=settings)
    cols = st.columns(4)
    cols[0].metric("Exec-eligible", status.execution_eligible_count)
    cols[1].metric("Sim-only", status.simulation_only_count)
    cols[2].metric("Rejected", status.rejected_count)
    cols[3].metric("Paper orders", status.paper_order_status)
    st.markdown(
        "Risk approval: " + _yes_no_badge(status.risk_approved)
        + " &nbsp; Review approval: " + _yes_no_badge(status.review_approved)
        + " &nbsp; Deterministic-risk eligible: " + _yes_no_badge(status.stock_long_eligible),
        unsafe_allow_html=True,
    )
    with st.expander("Latest proposal / risk / review"):
        st.code(read_safe_text(status.latest_proposal_path) or "none", language="json")
        st.text(read_safe_text(status.latest_risk_note_path) or "none")
        st.text(read_safe_text(status.latest_review_note_path) or "none")


def _render_daily_lab(config: DiscordBotConfig, settings: Settings | None, statuses: list[TeamStatus]) -> None:
    st.header("Daily Lab")
    st.caption("Repeatable review loop for safer improvement. Runtime memory only; no automatic model training.")

    st.subheader("Agent goals")
    goal_team = st.selectbox("Goal team", KNOWN_TEAM_IDS, key="goal_team")
    current_goal = read_agent_goal(goal_team)
    with st.form("agent_goal_form"):
        team_goal = st.text_area("Current team goal", value=current_goal.current_team_goal, height=70)
        agent_focus = st.text_area("Current agent focus", value=current_goal.current_agent_focus, height=70)
        constraints = st.text_area("Current constraints", value=current_goal.current_constraints, height=70)
        next_action_goal = st.text_area("Next action", value=current_goal.next_action, height=70)
        open_questions = st.text_area("Open questions", value=current_goal.open_questions, height=70)
        hypothesis = st.text_area("Hypothesis being tested", value=current_goal.hypothesis, height=70)
        if st.form_submit_button("Save agent goal"):
            path = write_agent_goal(
                AgentGoal(
                    team=goal_team,
                    current_team_goal=team_goal,
                    current_agent_focus=agent_focus,
                    current_constraints=constraints,
                    next_action=next_action_goal,
                    open_questions=open_questions,
                    hypothesis=hypothesis,
                )
            )
            _notify(f"Agent goal saved to {path}.", "success")

    st.subheader("Daily autonomy loop scaffold")
    loop_cols = st.columns(4)
    loop_cols[0].checkbox("Morning plan", value=False, key="loop_morning")
    loop_cols[1].checkbox("Market-hours paper test", value=False, key="loop_market")
    loop_cols[2].checkbox("End-of-day review", value=False, key="loop_eod")
    loop_cols[3].checkbox("Learning update", value=False, key="loop_learning")
    tomorrow_hypothesis = st.text_input("Tomorrow's hypothesis", value=current_goal.hypothesis)
    if st.button("Save tomorrow's hypothesis"):
        path = write_agent_goal(
            AgentGoal(
                team=goal_team,
                current_team_goal=current_goal.current_team_goal,
                current_agent_focus=current_goal.current_agent_focus,
                current_constraints=current_goal.current_constraints,
                next_action="Review tomorrow's hypothesis before any paper test.",
                open_questions=current_goal.open_questions,
                hypothesis=tomorrow_hypothesis,
            )
        )
        _notify(f"Tomorrow's hypothesis saved to {path}.", "success")
    st.caption("No scheduler is enabled here; use the manual buttons and existing gated Run Cycle path.")

    st.subheader("Morning checklist")
    for line in morning_checklist_lines(statuses):
        st.checkbox(line, value=False, key=f"daily_lab_check_{line[:40]}")

    st.subheader("Daily cycle runner")
    team_id = st.selectbox("Team for disabled-autonomy cycle", KNOWN_TEAM_IDS, key="daily_lab_team")
    status = next(item for item in statuses if item.team_id == team_id)
    prompt_text = st.text_area(
        "Disabled-autonomy cycle prompt",
        value=DEFAULT_RUN_CYCLE_PROMPT,
        height=120,
        key="daily_lab_prompt",
    )
    if status.autonomy_enabled:
        st.warning("Disable autonomy before the first Daily Lab smoke run.")
    else:
        st.info("Autonomy is disabled; this smoke run cannot submit paper orders.")
    if st.button("Run disabled-autonomy team cycle"):
        result = run_team_cycle_via_dashboard(
            team_id,
            prompt_text,
            config=config,
            autonomy_enabled=False,
            confirmation_checked=False,
            settings=settings,
        )
        if result.ran:
            st.success("Disabled-autonomy cycle completed through the existing gated path.")
            st.text(result.message)
        else:
            st.error(result.message)

    st.subheader("End-of-day review")
    review_cols = st.columns(2)
    for index, team in enumerate(KNOWN_TEAM_IDS):
        with review_cols[index]:
            st.markdown(f"**{team}**")
            with st.expander("Latest cycle summary", expanded=False):
                try:
                    st.text(build_latest_team_cycle_summary(team, config, settings=settings))
                except Exception as exc:  # pragma: no cover - depends on local runtime files
                    st.caption(f"Unavailable: {exc}")
            with st.expander("Latest report", expanded=False):
                if settings is None:
                    st.caption("Settings unavailable.")
                else:
                    try:
                        st.text(build_team_report_summary(team, settings=settings))
                    except Exception as exc:  # pragma: no cover - depends on local runtime files
                        st.caption(f"Unavailable: {exc}")

    st.subheader("Learning ledger")
    st.caption(no_automatic_changes_notice())
    with st.form("learning_ledger_form"):
        cols = st.columns(2)
        ledger_team = cols[0].selectbox("Team", KNOWN_TEAM_IDS, key="ledger_team")
        decision = cols[1].selectbox(
            "Decision",
            ["no_decision", "promote", "modify", "retest", "retire"],
            key="ledger_decision",
        )
        agent_strategy = st.text_input("Agent / strategy", value="")
        what_happened = st.text_area("What happened", height=80)
        evidence_path = st.text_input("Evidence path", value="")
        result = st.text_area("Result", height=80)
        lesson = st.text_area("Lesson", height=80)
        next_action = st.text_area("Next action", height=80)
        if st.form_submit_button("Add lesson learned"):
            append_learning_ledger_entry(
                LearningLedgerEntry(
                    timestamp=datetime.now(timezone.utc),
                    team=ledger_team,
                    agent_or_strategy=agent_strategy or "unspecified",
                    what_happened=what_happened,
                    evidence_path=evidence_path,
                    result=result,
                    lesson=lesson,
                    next_action=next_action,
                    decision=decision,
                )
            )
            _notify(f"Lesson saved to {DEFAULT_LEARNING_LEDGER_PATH}.", "success")

    with st.expander("Latest learning ledger", expanded=False):
        st.text(read_learning_ledger())
    with st.expander("Memory context available to future Agent Hub work", expanded=False):
        st.text(learning_memory_context())

    score = build_improvement_score(statuses)
    st.subheader("Improvement score")
    cols = st.columns(6)
    cols[0].metric("Proposals", score.proposals_generated)
    cols[1].metric("Risk approved", score.risk_approved)
    cols[2].metric("Review approved", score.review_approved)
    cols[3].metric("Risk accepted", score.deterministic_risk_accepted)
    cols[4].metric("Risk rejected", score.deterministic_risk_rejected)
    cols[5].metric("Paper submitted", score.paper_order_submitted)

    st.subheader("Strategy scorecards")
    scorecard_rows = [
        {
            "team": card.team,
            "strategy": card.strategy,
            "proposals": card.proposals_generated,
            "execution_eligible": card.execution_eligible,
            "risk_approved": card.risk_approved,
            "review_approved": card.review_approved,
            "deterministic_risk_approved": card.deterministic_risk_approved,
            "paper_submitted": card.paper_orders_submitted,
            "paper_blocked": card.paper_orders_blocked,
            "notes": card.rejection_notes,
        }
        for card in build_strategy_scorecards(statuses)
    ]
    st.dataframe(scorecard_rows, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Paper accounts
# ---------------------------------------------------------------------------
def _render_paper_accounts(settings: Settings | None) -> None:
    st.header("Paper accounts")
    st.caption("Read-only. No secrets shown, no order-submit form. Orders only flow through gated team cycles.")
    if settings is None:
        st.warning("Settings/credentials not configured. Add them on the Setup / Secrets page.")
        return
    for team_id in KNOWN_TEAM_IDS:
        with st.container(border=True):
            st.subheader(team_id)
            st.text(build_team_paper_status_summary(team_id, settings=settings))
            st.text(build_team_positions_summary(team_id, settings=settings))


def _render_data_tools(settings: Settings | None) -> None:
    st.header("Data Tools")
    st.caption("Safe data plumbing for agents. No uncontrolled web browsing is enabled in this phase.")
    env = read_env_file(DEFAULT_ENV_PATH)
    statuses = build_data_source_statuses(env)
    st.subheader("Configured data sources")
    st.dataframe(data_source_rows(statuses), use_container_width=True, hide_index=True)

    st.subheader("Paper account / market snapshot context")
    snapshots = [
        collect_team_portfolio_snapshot(team_id, base_settings=settings)
        for team_id in KNOWN_TEAM_IDS
    ]
    st.code(market_snapshot_context(snapshots), language="text")
    st.subheader("Agent prompt rule")
    st.code(agent_market_data_rules(market_snapshot_context(snapshots)), language="text")
    st.info(
        "Hermes/Ollama does not have internet by default. The app must fetch data and pass it "
        "into prompts. Alpaca can provide account, positions, market clock, and market data "
        "depending on API access. News/RSS/SEC adapters should be allowlisted and cached later."
    )


def _render_portfolio_cockpit(
    config: DiscordBotConfig,
    settings: Settings | None,
    statuses: list[TeamStatus],
) -> None:
    st.header("Portfolio Cockpit")
    st.caption("Read-only paper-account view. No order forms live here; orders only flow through gated Run Cycle.")

    snapshots = {
        team_id: collect_team_portfolio_snapshot(team_id, base_settings=settings)
        for team_id in KNOWN_TEAM_IDS
    }

    top = st.columns(4)
    alpha = snapshots["team_alpha"]
    beta = snapshots["team_beta"]
    top[0].metric("Alpha equity", f"${alpha.equity:,.2f}" if alpha.equity is not None else "n/a")
    top[1].metric("Beta equity", f"${beta.equity:,.2f}" if beta.equity is not None else "n/a")
    market_values = [snapshot.market_open for snapshot in snapshots.values() if snapshot.market_open is not None]
    market_label = "open" if any(market_values) else "closed/unknown"
    top[2].metric("Market", market_label)
    top[3].metric("Positions", sum(snapshot.positions_count for snapshot in snapshots.values()))

    comparison = compare_team_portfolios(alpha, beta)
    if comparison.leader:
        st.info(
            f"Team comparison: {comparison.leader} leads by "
            f"${abs(comparison.difference or 0):,.2f}. {comparison.spy_benchmark_status}"
        )
    else:
        st.info("Team comparison unavailable until both paper account snapshots have equity.")

    for status in statuses:
        snapshot = snapshots[status.team_id]
        with st.container(border=True):
            st.subheader(status.team_id)
            cols = st.columns(5)
            cols[0].metric("Equity", f"${snapshot.equity:,.2f}" if snapshot.equity is not None else "n/a")
            cols[1].metric("Cash", f"${snapshot.cash:,.2f}" if snapshot.cash is not None else "n/a")
            cols[2].metric(
                "Buying power",
                f"${snapshot.buying_power:,.2f}" if snapshot.buying_power is not None else "n/a",
            )
            cols[3].metric("Positions", snapshot.positions_count)
            cols[4].metric("Autonomy", "enabled" if status.autonomy_enabled else "disabled")
            st.caption(f"Data freshness: {snapshot.data_freshness.isoformat()}")
            if not snapshot.available:
                st.warning(snapshot.message)

            rows = position_table_rows(snapshot)
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
                alloc = allocation_rows(snapshot)
                if alloc:
                    st.bar_chart(pd.DataFrame(alloc), x="symbol", y="weight_pct")
            else:
                st.info("No current positions, or positions unavailable.")

            with st.expander("Advanced / debug details", expanded=False):
                st.text(build_team_autonomy_status_summary(status.team_id, config))
                if settings is not None:
                    st.text(build_team_paper_status_summary(status.team_id, settings=settings))

    history_paths = list_recent_runtime_files(["data/reports", "data/experiments"], limit=20)
    st.subheader("Daily report and history")
    st.info(portfolio_history_message(history_paths))
    for team_id in KNOWN_TEAM_IDS:
        with st.expander(f"{team_id} latest daily report summary", expanded=False):
            if settings is None:
                st.caption("Settings unavailable; local report lookup skipped.")
            else:
                st.text(build_team_report_summary(team_id, settings=settings))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _render_reports(config: DiscordBotConfig, settings: Settings | None, statuses: list[TeamStatus]) -> None:
    st.header("Reports")
    st.subheader("Approvals (latest cycle)")
    st.table(team_status_table_rows(statuses))

    for team_id in KNOWN_TEAM_IDS:
        with st.expander(f"{team_id} latest team cycle summary"):
            try:
                st.text(build_latest_team_cycle_summary(team_id, config, settings=settings))
            except Exception as exc:  # pragma: no cover - depends on runtime files
                st.caption(f"Unavailable: {exc}")
        if settings is not None:
            with st.expander(f"{team_id} latest daily team report"):
                st.text(build_team_report_summary(team_id, settings=settings))

    st.subheader("Recent proposals")
    proposals = list_recent_runtime_files([DEFAULT_ASK_TEAM_OUTPUT_DIR], limit=15, suffixes=(".json",))
    if proposals:
        for path in proposals:
            st.caption(str(path))
    else:
        st.caption("No saved proposals yet.")

    st.subheader("Equity / history")
    st.info("Equity/history chart pending Phase 7H.2.")


# ---------------------------------------------------------------------------
# Runtime files
# ---------------------------------------------------------------------------
def _render_runtime_files() -> None:
    st.header("Runtime files (read-only)")
    st.caption("These files live under data/ and are never committed. Secrets are redacted; large files truncated.")
    files = list_recent_runtime_files(RUNTIME_BROWSE_DIRS, limit=200)
    if not files:
        st.caption("No runtime files found yet.")
        return
    labels = {f"{path}  —  modified {path.stat().st_mtime:.0f}": path for path in files}
    choice = st.selectbox("Select a file to view", list(labels.keys()))
    selected = labels[choice]
    language = "json" if selected.suffix == ".json" else "text"
    st.code(read_safe_text(selected) or "(empty or unreadable)", language=language)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _render_settings(config: DiscordBotConfig) -> None:
    st.header("Settings")
    st.caption("Safe runtime knobs. Caps/autonomy save to runtime config; scheduling saves to local .env.")

    st.subheader("Recommended first-test settings")
    st.code(recommended_first_test_env_text(), language="dotenv")

    for team_id in KNOWN_TEAM_IDS:
        autonomy = config.autonomy_for(team_id)
        with st.form(f"settings_{team_id}"):
            st.markdown(f"**{team_id}**")
            enabled = st.checkbox("Autonomy enabled", value=autonomy.enabled, key=f"set_en_{team_id}")
            orders = st.number_input(
                "Max paper orders/day", min_value=0, value=int(autonomy.max_paper_orders_per_day), key=f"set_ord_{team_id}"
            )
            notional = st.number_input(
                "Max daily notional ($)", min_value=0.0, value=float(autonomy.max_daily_notional), step=1000.0, key=f"set_not_{team_id}"
            )
            if st.form_submit_button(f"Save {team_id} settings"):
                update_team_runtime_config(
                    team_id,
                    config,
                    enabled=bool(enabled),
                    max_paper_orders_per_day=int(orders),
                    max_daily_notional=float(notional),
                )
                _notify(f"{team_id} settings saved to runtime config.", "success")

    with st.form("settings_schedule"):
        st.markdown("**Scheduled reports (saved to .env)**")
        sched_enabled = st.checkbox(
            "Scheduled team updates enabled", value=config.scheduled_team_updates_enabled
        )
        interval = st.number_input(
            "Report interval (minutes)", min_value=1.0, value=float(config.scheduled_team_update_minutes), step=30.0
        )
        log_channel = st.text_input(
            "Paper trading log channel ID",
            value=str(config.special_channel_ids.get("paper_trading_log", "")),
        )
        if st.form_submit_button("Save scheduling settings"):
            updates = {
                "DISCORD_SCHEDULED_TEAM_UPDATES_ENABLED": "true" if sched_enabled else "false",
                "DISCORD_SCHEDULED_TEAM_UPDATE_MINUTES": str(int(interval)),
            }
            if log_channel.strip():
                updates["DISCORD_PAPER_TRADING_LOG_CHANNEL_ID"] = log_channel.strip()
            result = write_env_updates(updates)
            load_dotenv(result.path, override=True)
            _notify(
                f"Saved scheduling settings to {result.path}. Restart the dashboard to fully apply.",
                "success",
            )


# ---------------------------------------------------------------------------
# Setup / Secrets
# ---------------------------------------------------------------------------
def _render_setup_secrets() -> None:
    st.header("Setup / Secrets")
    st.info(
        "For security, saved secrets are not shown. Blank secret fields keep the current "
        "saved value. Enter a value only to replace it."
    )
    st.caption(
        "Secrets are entered in password fields, saved only to your local (git-ignored) .env, "
        "and never displayed afterward. A timestamped .env backup is written before each save."
    )
    env = read_env_file(DEFAULT_ENV_PATH)

    st.subheader("Configuration status")
    for field in env_setup_status(env):
        badge = _badge("configured", "green") if field.configured else _badge("missing", "yellow")
        st.markdown(f"`{field.key}` " + badge + (f" · {field.display_value}" if not field.is_secret else ""), unsafe_allow_html=True)

    def _save(submitted: dict[str, str], label: str) -> None:
        # Blank inputs are dropped so existing .env values (especially secrets) are preserved.
        cleaned = build_env_updates(submitted)
        if not cleaned:
            _notify("Nothing to save (all fields blank; existing values kept).", "info")
            return
        result = write_env_updates(cleaned)
        load_dotenv(result.path, override=True)
        backup_note = f" Backup: {result.backup_path}" if result.backup_path else ""
        _notify(
            f"Saved {len(result.updated_keys)} {label} value(s) to {result.path}. "
            f"Restart the dashboard to fully apply.{backup_note}",
            "success",
        )

    with st.form("setup_discord"):
        st.markdown("**Discord**")
        token = st.text_input(
            "DISCORD_BOT_TOKEN",
            type="password",
            help=secret_field_status_label("DISCORD_BOT_TOKEN", env),
        )
        ids = {key: st.text_input(key, value=env.get(key, "")) for key in SETUP_DISCORD_KEYS if key != "DISCORD_BOT_TOKEN"}
        if st.form_submit_button("Save Discord settings"):
            updates = {"DISCORD_BOT_TOKEN": token, **ids}
            _save(updates, "Discord")

    with st.form("setup_hermes"):
        st.markdown("**Hermes / Ollama**")
        hermes = {
            "HERMES_ENABLED": "true" if st.checkbox("HERMES_ENABLED", value=env.get("HERMES_ENABLED", "") == "true") else "false",
            "HERMES_BASE_URL": st.text_input("HERMES_BASE_URL", value=env.get("HERMES_BASE_URL", "")),
            "HERMES_MODEL": st.text_input("HERMES_MODEL", value=env.get("HERMES_MODEL", "")),
        }
        if st.form_submit_button("Save Hermes settings"):
            _save(hermes, "Hermes")

    for team_id, prefix in (("team_alpha", "TEAM_ALPHA"), ("team_beta", "TEAM_BETA")):
        with st.form(f"setup_alpaca_{team_id}"):
            st.markdown(f"**Alpaca paper — {team_id}**")
            api_key = st.text_input(
                f"{prefix}_ALPACA_API_KEY",
                type="password",
                key=f"ak_{team_id}",
                help=secret_field_status_label(f"{prefix}_ALPACA_API_KEY", env),
            )
            secret_key = st.text_input(
                f"{prefix}_ALPACA_SECRET_KEY",
                type="password",
                key=f"sk_{team_id}",
                help=secret_field_status_label(f"{prefix}_ALPACA_SECRET_KEY", env),
            )
            base_url = st.text_input(
                f"{prefix}_ALPACA_BASE_URL",
                value=env.get(f"{prefix}_ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
                key=f"bu_{team_id}",
            )
            paper = st.checkbox("Paper mode (must stay true)", value=True, key=f"pp_{team_id}")
            if not paper:
                st.error("Paper mode must remain true. Live trading is not supported.")
            if st.form_submit_button(f"Save {team_id} Alpaca settings"):
                updates = {
                    f"{prefix}_ALPACA_API_KEY": api_key,
                    f"{prefix}_ALPACA_SECRET_KEY": secret_key,
                    f"{prefix}_ALPACA_BASE_URL": base_url,
                    f"{prefix}_ALPACA_PAPER": "true",  # forced paper-only
                }
                _save(updates, f"{team_id} Alpaca")

    st.subheader("Validation")
    if st.button("Check Discord config present"):
        st.success("Discord token present.") if env.get("DISCORD_BOT_TOKEN", "").strip() else st.warning("Discord token missing.")
    if st.button("Check Hermes/Ollama configured"):
        if env.get("HERMES_BASE_URL", "").strip() and env.get("HERMES_MODEL", "").strip():
            st.success("Hermes base URL and model configured.")
        else:
            st.warning("Hermes base URL and/or model missing.")
    settings = _safe_settings()
    if st.button("Check Alpaca paper status (per team)"):
        if settings is None:
            st.warning("Settings unavailable; cannot check Alpaca.")
        else:
            for team_id in KNOWN_TEAM_IDS:
                st.text(build_team_paper_status_summary(team_id, settings=settings))


def _render_setup_wizard() -> None:
    st.header("Setup Wizard")
    st.caption("Beginner-friendly first run. This page checks local config only and never prints secrets.")
    env = read_env_file(DEFAULT_ENV_PATH)

    steps = first_run_step_labels()
    step = st.radio("Step", steps, horizontal=True)

    if step == "Welcome / paper-only warning":
        st.warning("This lab is paper-only. Live trading, short execution, margin execution, and options execution are disabled.")
        st.markdown(
            "- Start with Alpha only.\n"
            "- Keep Beta disabled at first.\n"
            "- Agent Hub chat and proposal modes do not trade.\n"
            "- Run Cycle is the only path that can reach paper orders, and only after all gates pass."
        )
    elif step == "Local requirements":
        st.markdown(
            "- Python installed\n"
            "- Dependencies installed with `pip install -r requirements.txt`\n"
            "- Streamlit installed from requirements\n"
            "- Ollama optional, but needed for local Hermes agent calls\n"
            "- Alpaca paper account keys needed for account views and paper order tests"
        )
    elif step == ".env setup":
        st.info("Use Setup / Secrets to write local `.env` values. Secrets are saved locally and never displayed.")
        st.dataframe(setup_secret_status_rows(env), use_container_width=True, hide_index=True)
    elif step == "Safety caps":
        st.markdown("Recommended first-test settings:")
        st.code("\n".join(f"{key}={value}" for key, value in recommended_safe_updates().items()), language="bash")
        if st.button("Save recommended first-test safety caps"):
            result = write_env_updates(recommended_safe_updates())
            load_dotenv(result.path, override=True)
            _notify(f"Saved recommended first-test settings to {result.path}.", "success")
    elif step == "Validation":
        checks = build_setup_checks(env, env_path=DEFAULT_ENV_PATH)
        st.progress(setup_progress_percent(checks))
        for check in checks:
            (st.success if check.ok else st.warning)(f"{check.label}: {check.message}")
    elif step == "Finish":
        st.markdown(
            "- Start desktop-style app: `python -m src.main app`\n"
            "- Browser fallback: `python -m src.main dashboard`\n"
            "- Optional wrapper install: `pip install pywebview`\n"
            "- Start Discord Bot only if wanted: use the Discord Bot page\n"
            "- No-Discord mode is fine: use Agent Hub, Daily Lab, and Run Cycle from this dashboard\n"
            "- Run disabled-autonomy smoke test from Daily Lab\n"
            "- Market-hours paper test: Alpha only, one controlled run, then disable autonomy\n"
            "- Keep Beta disabled until Alpha is stable"
        )


def _render_hermes_local_ai() -> None:
    st.header("Hermes / Ollama / Local AI")
    env = read_env_file(DEFAULT_ENV_PATH)
    st.markdown(
        """
Hermes is this project's agent runtime adapter layer. In a typical local setup, Hermes calls
an Ollama-served model at a local URL.

Local Ollama usually has no per-message API fee, but it still uses your local CPU/GPU and
electricity. It does not automatically know current market news unless the app feeds it data,
and it does not train itself from chats.

Better teams come from better models, better data, better prompts, better tools, scoring,
evaluation, memory, and human review. Saved lessons can be included in future prompts as
runtime context, but that is not model training.

Agents cannot access the internet unless the app fetches and passes tool/data context. They
may only claim market/news facts that appear in that context. Agent Hub chat is not trading.
Proposal modes are sandbox-routed. Run Cycle is the only path that can reach paper orders,
and only after every safety gate passes.

Future hosted models may have API costs if you configure them. Local Ollama does not remove
the need for good prompts, good evidence, and careful review.
"""
    )
    rows = [
        {"key": "HERMES_ENABLED", "status": "configured" if env.get("HERMES_ENABLED", "").strip() else "missing"},
        {"key": "HERMES_BASE_URL", "status": "configured" if env.get("HERMES_BASE_URL", "").strip() else "missing"},
        {"key": "HERMES_MODEL", "status": "configured" if env.get("HERMES_MODEL", "").strip() else "missing"},
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Help / Safety
# ---------------------------------------------------------------------------
def _render_help() -> None:
    st.header("Help / Safety")
    st.markdown(
        """
**What this project does** — A local research lab where small AI agent teams (via Ollama/Hermes)
propose stock ideas, debate risk/review, and *paper-trade* through Alpaca paper accounts, trying
to beat SPY. Discord and this dashboard are control surfaces.

**What paper trading means** — Simulated orders against Alpaca's paper API using fake money.
No real money, ever. Live trading is disabled.

**What autonomy means** — When a team's autonomy is *enabled*, a run cycle is allowed to attempt
paper orders — but only if every gate below passes.

**Before any order can submit, ALL must be true:**
1. autonomy enabled
2. risk agent approval (`RISK_AGENT_APPROVED: true`)
3. review agent approval (`REVIEW_AGENT_APPROVED: true`)
4. deterministic Python risk approval
5. daily caps (orders/day, daily notional)
6. Alpaca paper-only wrapper

**Why natural chat does not trade** — Chat is conversation only; it never reaches the order path.
The same is true for `!ask_team`, `!ask_agent`, and `!run_tournament`.

**Why short / margin / options are simulation-only** — Those routes are intentionally never
executed; they are for research/simulation only.

**How to run a first market-hours test safely** — Set the recommended first-test settings
(Alpha 1 order/day & $250k cap, Beta 0/$0), keep both autonomy *off* first, run a cycle with
autonomy disabled to confirm "no paper orders submitted", then enable Alpha only and run one
controlled cycle during market hours.

**How to disable everything** — Use the Overview "Disable ALL autonomy" kill switch.

**Where runtime files are stored** — Under `data/` (proposals in `data/agent_runs`, notes in
`data/notes/paper_cycles`, reports in `data/reports`). These are git-ignored and never committed.

**Why `.env` is never committed** — It holds your local credentials. It is git-ignored; the
Setup/Secrets page saves to it locally and backs it up, but it must never be committed.

**Desktop-style app** - Run `python -m src.main app` or `python scripts/launch_desktop_app.py`.
It starts Streamlit on `127.0.0.1` and opens a desktop window when `pywebview` is installed.
If the wrapper is missing, it opens the browser and explains `pip install pywebview`.

**Data and internet** - Hermes/Ollama has no internet by default. The app must fetch account,
market, news, RSS, or SEC data and pass it to agents as tool context. This phase wires the
safe architecture and Alpaca/local runtime status; arbitrary web scraping is not enabled.

**Learning** - Agents do not train their model weights. Learning here means saved goals,
lessons, scorecards, and evidence are fed into future prompts under human-readable controls.

**Normal setup path** - Install Python dependencies, add paper Alpaca keys on Setup / Secrets,
optionally install Ollama and pywebview, run the Setup Wizard validation, then run the Daily
Lab disabled-autonomy smoke test before any controlled Alpha paper test.
"""
    )


# ---------------------------------------------------------------------------
# Discord Bot control
# ---------------------------------------------------------------------------
def _render_discord_bot() -> None:
    st.header("Discord Bot")
    st.caption(
        "Start/stop the local Discord bot without a terminal. The bot reads your local .env; "
        "no secrets are passed on the command line."
    )
    report = build_bot_process_report()

    # PID-file (UI-tracked) status.
    if report.pid_file_running:
        st.markdown("UI-tracked PID: " + _badge(f"running (PID {report.pid_file_pid})", "green"), unsafe_allow_html=True)
    elif report.pid_file_pid is not None:
        st.markdown("UI-tracked PID: " + _badge(f"stale PID {report.pid_file_pid}", "yellow"), unsafe_allow_html=True)
    else:
        st.markdown("UI-tracked PID: " + _badge("none", "gray"), unsafe_allow_html=True)

    # System-wide detection (catches bots started from a terminal or a previous launch).
    if report.detected_pids:
        st.markdown(
            "Detected bot processes: " + _badge(", ".join(str(p) for p in report.detected_pids), "green"),
            unsafe_allow_html=True,
        )
    else:
        st.markdown("Detected bot processes: " + _badge("none", "gray"), unsafe_allow_html=True)

    if report.untracked_running:
        st.warning(
            "The UI did not start a bot, but a Discord bot process is running (likely from a "
            "terminal or a previous launch). Use **Stop all detected** below to terminate it."
        )

    st.markdown("Command run:")
    st.code("python -m src.main discord-bot", language="bash")

    cols = st.columns(4)
    if cols[0].button("Start bot", type="primary"):
        result = start_discord_bot()
        _notify(result.message, "success" if result.ok else "warning")
    if cols[1].button("Stop (saved PID)"):
        result = stop_discord_bot()
        _notify(result.message, "success" if result.ok else "warning")
    if cols[2].button("Stop all detected"):
        result = stop_all_bot_processes()
        _notify(result.message, "success" if result.ok else "warning")
    if cols[3].button("Restart bot"):
        result = restart_discord_bot()
        _notify("Restart attempted. " + result.message, "success" if result.ok else "warning")

    st.info(
        "‘Stop (saved PID)’ stops only the bot this UI launched. ‘Stop all detected’ terminates "
        "every detected `src.main discord-bot` process (on Windows it force-closes the process "
        "tree) — use this if a bot was started from a terminal. Changing .env or Discord settings "
        "requires a bot restart to take effect."
    )

    st.subheader("Bot log (tail)")
    log_text = read_tail(bot_log_path())
    st.code(log_text or "(no log yet — start the bot to create one)", language="text")


# ---------------------------------------------------------------------------
# Agent Hub
# ---------------------------------------------------------------------------
_AGENT_HUB_MODE_LABELS = {
    "Team Chat": TEAM_CHAT_MODE,
    "Agent Chat": AGENT_CHAT_MODE,
    "Ask Team for Proposal": ASK_TEAM_MODE,
    "Ask Agent for Proposal": ASK_AGENT_MODE,
}
_AGENT_SCOPED_HUB_MODES = (AGENT_CHAT_MODE, ASK_AGENT_MODE)


def _agent_hub_evidence(config: DiscordBotConfig, team_id: str, agent_id: str | None, settings: Settings | None):
    """Gather grounded evidence (status + recent files + latest report) for a team/agent."""

    status = collect_team_status(team_id, config, settings=settings)
    recent_proposals = list_recent_runtime_files([DEFAULT_ASK_TEAM_OUTPUT_DIR], limit=5, suffixes=(".json",))
    recent_notes = list_recent_runtime_files([Path(DEFAULT_TEAM_CYCLE_DIR) / team_id], limit=5, suffixes=(".md",))
    reports = list_recent_runtime_files(["data/reports"], limit=1, suffixes=(".md",))
    return build_agent_hub_evidence_context(
        team_id,
        status=status,
        agent_id=agent_id,
        recent_proposal_paths=recent_proposals,
        recent_note_paths=recent_notes,
        latest_report_path=reports[0] if reports else None,
    )


def _agent_hub_reply(
    config: DiscordBotConfig,
    team_id: str,
    mode: str,
    agent_id: str | None,
    prompt: str,
    *,
    settings: Settings | None,
) -> str:
    """Route a hub message to the right backend.

    Chat modes use the grounded conversational path; proposal modes use the existing
    proposal-only helpers (with a blank-prompt guard). None submit orders or run a cycle.
    """

    if mode == TEAM_CHAT_MODE:
        evidence = _agent_hub_evidence(config, team_id, None, settings)
        snapshots = [collect_team_portfolio_snapshot(team_id, base_settings=settings)]
        memory = goals_memory_context([team_id]) + "\n\n" + learning_memory_context(limit=3)
        return team_chat_reply(
            team_id,
            prompt,
            evidence=evidence,
            memory_context=memory,
            data_rules=agent_market_data_rules(market_snapshot_context(snapshots)),
        )
    if mode == AGENT_CHAT_MODE:
        evidence = _agent_hub_evidence(config, team_id, agent_id, settings)
        snapshots = [collect_team_portfolio_snapshot(team_id, base_settings=settings)]
        memory = goals_memory_context([team_id]) + "\n\n" + learning_memory_context(limit=3)
        return agent_chat_reply(
            team_id,
            agent_id,
            prompt,
            evidence=evidence,
            memory_context=memory,
            data_rules=agent_market_data_rules(market_snapshot_context(snapshots)),
        )

    # Proposal modes: block blank input before calling the helper.
    proposal_prompt = validate_proposal_prompt(prompt)
    if mode == ASK_AGENT_MODE:
        return agent_hub_ask_agent(team_id, agent_id, proposal_prompt, registry_path=config.default_registry_path)
    # ASK_TEAM_MODE
    research = default_research_agent(team_id, config.default_registry_path)
    if research is None:
        raise ValueError(f"No active research agent found for {team_id}.")
    strategy_id = getattr(research, "latest_strategy_id", None) or f"{team_id}_hub_v1"
    return agent_hub_ask_team(team_id, research.agent_id, research.role.value, strategy_id, proposal_prompt)


def _render_agent_hub(config: DiscordBotConfig, settings: Settings | None) -> None:
    st.header("Agent Hub")

    controls = st.columns([1, 1, 2])
    team_id = controls[0].selectbox("Team", KNOWN_TEAM_IDS, key="hub_team")
    mode_label = controls[1].radio("Mode", list(_AGENT_HUB_MODE_LABELS.keys()), key="hub_mode")
    mode = _AGENT_HUB_MODE_LABELS[mode_label]
    agent_id = None
    if mode in _AGENT_SCOPED_HUB_MODES:
        agent_id = controls[2].selectbox("Agent", AGENT_HUB_AGENT_IDS.get(team_id, ()), key="hub_agent")

    conversational = mode in (TEAM_CHAT_MODE, AGENT_CHAT_MODE)
    if conversational:
        st.info("Conversational only; no proposals or trades. Answers are grounded in saved runtime files.")
        placeholder = "Message the team…" if mode == TEAM_CHAT_MODE else "Message the agent…"
    else:
        st.warning("Structured proposal-only; no trades placed.")
        placeholder = "Ask for a structured proposal…"

    # Evidence panel: show exactly what the chat is grounded on.
    evidence = _agent_hub_evidence(config, team_id, agent_id, settings)
    with st.expander("Evidence available to this chat", expanded=False):
        st.text(render_evidence_context(evidence))

    history_key = agent_hub_history_key(team_id, mode, agent_id)
    history = get_chat_history(st.session_state, history_key)

    action = st.columns(2)
    if action[0].button("Clear chat"):
        clear_chat_history(st.session_state, history_key)
        _notify("Chat cleared.", "info")
    if action[1].button("Save transcript"):
        if history:
            path = save_agent_hub_transcript(history, agent_hub_transcript_path(history_key))
            _notify(f"Transcript saved to {path}.", "success")
        else:
            _notify("Nothing to save yet.", "info")

    for message in history:
        with st.chat_message(message["role"]):
            st.text(message["content"])

    user_message = st.chat_input(placeholder)
    if user_message:
        append_chat_message(st.session_state, history_key, "user", user_message)
        try:
            reply = _agent_hub_reply(config, team_id, mode, agent_id, user_message, settings=settings)
        except ValueError as exc:
            reply = f"Could not run that: {exc}"
        except Exception as exc:  # pragma: no cover - depends on runtime availability
            reply = f"Agent Hub request failed: {exc}"
        append_chat_message(st.session_state, history_key, "assistant", reply)
        st.rerun()

    note = (
        "Conversational only · no proposals or trades."
        if conversational
        else "Structured proposal-only · no trades placed."
    )
    st.caption(f"{note} Saved transcripts go to data/notes/agent_hub/.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def render() -> None:
    st.set_page_config(page_title=PAGE_TITLE, layout="wide", page_icon="📈")
    template_id = selected_template_id(DEFAULT_TEMPLATE_CONFIG_PATH)
    template = UI_TEMPLATES[template_id]
    _apply_template_style(template_id)
    st.title(PAGE_TITLE)
    st.caption(f"ExaltedFable {template.label}")
    _render_notifications()
    _safety_banner(compact=template.compact_safety_banner)

    config = _load_config()
    settings = _safe_settings()
    statuses = [collect_team_status(team_id, config, settings=settings) for team_id in KNOWN_TEAM_IDS]

    st.sidebar.title(template.label)
    options = template_options()
    option_ids = [option.template_id for option in options]
    selected_label = st.sidebar.selectbox(
        "UI template",
        option_ids,
        index=option_ids.index(template_id),
        format_func=lambda value: UI_TEMPLATES[value].label,
    )
    st.sidebar.caption(UI_TEMPLATES[selected_label].description)
    if selected_label != template_id:
        save_template_selection(selected_label, DEFAULT_TEMPLATE_CONFIG_PATH)
        _notify(f"UI template saved: {UI_TEMPLATES[selected_label].label}", "success")
    if st.sidebar.button("Reset template"):
        reset_template_selection(DEFAULT_TEMPLATE_CONFIG_PATH)
        _notify("UI template reset to Portfolio Cockpit.", "success")
    pages = [
        "Home",
        "Portfolio Cockpit",
        "Overview",
        "Daily Lab",
        "Teams",
        "Agents",
        "Agent Hub",
        "Run Cycle",
        "Data Tools",
        "Paper Accounts",
        "Discord Bot",
        "Reports",
        "Runtime Files",
        "Settings",
        "Setup Wizard",
        "Setup / Secrets",
        "Hermes / Ollama / Local AI",
        "Help / Safety",
    ]
    default_page = template.default_page if template.default_page in pages else pages[0]
    page = st.sidebar.radio("Navigate", pages, index=pages.index(default_page))
    st.sidebar.caption("Local-only · paper-only · no live trading")

    if page == "Home":
        _render_template_home(template_id, config, settings, statuses)
    elif page == "Portfolio Cockpit":
        _render_portfolio_cockpit(config, settings, statuses)
    elif page == "Overview":
        _render_overview(config, settings, statuses)
    elif page == "Daily Lab":
        _render_daily_lab(config, settings, statuses)
    elif page == "Teams":
        _render_teams(config, settings, statuses)
    elif page == "Agents":
        _render_agents(config)
    elif page == "Agent Hub":
        _render_agent_hub(config, settings)
    elif page == "Run Cycle":
        _render_run_cycle(config, settings)
    elif page == "Data Tools":
        _render_data_tools(settings)
    elif page == "Paper Accounts":
        _render_paper_accounts(settings)
    elif page == "Discord Bot":
        _render_discord_bot()
    elif page == "Reports":
        _render_reports(config, settings, statuses)
    elif page == "Runtime Files":
        _render_runtime_files()
    elif page == "Settings":
        _render_settings(config)
    elif page == "Setup Wizard":
        _render_setup_wizard()
    elif page == "Setup / Secrets":
        _render_setup_secrets()
    elif page == "Hermes / Ollama / Local AI":
        _render_hermes_local_ai()
    elif page == "Help / Safety":
        _render_help()


if __name__ == "__main__":
    render()
