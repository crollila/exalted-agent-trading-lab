"""Arena Command Center theme: scoped CSS + premium header (Phase 7Q).

Original CSS only — deep navy/charcoal base with cyan/white/gold/purple accents,
glass panels, soft glows, status dots, and subtle motion. No external CDN, no
copyrighted assets/logos. The ``arena_css`` builder is pure (returns a string) so it
can be asserted in tests without launching Streamlit.
"""

from __future__ import annotations

from src.ui.arena_components import kill_switch_badge, status_pill_html
from src.ui.navigation import ArenaMode

# Accent palette (kept here so tests can reference exact tokens if needed).
ARENA_COLORS = {
    "bg": "#0b1020",
    "panel": "#121a2e",
    "cyan": "#35e0d8",
    "gold": "#e8b95a",
    "purple": "#9a7bff",
    "alpha": "#35e0d8",  # cyan
    "beta": "#9a7bff",  # purple
    "text": "#e8edf6",
    "muted": "#8a97b0",
    "good": "#3ddc97",
    "warn": "#e8b95a",
    "bad": "#ff6b6b",
}


def arena_css() -> str:
    """Return the scoped Arena stylesheet (wrapped in a <style> tag)."""

    c = ARENA_COLORS
    return f"""
<style>
:root {{
  --arena-bg: {c['bg']};
  --arena-panel: {c['panel']};
  --arena-cyan: {c['cyan']};
  --arena-gold: {c['gold']};
  --arena-purple: {c['purple']};
  --arena-text: {c['text']};
  --arena-muted: {c['muted']};
  --arena-good: {c['good']};
  --arena-warn: {c['warn']};
  --arena-bad: {c['bad']};
}}
.stApp {{
  background:
    radial-gradient(1200px 600px at 12% -8%, rgba(53,224,216,0.10), transparent 60%),
    radial-gradient(1000px 600px at 105% 0%, rgba(154,123,255,0.12), transparent 55%),
    var(--arena-bg);
  color: var(--arena-text);
}}
.arena-header {{
  border-radius: 18px;
  padding: 18px 22px;
  margin-bottom: 14px;
  background: linear-gradient(135deg, rgba(53,224,216,0.10), rgba(154,123,255,0.10));
  border: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 10px 30px rgba(0,0,0,0.35);
  backdrop-filter: blur(6px);
}}
.arena-title {{ font-size: 1.9rem; font-weight: 800; letter-spacing: 0.04em;
  background: linear-gradient(90deg, var(--arena-cyan), #ffffff 55%, var(--arena-purple));
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }}
.arena-subtitle {{ color: var(--arena-muted); font-size: 0.95rem; margin-top: 2px; }}
.arena-badge-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}

.arena-pill {{ display: inline-flex; align-items: center; gap: 7px; font-size: 0.78rem;
  padding: 4px 11px; border-radius: 999px; border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.04); color: var(--arena-text); }}
.arena-pill-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--arena-muted);
  box-shadow: 0 0 8px currentColor; }}
.arena-pill-good {{ color: var(--arena-good); }}
.arena-pill-good .arena-pill-dot {{ background: var(--arena-good); }}
.arena-pill-warn {{ color: var(--arena-warn); }}
.arena-pill-warn .arena-pill-dot {{ background: var(--arena-warn); }}
.arena-pill-bad {{ color: var(--arena-bad); }}
.arena-pill-bad .arena-pill-dot {{ background: var(--arena-bad); animation: arena-blink 1.2s infinite; }}
.arena-pill-neutral {{ color: var(--arena-muted); }}
.arena-pill-paper {{ color: var(--arena-cyan); border-color: rgba(53,224,216,0.4); }}
.arena-pill-paper .arena-pill-dot {{ background: var(--arena-cyan); }}
@keyframes arena-blink {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }} }}

.arena-pill-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }}

.arena-team-card, .arena-metric-card, .arena-llm-card, .arena-orb, .arena-feed,
.arena-scoreboard {{
  background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015));
  border: 1px solid rgba(255,255,255,0.08); border-radius: 16px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.30); }}
.arena-team-card {{ padding: 16px; margin-bottom: 14px; position: relative; overflow: hidden; }}
.arena-team-card::before {{ content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px; }}
.arena-accent-alpha::before {{ background: linear-gradient(90deg, var(--arena-cyan), transparent); }}
.arena-accent-beta::before {{ background: linear-gradient(90deg, var(--arena-purple), transparent); }}
.arena-accent-neutral::before {{ background: linear-gradient(90deg, var(--arena-muted), transparent); }}
.arena-team-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
.arena-team-name {{ font-weight: 800; letter-spacing: 0.06em; font-size: 1.05rem; }}
.arena-rank {{ font-size: 0.74rem; padding: 2px 9px; border-radius: 999px; font-weight: 700;
  background: rgba(232,185,90,0.18); color: var(--arena-gold); }}
.arena-demo-badge {{ font-size: 0.66rem; padding: 2px 8px; border-radius: 6px; font-weight: 700;
  background: rgba(232,185,90,0.16); color: var(--arena-gold); border: 1px dashed rgba(232,185,90,0.5); }}
.arena-team-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin: 10px 0; }}
.arena-team-grid > div {{ display: flex; flex-direction: column; }}
.arena-k {{ color: var(--arena-muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; }}
.arena-v {{ font-weight: 700; font-size: 0.98rem; }}
.arena-team-sub {{ color: var(--arena-muted); font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.05em; margin: 10px 0 4px; }}
.arena-mini {{ text-transform: none; letter-spacing: 0; font-style: italic; }}
.arena-chip-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.arena-chip {{ font-size: 0.74rem; padding: 3px 9px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.03); }}
.arena-chip-good {{ color: var(--arena-good); border-color: rgba(61,220,151,0.35); }}
.arena-chip-bad {{ color: var(--arena-bad); border-color: rgba(255,107,107,0.35); }}
.arena-chip-warn {{ color: var(--arena-warn); border-color: rgba(232,185,90,0.35); }}
.arena-chip-neutral {{ color: var(--arena-muted); }}
.arena-roster-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.arena-roster-pill {{ font-size: 0.72rem; padding: 3px 9px; border-radius: 999px;
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10); }}

.arena-metric-card {{ padding: 14px 16px; }}
.arena-metric-title {{ color: var(--arena-muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; }}
.arena-metric-value {{ font-size: 1.5rem; font-weight: 800; margin-top: 2px; }}
.arena-metric-delta {{ font-size: 0.82rem; font-weight: 700; margin-top: 2px; }}
.arena-delta-up {{ color: var(--arena-good); }}
.arena-delta-down {{ color: var(--arena-bad); }}
.arena-delta-flat {{ color: var(--arena-muted); }}
.arena-metric-caption {{ color: var(--arena-muted); font-size: 0.72rem; margin-top: 4px; }}

.arena-scoreboard {{ display: grid; grid-template-columns: 1fr 1.1fr 1fr; align-items: center;
  padding: 18px; margin-bottom: 14px; }}
.arena-score-side {{ text-align: center; }}
.arena-score-team {{ font-weight: 800; letter-spacing: 0.08em; color: var(--arena-text); }}
.arena-score-val {{ font-size: 1.8rem; font-weight: 800; }}
.arena-score-cap {{ color: var(--arena-muted); font-size: 0.7rem; text-transform: uppercase; }}
.arena-score-eq {{ color: var(--arena-muted); font-size: 0.85rem; margin-top: 4px; }}
.arena-score-center {{ text-align: center; }}
.arena-score-leader {{ font-size: 1.25rem; font-weight: 800; color: var(--arena-gold); }}
.arena-score-lead-detail {{ color: var(--arena-muted); font-size: 0.82rem; }}
.arena-vs {{ margin-top: 8px; font-weight: 800; letter-spacing: 0.2em; color: var(--arena-muted); opacity: 0.6; }}

.arena-orb {{ display: flex; gap: 12px; padding: 12px 14px; margin-bottom: 10px; align-items: flex-start; }}
.arena-orb-core {{ width: 34px; height: 34px; border-radius: 50%; flex: 0 0 auto; margin-top: 2px;
  background: radial-gradient(circle at 30% 30%, #ffffff, var(--arena-cyan));
  box-shadow: 0 0 14px var(--arena-cyan); animation: arena-pulse 2.6s ease-in-out infinite; }}
.arena-accent-beta .arena-orb-core {{ background: radial-gradient(circle at 30% 30%, #ffffff, var(--arena-purple));
  box-shadow: 0 0 14px var(--arena-purple); }}
.arena-orb-good {{ box-shadow: 0 0 16px var(--arena-good) !important; }}
.arena-orb-warn {{ box-shadow: 0 0 16px var(--arena-warn) !important; }}
.arena-orb-bad {{ box-shadow: 0 0 16px var(--arena-bad) !important; }}
@keyframes arena-pulse {{ 0%,100% {{ transform: scale(1); opacity: 0.92; }} 50% {{ transform: scale(1.08); opacity: 1; }} }}
.arena-orb-role {{ font-weight: 700; font-size: 0.92rem; }}
.arena-orb-model {{ color: var(--arena-muted); font-size: 0.72rem; }}
.arena-orb-note {{ color: var(--arena-text); font-size: 0.78rem; margin-top: 3px; }}
.arena-orb-foot {{ color: var(--arena-muted); font-size: 0.66rem; font-style: italic; margin-top: 4px; }}

.arena-feed {{ padding: 8px; }}
.arena-feed-item {{ display: flex; gap: 10px; align-items: baseline; padding: 8px 10px; border-radius: 10px;
  border-left: 3px solid rgba(255,255,255,0.10); margin-bottom: 6px; background: rgba(255,255,255,0.02); }}
.arena-feed-item.arena-accent-alpha {{ border-left-color: var(--arena-cyan); }}
.arena-feed-item.arena-accent-beta {{ border-left-color: var(--arena-purple); }}
.arena-feed-cat {{ font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--arena-muted);
  flex: 0 0 96px; }}
.arena-feed-text {{ font-size: 0.84rem; }}

.arena-llm-card {{ padding: 14px 16px; margin-bottom: 12px; }}

.arena-footer {{ margin-top: 18px; padding: 12px 16px; border-radius: 12px; text-align: center;
  color: var(--arena-muted); font-size: 0.78rem; border: 1px solid rgba(255,255,255,0.06);
  background: rgba(255,255,255,0.02); }}
</style>
"""


def header_html(
    mode: ArenaMode,
    *,
    kill_switch_engaged: bool,
    market_open: bool | None = None,
    cheap_loop_running: bool | None = None,
) -> str:
    """Premium header command bar with paper-only / mode / kill-switch badges."""

    audience_label = "OPERATOR MODE" if mode.is_operator else "DEMO MODE"
    audience_state = "warn" if mode.is_operator else "good"
    density_label = "EXPERT" if mode.is_expert else "SIMPLE"
    ks_label, ks_state = kill_switch_badge(kill_switch_engaged)

    badges = [
        status_pill_html("PAPER-ONLY · NO LIVE TRADING", "paper"),
        status_pill_html(audience_label, audience_state),
        status_pill_html(density_label, "neutral"),
        status_pill_html(ks_label, ks_state),
    ]
    if market_open is not None:
        badges.append(status_pill_html("Market open" if market_open else "Market closed",
                                       "good" if market_open else "neutral"))
    if cheap_loop_running is not None:
        badges.append(status_pill_html("Bot loop running" if cheap_loop_running else "Bot loop stopped",
                                       "good" if cheap_loop_running else "neutral"))

    return (
        '<div class="arena-header">'
        '<div class="arena-title">ExaltedFable Arena</div>'
        '<div class="arena-subtitle">Alpha vs Beta · AI Paper-Trading Competition</div>'
        f'<div class="arena-badge-row">{"".join(badges)}</div>'
        "</div>"
    )


def footer_html() -> str:
    """Safety footer reaffirming the unchanged paper-only guarantees."""

    return (
        '<div class="arena-footer">'
        "Paper-only · No live trading · LLMs never execute orders · "
        "Orders only through the existing gated paper path · Deterministic risk remains authoritative"
        "</div>"
    )


def render_arena_header(st, mode: ArenaMode, **kwargs) -> None:
    st.markdown(header_html(mode, **kwargs), unsafe_allow_html=True)


def render_arena_footer(st) -> None:
    st.markdown(footer_html(), unsafe_allow_html=True)
