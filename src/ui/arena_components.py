"""Reusable Arena UI components (Phase 7Q).

Two layers:

* Pure HTML/string builders (no Streamlit import) so the markup can be unit-tested
  offline — these never include secrets and always truncate long text.
* Thin ``render_*`` functions that draw the components with ``st.markdown`` using the
  scoped Arena CSS (no external CDN, no copyrighted assets — original CSS only).

Nothing here trades, reads secrets, or bypasses any safety gate.
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Mapping, Sequence

from src.ui.arena_data import (
    AGENT_ROSTER,
    DEMO_LABEL,
    FeedItem,
    ScoreboardLeader,
    TeamArenaSnapshot,
)

# Pill state -> css class. Keep the vocabulary small and explicit.
_PILL_STATE_CLASS = {
    "good": "arena-pill-good",
    "ok": "arena-pill-good",
    "positive": "arena-pill-good",
    "live": "arena-pill-good",
    "warn": "arena-pill-warn",
    "caution": "arena-pill-warn",
    "pending": "arena-pill-warn",
    "bad": "arena-pill-bad",
    "danger": "arena-pill-bad",
    "negative": "arena-pill-bad",
    "neutral": "arena-pill-neutral",
    "info": "arena-pill-neutral",
    "paper": "arena-pill-paper",
}


def safe_truncate_text(text: object, max_chars: int = 140) -> str:
    """Return a single-line, length-bounded string. Never raises on odd input."""

    raw = "" if text is None else str(text)
    raw = " ".join(raw.split())  # collapse whitespace/newlines (no giant raw logs)
    if max_chars <= 0:
        return ""
    if len(raw) <= max_chars:
        return raw
    if max_chars <= 1:
        return raw[:max_chars]
    return raw[: max_chars - 1].rstrip() + "…"


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _pill_class(state: str) -> str:
    return _PILL_STATE_CLASS.get(str(state).strip().lower(), "arena-pill-neutral")


def status_pill_html(label: str, state: str = "neutral") -> str:
    """A small status pill with a colored dot. Pure HTML."""

    return (
        f'<span class="arena-pill {_pill_class(state)}">'
        f'<span class="arena-pill-dot"></span>{_esc(label)}</span>'
    )


def kill_switch_badge(engaged: bool) -> tuple[str, str]:
    """Map kill-switch state to a (label, pill-state) pair."""

    if engaged:
        return "KILL SWITCH ENGAGED", "bad"
    return "Kill switch off", "good"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2%}"


def metric_card_html(title: str, value: object, *, delta: object | None = None, caption: str | None = None) -> str:
    """A compact metric card. Pure HTML."""

    delta_html = ""
    if delta is not None and str(delta).strip():
        delta_text = str(delta)
        cls = "arena-delta-up" if delta_text.strip().startswith("+") else (
            "arena-delta-down" if delta_text.strip().startswith("-") else "arena-delta-flat"
        )
        delta_html = f'<div class="arena-metric-delta {cls}">{_esc(delta_text)}</div>'
    caption_html = f'<div class="arena-metric-caption">{_esc(caption)}</div>' if caption else ""
    return (
        '<div class="arena-metric-card">'
        f'<div class="arena-metric-title">{_esc(title)}</div>'
        f'<div class="arena-metric-value">{_esc(value)}</div>'
        f"{delta_html}{caption_html}</div>"
    )


def _attrib_chips(attribution: Mapping[str, int]) -> str:
    chips = []
    palette = {"worked": "good", "failed": "bad", "mixed": "warn", "pending": "neutral"}
    for key in ("worked", "failed", "mixed", "pending"):
        count = int(attribution.get(key, 0) or 0)
        chips.append(
            f'<span class="arena-chip arena-chip-{palette[key]}">{count} {key}</span>'
        )
    return '<div class="arena-chip-row">' + "".join(chips) + "</div>"


def team_card_html(snapshot: TeamArenaSnapshot) -> str:
    """Render a full team card as pure HTML. Handles missing data safely (n/a)."""

    label = "TEAM ALPHA" if snapshot.team_id == "team_alpha" else "TEAM BETA"
    accent = "alpha" if snapshot.team_id == "team_alpha" else "beta"
    demo_badge = (
        f'<span class="arena-demo-badge">{_esc(DEMO_LABEL)}</span>' if snapshot.is_demo else ""
    )
    rank_badge = f'<span class="arena-rank">#{snapshot.rank}</span>' if snapshot.rank else ""

    pm_state = "neutral"
    if snapshot.pm_decision_type:
        pm_state = "warn" if snapshot.pm_no_trade else "good"
    pm_text = "n/a"
    if snapshot.pm_decision_type:
        pm_text = (
            f"{snapshot.pm_decision_type} · no_trade={'yes' if snapshot.pm_no_trade else 'no'} · "
            f"max_new={snapshot.pm_max_new_proposals if snapshot.pm_max_new_proposals is not None else 'n/a'}"
        )

    gate_text = "n/a"
    if snapshot.gate_should_run_full_cycle is not None:
        if snapshot.gate_should_run_full_cycle:
            gate_text = "full cycle recommended"
        else:
            gate_text = f"stay cheap (~{snapshot.gate_recommended_wait_minutes}m)"
            if snapshot.gate_recommend_review_only:
                gate_text += " · review-only"

    roster_html = "".join(
        f'<span class="arena-roster-pill">{_esc(name)}</span>' for _key, name in AGENT_ROSTER
    )

    risk_state = "good" if snapshot.risk_approved else "warn"
    review_state = "good" if snapshot.review_approved else "warn"

    return (
        f'<div class="arena-team-card arena-accent-{accent}">'
        f'<div class="arena-team-head"><span class="arena-team-name">{_esc(label)}</span>'
        f"{rank_badge}{demo_badge}</div>"
        f'<div class="arena-pill-row">'
        f'{status_pill_html("risk " + ("ok" if snapshot.risk_approved else "pending"), risk_state)}'
        f'{status_pill_html("review " + ("ok" if snapshot.review_approved else "pending"), review_state)}'
        f'{status_pill_html(snapshot.mode or "mode n/a", "neutral")}'
        "</div>"
        '<div class="arena-team-grid">'
        f'<div><span class="arena-k">Equity</span><span class="arena-v">{_fmt_money(snapshot.equity)}</span></div>'
        f'<div><span class="arena-k">Cash</span><span class="arena-v">{_fmt_money(snapshot.cash)}</span></div>'
        f'<div><span class="arena-k">Buying power</span><span class="arena-v">{_fmt_money(snapshot.buying_power)}</span></div>'
        f'<div><span class="arena-k">Daily P&L</span><span class="arena-v">{_fmt_money(snapshot.daily_pl)}</span></div>'
        f'<div><span class="arena-k">Positions</span><span class="arena-v">'
        f'{snapshot.positions_count if snapshot.positions_count is not None else "n/a"}</span></div>'
        f'<div><span class="arena-k">Excess vs SPY</span><span class="arena-v">{_fmt_pct(snapshot.excess_return)}</span></div>'
        "</div>"
        '<div class="arena-team-sub">Latest proposals</div>'
        f'<div class="arena-chip-row">'
        f'<span class="arena-chip arena-chip-good">{snapshot.execution_eligible_count} exec-eligible</span>'
        f'<span class="arena-chip arena-chip-neutral">{snapshot.simulation_only_count} sim-only</span>'
        f'<span class="arena-chip arena-chip-bad">{snapshot.rejected_count} rejected</span>'
        f'<span class="arena-chip arena-chip-warn">{snapshot.broker_rejected_count} broker-rej</span>'
        "</div>"
        '<div class="arena-team-sub">Attribution outcomes</div>'
        f"{_attrib_chips(snapshot.attribution)}"
        '<div class="arena-team-sub">Portfolio Manager</div>'
        f'<div class="arena-pill-row">{status_pill_html(pm_text, pm_state)}</div>'
        '<div class="arena-team-sub">Cheap-cycle gate</div>'
        f'<div class="arena-pill-row">{status_pill_html(gate_text, "neutral")}</div>'
        '<div class="arena-team-sub">Agent roster <span class="arena-mini">· no direct trade permissions</span></div>'
        f'<div class="arena-roster-row">{roster_html}</div>'
        "</div>"
    )


def scoreboard_html(
    alpha: TeamArenaSnapshot,
    beta: TeamArenaSnapshot,
    leader: ScoreboardLeader,
) -> str:
    """Render the Alpha vs Beta scoreboard header as pure HTML."""

    if leader.leader == "team_alpha":
        winner_class = "arena-accent-alpha"
    elif leader.leader == "team_beta":
        winner_class = "arena-accent-beta"
    else:
        winner_class = "arena-accent-neutral"

    lead_detail = ""
    if leader.lead_metric is not None and leader.lead_basis:
        if leader.lead_basis == "excess vs SPY":
            lead_detail = f"by {leader.lead_metric:+.2%} {leader.lead_basis}"
        else:
            lead_detail = f"by ${leader.lead_metric:,.0f} {leader.lead_basis}"

    def side(snap: TeamArenaSnapshot, name: str) -> str:
        return (
            f'<div class="arena-score-side">'
            f'<div class="arena-score-team">{name}</div>'
            f'<div class="arena-score-val">{_fmt_pct(snap.excess_return)}</div>'
            f'<div class="arena-score-cap">excess vs SPY</div>'
            f'<div class="arena-score-eq">{_fmt_money(snap.equity)}</div>'
            "</div>"
        )

    return (
        f'<div class="arena-scoreboard {winner_class}">'
        f"{side(alpha, 'ALPHA')}"
        '<div class="arena-score-center">'
        f'<div class="arena-score-leader">{_esc(leader.headline)}</div>'
        f'<div class="arena-score-lead-detail">{_esc(lead_detail)}</div>'
        '<div class="arena-vs">VS</div>'
        "</div>"
        f"{side(beta, 'BETA')}"
        "</div>"
    )


def agent_orb_html(team_id: str, role_key: str, role_label: str, *, status: str = "idle",
                   note: str = "", model_used: str = "") -> str:
    """An original glowing-orb agent card (no copyrighted assets). Pure HTML."""

    accent = "alpha" if team_id == "team_alpha" else "beta"
    status_state = {
        "active": "good",
        "approved": "good",
        "pending": "warn",
        "idle": "neutral",
        "blocked": "bad",
    }.get(str(status).strip().lower(), "neutral")
    model_html = f'<div class="arena-orb-model">model: {_esc(model_used)}</div>' if model_used else ""
    note_html = f'<div class="arena-orb-note">{_esc(safe_truncate_text(note, 110))}</div>' if note else ""
    return (
        f'<div class="arena-orb arena-accent-{accent}">'
        f'<div class="arena-orb-core arena-orb-{status_state}"></div>'
        f'<div class="arena-orb-body">'
        f'<div class="arena-orb-role">{_esc(role_label)}</div>'
        f'<div class="arena-orb-status">{status_pill_html(status, status_state)}</div>'
        f"{model_html}{note_html}"
        '<div class="arena-orb-foot">no direct trade permissions</div>'
        "</div></div>"
    )


def intelligence_feed_html(items: Iterable[FeedItem]) -> str:
    """Render the live intelligence feed as compact rows. Pure HTML."""

    rows = []
    for item in items:
        accent = "alpha" if item.team_id == "team_alpha" else (
            "beta" if item.team_id == "team_beta" else "neutral"
        )
        rows.append(
            f'<div class="arena-feed-item arena-accent-{accent}">'
            f'<span class="arena-feed-cat">{_esc(item.category)}</span>'
            f'<span class="arena-feed-text">{_esc(item.text)}</span>'
            "</div>"
        )
    if not rows:
        rows.append('<div class="arena-feed-item arena-accent-neutral">'
                    '<span class="arena-feed-text">No activity yet — run a cycle or the cheap loop.</span></div>')
    return '<div class="arena-feed">' + "".join(rows) + "</div>"


def llm_status_cards_html(cards: Mapping[str, Any]) -> str:
    """Render the LLM routing/review status (model names + bool only; never keys)."""

    key_state = "good" if cards.get("api_key_configured") else "warn"
    items = [
        ("Provider", cards.get("provider", "?")),
        ("Strategy", cards.get("strategy_model", "?")),
        ("Review", cards.get("review_model", "?")),
        ("Critique", cards.get("critique_model", "?")),
        ("Summary", cards.get("summary_model", "?")),
    ]
    chips = "".join(
        f'<span class="arena-chip arena-chip-neutral">{_esc(name)}: {_esc(value)}</span>' for name, value in items
    )
    return (
        '<div class="arena-llm-card">'
        '<div class="arena-team-sub">LLM models (advisory only)</div>'
        f'<div class="arena-chip-row">{chips}</div>'
        f'<div class="arena-pill-row">{status_pill_html("API key configured: " + ("yes" if cards.get("api_key_configured") else "no"), key_state)}</div>'
        "</div>"
    )


# ---------------------------------------------------------------------------
# Streamlit render wrappers (thin; the pure builders above carry the markup)
# ---------------------------------------------------------------------------
def render_status_pill(st, label: str, state: str = "neutral") -> None:
    st.markdown(status_pill_html(label, state), unsafe_allow_html=True)


def render_metric_card(st, title: str, value: object, *, delta: object | None = None, caption: str | None = None) -> None:
    st.markdown(metric_card_html(title, value, delta=delta, caption=caption), unsafe_allow_html=True)


def render_team_card(st, snapshot: TeamArenaSnapshot) -> None:
    st.markdown(team_card_html(snapshot), unsafe_allow_html=True)


def render_scoreboard(st, alpha: TeamArenaSnapshot, beta: TeamArenaSnapshot, leader: ScoreboardLeader) -> None:
    st.markdown(scoreboard_html(alpha, beta, leader), unsafe_allow_html=True)


def render_agent_orb(st, team_id: str, role_key: str, role_label: str, **kwargs: Any) -> None:
    st.markdown(agent_orb_html(team_id, role_key, role_label, **kwargs), unsafe_allow_html=True)


def render_intelligence_feed(st, items: Sequence[FeedItem]) -> None:
    st.markdown(intelligence_feed_html(items), unsafe_allow_html=True)


def render_expert_expander(st, title: str, content: str, *, is_expert: bool) -> None:
    """Raw text/logs only behind an expander, and only in Expert density."""

    if not is_expert:
        return
    with st.expander(title, expanded=False):
        st.code(safe_truncate_text(content, 20000) if content else "(none)")
