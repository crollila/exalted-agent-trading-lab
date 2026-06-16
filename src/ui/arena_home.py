"""The Arena Command Center home page (Phase 7Q).

Three-column command layout: team cards (left), intelligence brief + performance
visual (center), agent orbs + live intelligence feed (right), an Expert-only raw
drawer, and a safety footer. Streamlit-thin: all data comes from the pure helpers in
:mod:`src.ui.arena_data` and all markup from :mod:`src.ui.arena_components`.

Never submits orders, never reads secret values, never bypasses a safety gate.
"""

from __future__ import annotations

from typing import Any

from src.agents.llm_review_agents import team_debate_context
from src.competition.daily_review import load_daily_spy_attribution, load_latest_daily_team_review
from src.learning.strategy_memory import StrategyMemory, strategy_memory_context
from src.safety.kill_switch import read_kill_switch
from src.ui.arena_components import (
    intelligence_feed_html,
    llm_status_cards_html,
    render_agent_orb,
    render_scoreboard,
    render_team_card,
)
from src.ui.arena_data import (
    AGENT_ROSTER,
    ARENA_TEAMS,
    build_demo_snapshot,
    build_intelligence_feed,
    build_team_arena_snapshot,
    build_team_intelligence_brief,
    compute_scoreboard_leader,
    llm_status_cards,
)
from src.ui.arena_theme import render_arena_footer, render_arena_header
from src.ui.navigation import ArenaMode
from src.ui.operator_controls import cheap_loop_status


def _agent_status_for(snapshot, role_key: str) -> tuple[str, str]:
    """Map a roster role to a (status, note) pair from local snapshot state."""

    if role_key == "risk":
        return ("approved" if snapshot.risk_approved else "pending",
                "Risk gate before any paper order.")
    if role_key == "review":
        return ("approved" if snapshot.review_approved else "pending",
                "Review approval required for execution.")
    if role_key == "portfolio_manager":
        if snapshot.pm_decision_type:
            return ("active", f"Decided {snapshot.pm_decision_type}.")
        return ("idle", "Awaiting next cycle.")
    if role_key == "llm_review":
        return ("active", "Advisory critique / debate (no execution).")
    return ("active", "Generates proposals (no execution).")


def _build_snapshots(mode: ArenaMode, settings) -> dict[str, Any]:
    """Build per-team Arena snapshots for the current mode (demo vs operator)."""

    snapshots: dict[str, Any] = {}
    for team_id in ARENA_TEAMS:
        if mode.is_demo:
            snapshots[team_id] = build_demo_snapshot(team_id)
            continue
        portfolio_snapshot = None
        if settings is not None:
            try:
                from src.ui.portfolio_view import collect_team_portfolio_snapshot

                portfolio_snapshot = collect_team_portfolio_snapshot(team_id, base_settings=settings)
            except Exception:  # noqa: BLE001 - degrade to no account data; never crash the page
                portfolio_snapshot = None
        snapshots[team_id] = build_team_arena_snapshot(team_id, portfolio_snapshot=portfolio_snapshot)
    return snapshots


def _market_open(snapshots: dict[str, Any]) -> bool | None:
    for snap in snapshots.values():
        # demo snapshots have no market flag; operator account snapshots may.
        if getattr(snap, "account_available", False):
            return None
    return None


def render_arena(st, *, mode: ArenaMode, settings) -> None:
    """Render the full Arena home page."""

    snapshots = _build_snapshots(mode, settings)
    alpha = snapshots["team_alpha"]
    beta = snapshots["team_beta"]

    kill_engaged = read_kill_switch().engaged
    try:
        loop_running = cheap_loop_status().running
    except Exception:  # noqa: BLE001
        loop_running = None

    render_arena_header(
        st, mode,
        kill_switch_engaged=kill_engaged,
        market_open=None,
        cheap_loop_running=loop_running,
    )

    if mode.is_demo:
        st.info("Demo Mode — figures below are clearly-labeled **sample data**, not a real account. "
                "Switch to Operator Mode for real local runtime state.")

    leader = compute_scoreboard_leader(alpha, beta)
    render_scoreboard(st, alpha, beta, leader)

    left, center, right = st.columns([1.05, 1.15, 1.0], gap="medium")

    # LEFT — team cards.
    with left:
        st.markdown("#### Teams")
        render_team_card(st, alpha)
        render_team_card(st, beta)

    # CENTER — intelligence brief + performance visual + LLM models.
    with center:
        st.markdown("#### Team Intelligence Brief")
        daily_reviews = {tid: _safe_daily_review(tid, mode) for tid in ARENA_TEAMS}
        for team_id in ARENA_TEAMS:
            snap = snapshots[team_id]
            label = "Alpha" if team_id == "team_alpha" else "Beta"
            with st.container():
                st.markdown(f"**{label}**")
                for line in build_team_intelligence_brief(snap, daily_review=daily_reviews.get(team_id)):
                    st.markdown(f"- {line}")

        st.markdown("#### Performance")
        _render_performance_visual(st, mode, alpha, beta)

        st.markdown("#### LLM models")
        st.markdown(llm_status_cards_html(llm_status_cards()), unsafe_allow_html=True)

    # RIGHT — agent orbs + live intelligence feed.
    with right:
        st.markdown("#### Agent Feed")
        for team_id in ARENA_TEAMS:
            snap = snapshots[team_id]
            label = "Alpha" if team_id == "team_alpha" else "Beta"
            st.markdown(f"**{label} agents**")
            for role_key, role_label in AGENT_ROSTER:
                status, note = _agent_status_for(snap, role_key)
                model = ""
                if role_key == "llm_review":
                    model = llm_status_cards().get("critique_model", "")
                render_agent_orb(st, team_id, role_key, role_label, status=status, note=note, model_used=model)

        st.markdown("#### Live Intelligence Feed")
        feed = build_intelligence_feed(
            [alpha, beta],
            daily_reviews={tid: daily_reviews.get(tid) for tid in ARENA_TEAMS},
            limit=10,
        )
        st.markdown(intelligence_feed_html(feed), unsafe_allow_html=True)

    # EXPERT drawer — raw tables/paths/files/memory/reviews/config.
    if mode.is_expert:
        _render_expert_drawer(st, mode, snapshots)

    render_arena_footer(st)


def _safe_daily_review(team_id: str, mode: ArenaMode):
    if mode.is_demo:
        return None
    try:
        return load_latest_daily_team_review(team_id)
    except Exception:  # noqa: BLE001
        return None


def _render_performance_visual(st, mode: ArenaMode, alpha, beta) -> None:
    """Small equity visual. Real data when available; clearly-labeled demo otherwise."""

    import pandas as pd

    have_real = (alpha.equity is not None or beta.equity is not None) and not mode.is_demo
    if mode.is_demo:
        st.caption("DEMO / SAMPLE DATA — illustrative only, not real performance.")
        frame = pd.DataFrame(
            {"equity": [1_000_000, 1_004_000, 1_009_500, 1_012_450],
             "beta": [1_000_000, 1_002_100, 1_004_800, 1_006_110]},
        )
        st.line_chart(frame)
        return
    if have_real:
        frame = pd.DataFrame(
            {"team": ["Alpha", "Beta"], "equity": [alpha.equity or 0.0, beta.equity or 0.0]}
        ).set_index("team")
        st.bar_chart(frame)
        return
    st.caption("No real performance history yet — run a cycle or the cheap loop to collect data.")


def _render_expert_drawer(st, mode: ArenaMode, snapshots: dict[str, Any]) -> None:
    from src.ui.arena_components import safe_truncate_text

    st.divider()
    st.markdown("### Expert drawer")

    with st.expander("Raw team snapshot table", expanded=False):
        st.table([
            {
                "team": s.team_id,
                "equity": s.equity,
                "excess_vs_spy": s.excess_return,
                "rank": s.rank,
                "exec_eligible": s.execution_eligible_count,
                "sim_only": s.simulation_only_count,
                "rejected": s.rejected_count,
                "broker_rej": s.broker_rejected_count,
                "pm_decision": s.pm_decision_type,
                "pm_no_trade": s.pm_no_trade,
                "gate_full_cycle": s.gate_should_run_full_cycle,
                **{f"attrib_{k}": v for k, v in s.attribution.items()},
            }
            for s in snapshots.values()
        ])

    with st.expander("Latest daily review + strategy memory", expanded=False):
        for team_id in ARENA_TEAMS:
            st.markdown(f"**{team_id}**")
            try:
                attribution = load_daily_spy_attribution(team_id)
                st.caption(safe_truncate_text(getattr(attribution, "explanation", "") or "(no attribution)", 300))
            except Exception:  # noqa: BLE001
                st.caption("(no daily attribution)")
            memory = StrategyMemory.load(team_id)
            if memory is not None:
                st.json(strategy_memory_context(team_id))
            else:
                st.caption("(no multi-day strategy memory yet)")

    with st.expander("Advisory team debate (deterministic; no LLM call)", expanded=False):
        for team_id in ARENA_TEAMS:
            debate = team_debate_context(team_id, enabled=True)
            st.markdown(f"**{team_id}** — {debate.get('trade_hold_or_observe', 'n/a')}")
            st.caption(f"Bull: {safe_truncate_text(debate.get('bull_case', ''), 200)}")
            st.caption(f"Bear: {safe_truncate_text(debate.get('bear_case', ''), 200)}")

    with st.expander("LLM routing / review config (model names only)", expanded=False):
        st.json(llm_status_cards())
