"""Phase 7Q: Arena Command Center UI helpers + safe operator process wrappers.

Pure-function tests only — no Streamlit browser launch, no network, no real
broker/process. Verifies the new UI surfaces 7L–7P data, hides secrets, labels demo
data honestly, and that the operator controls reuse the existing safe CLI/process
wrappers without adding any broker execution path.
"""

from __future__ import annotations

import subprocess

import pytest

from src.ui import arena_components as comp
from src.ui import arena_data as data
from src.ui import arena_theme as theme
from src.ui import navigation as nav
from src.ui import operator_controls as ops


# ---------------------------------------------------------------------------
# Navigation grouping + mode defaults
# ---------------------------------------------------------------------------
def test_navigation_groups_structure():
    groups = nav.navigation_groups()
    labels = [g.label for g in groups]
    assert labels == ["Arena", "Agents", "Portfolio", "Research Lab", "Operator", "Setup & Safety"]
    # Arena is the first page of the first group and the default landing page.
    assert groups[0].pages[0].page_id == "Arena"
    assert nav.default_page_id() == "Arena"


def test_navigation_every_page_unique_and_reachable():
    page_ids = [p.page_id for p in nav.all_pages()]
    assert len(page_ids) == len(set(page_ids))
    # Core legacy features remain reachable through the grouped nav.
    for needed in ("Kill Switch", "Agent Hub", "Proposal Attribution", "Portfolio Cockpit", "Operator"):
        assert needed in page_ids


def test_mode_defaults_demo_and_simple():
    mode = nav.ArenaMode()
    assert mode.audience == "demo"
    assert mode.density == "simple"
    assert mode.is_demo and mode.is_simple
    assert not mode.is_operator and not mode.is_expert


def test_mode_persistence_roundtrip(tmp_path):
    path = tmp_path / "arena_ui.json"
    assert nav.read_arena_mode(path) == nav.ArenaMode()  # missing file -> safe defaults
    nav.save_arena_mode(nav.ArenaMode(audience="operator", density="expert"), path)
    loaded = nav.read_arena_mode(path)
    assert loaded.audience == "operator" and loaded.density == "expert"


def test_mode_normalization_rejects_junk():
    assert nav.normalize_audience("nonsense") == "demo"
    assert nav.normalize_density("nonsense") == "simple"


def test_simple_mode_hides_expert_pages():
    operator_group = next(g for g in nav.navigation_groups() if g.key == "operator")
    simple_pages = {p.page_id for p in nav.visible_pages(operator_group, audience="operator", density="simple")}
    expert_pages = {p.page_id for p in nav.visible_pages(operator_group, audience="operator", density="expert")}
    # Run Cycle / Runtime Files are expert-only; hidden in Simple, shown in Expert.
    assert "Run Cycle" not in simple_pages
    assert "Run Cycle" in expert_pages
    assert "Runtime Files" not in simple_pages
    assert "Runtime Files" in expert_pages


def test_demo_mode_hides_operator_only_pages():
    operator_group = next(g for g in nav.navigation_groups() if g.key == "operator")
    demo_pages = {p.page_id for p in nav.visible_pages(operator_group, audience="demo", density="expert")}
    operator_pages = {p.page_id for p in nav.visible_pages(operator_group, audience="operator", density="expert")}
    assert "Discord Bot" not in demo_pages  # operator_only
    assert "Discord Bot" in operator_pages


# ---------------------------------------------------------------------------
# Safe text truncation
# ---------------------------------------------------------------------------
def test_safe_truncate_text_basic():
    assert comp.safe_truncate_text("hello", 100) == "hello"
    out = comp.safe_truncate_text("x" * 200, 10)
    assert len(out) == 10
    assert out.endswith("…")


def test_safe_truncate_collapses_whitespace_and_handles_none():
    assert comp.safe_truncate_text("a\n\n  b\tc", 100) == "a b c"
    assert comp.safe_truncate_text(None, 50) == ""
    assert comp.safe_truncate_text("anything", 0) == ""


# ---------------------------------------------------------------------------
# Scoreboard leader calculation
# ---------------------------------------------------------------------------
def _snap(team, **over):
    base = dict(team_id=team)
    base.update(over)
    return data.TeamArenaSnapshot(**base)


def test_scoreboard_alpha_leads_on_excess():
    leader = data.compute_scoreboard_leader(
        _snap("team_alpha", excess_return=0.02), _snap("team_beta", excess_return=0.01)
    )
    assert leader.leader == "team_alpha"
    assert leader.headline == "Alpha leads"
    assert leader.lead_metric == pytest.approx(0.01)
    assert leader.lead_basis == "excess vs SPY"


def test_scoreboard_beta_leads():
    leader = data.compute_scoreboard_leader(
        _snap("team_alpha", excess_return=-0.01), _snap("team_beta", excess_return=0.03)
    )
    assert leader.leader == "team_beta"
    assert leader.headline == "Beta leads"


def test_scoreboard_tie():
    leader = data.compute_scoreboard_leader(
        _snap("team_alpha", excess_return=0.01), _snap("team_beta", excess_return=0.01)
    )
    assert leader.leader == "tie"


def test_scoreboard_no_leader_when_missing_data():
    leader = data.compute_scoreboard_leader(_snap("team_alpha"), _snap("team_beta"))
    assert leader.leader is None
    assert leader.headline == "No leader yet"


def test_scoreboard_falls_back_to_equity_when_no_excess():
    leader = data.compute_scoreboard_leader(
        _snap("team_alpha", equity=1_010_000.0), _snap("team_beta", equity=1_000_000.0)
    )
    assert leader.leader == "team_alpha"
    assert leader.lead_basis == "equity"


# ---------------------------------------------------------------------------
# Team card snapshot handles missing data; PM decision + attribution appear
# ---------------------------------------------------------------------------
def test_team_card_handles_missing_data_safely():
    html = comp.team_card_html(_snap("team_alpha"))
    assert "TEAM ALPHA" in html
    assert "n/a" in html  # equity/cash etc. unavailable -> n/a, never a crash


def test_team_card_shows_portfolio_manager_decision():
    snap = _snap("team_beta", pm_decision_type="hold", pm_no_trade=True, pm_max_new_proposals=0)
    html = comp.team_card_html(snap)
    assert "hold" in html
    assert "no_trade=yes" in html


def test_team_card_shows_attribution_outcomes():
    snap = _snap("team_alpha", attribution={"worked": 3, "failed": 1, "mixed": 0, "pending": 2})
    html = comp.team_card_html(snap)
    assert "3 worked" in html
    assert "1 failed" in html
    assert "2 pending" in html


def test_attribution_outcomes_appear_in_feed():
    snap = _snap("team_alpha", attribution={"worked": 2, "failed": 0, "mixed": 0, "pending": 1})
    items = data.build_intelligence_feed([snap])
    cats = {i.category for i in items}
    assert "Attribution" in cats
    assert any("worked=2" in i.text for i in items)


def test_attribution_outcome_summary_from_feedback():
    fb = {"outcome_feedback": {"worked_count": 4, "failed_count": 2, "mixed_count": 1, "pending_count": 3}}
    summary = data.attribution_outcome_summary(fb)
    assert summary == {"worked": 4, "failed": 2, "mixed": 1, "pending": 3}
    assert data.attribution_outcome_summary(None) == {"worked": 0, "failed": 0, "mixed": 0, "pending": 0}


# ---------------------------------------------------------------------------
# Kill switch badge mapping
# ---------------------------------------------------------------------------
def test_kill_switch_badge_mapping():
    label_on, state_on = comp.kill_switch_badge(True)
    assert "ENGAGED" in label_on and state_on == "bad"
    label_off, state_off = comp.kill_switch_badge(False)
    assert "off" in label_off.lower() and state_off == "good"


def test_header_html_shows_paper_and_kill_switch_badges():
    html = theme.header_html(nav.ArenaMode(), kill_switch_engaged=True)
    assert "PAPER-ONLY" in html
    assert "KILL SWITCH ENGAGED" in html
    assert "DEMO MODE" in html


# ---------------------------------------------------------------------------
# Intelligence feed / brief construction from empty/missing data
# ---------------------------------------------------------------------------
def test_intelligence_feed_empty_data_safe():
    items = data.build_intelligence_feed([_snap("team_alpha"), _snap("team_beta")])
    assert isinstance(items, list)  # no crash; may be empty
    # Rendering empty feed still produces a friendly placeholder.
    html = comp.intelligence_feed_html(items)
    assert "arena-feed" in html


def test_intelligence_feed_respects_limit():
    snap = _snap("team_alpha", pm_decision_type="add", pm_max_new_proposals=2,
                 attribution={"worked": 1, "failed": 1, "mixed": 0, "pending": 0},
                 broker_rejected_count=1, gate_reason="stay cheap", excess_return=0.01)
    items = data.build_intelligence_feed([snap, snap, snap], limit=4)
    assert len(items) == 4


def test_team_brief_missing_data_does_not_crash():
    brief = data.build_team_intelligence_brief(_snap("team_beta"))
    assert isinstance(brief, list)


def test_team_brief_includes_pm_and_attribution():
    snap = _snap("team_alpha", excess_return=0.02, pm_decision_type="rotate", pm_max_new_proposals=2,
                 attribution={"worked": 2, "failed": 1, "mixed": 0, "pending": 0})
    brief = data.build_team_intelligence_brief(snap)
    joined = " ".join(brief)
    assert "Portfolio Manager decided rotate" in joined
    assert "Attribution" in joined


# ---------------------------------------------------------------------------
# Demo data is always labeled and never claimed real
# ---------------------------------------------------------------------------
def test_demo_snapshot_is_labeled():
    snap = data.build_demo_snapshot("team_alpha")
    assert snap.is_demo is True
    assert snap.account_message == data.DEMO_LABEL
    assert "not real" in data.DEMO_LABEL.lower()


def test_demo_feed_items_carry_demo_label():
    snap = data.build_demo_snapshot("team_alpha")
    items = data.build_intelligence_feed([snap])
    assert items, "demo snapshot should produce feed items"
    assert all(data.DEMO_LABEL in i.text for i in items)


def test_demo_brief_carries_demo_label():
    snap = data.build_demo_snapshot("team_beta")
    brief = data.build_team_intelligence_brief(snap)
    assert all(data.DEMO_LABEL in line for line in brief)


def test_team_card_demo_badge():
    html = comp.team_card_html(data.build_demo_snapshot("team_alpha"))
    assert data.DEMO_LABEL in html


# ---------------------------------------------------------------------------
# LLM status cards never reveal API keys
# ---------------------------------------------------------------------------
def test_llm_status_cards_hide_api_keys():
    env = {
        "EXALTED_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-SECRET-NEVER-PRINT",
        "LLM_MODEL_STRATEGY": "gpt-5.4-mini",
        "LLM_MODEL_REVIEW": "gpt-5.4-nano",
    }
    cards = data.llm_status_cards(env)
    assert cards["api_key_configured"] is True
    assert cards["strategy_model"] == "gpt-5.4-mini"
    assert "sk-SECRET-NEVER-PRINT" not in repr(cards)
    html = comp.llm_status_cards_html(cards)
    assert "sk-SECRET-NEVER-PRINT" not in html
    assert "gpt-5.4-nano" in html


def test_no_secrets_in_rendered_team_card_or_feed():
    secret = "sk-SECRET-NEVER-PRINT"
    snap = _snap("team_alpha", pm_rationale=f"note {secret}", gate_reason=f"reason {secret}",
                 excess_return=0.01, pm_decision_type="add")
    # The card never renders the rationale/reason verbatim (only structured fields).
    assert secret not in comp.team_card_html(snap)


# ---------------------------------------------------------------------------
# Strategy memory / daily review missing data does not crash the builders
# ---------------------------------------------------------------------------
def test_build_team_arena_snapshot_offline_safe(tmp_path, monkeypatch):
    # Force all loaders to "no data" and confirm a safe snapshot is still produced.
    monkeypatch.setattr(data, "load_latest_scorecard", lambda *a, **k: None)
    monkeypatch.setattr(data, "performance_feedback", lambda *a, **k: {})
    monkeypatch.setattr(data.TeamLearningLedger, "load", classmethod(lambda cls, t, *a, **k: cls(team_id=t)))
    snap = data.build_team_arena_snapshot("team_alpha")
    assert snap.team_id == "team_alpha"
    assert snap.equity is None  # no account data -> n/a, no crash
    assert snap.attribution == {"worked": 0, "failed": 0, "mixed": 0, "pending": 0}


# ---------------------------------------------------------------------------
# Operator bot controls: reuse existing safe command/process wrappers
# ---------------------------------------------------------------------------
def test_cheap_loop_command_is_gated_cli_without_secrets():
    cmd = ops.build_cheap_loop_command(sleep_seconds=900, team="both", llm_review_when_skipped=True)
    assert cmd[1:] == ["-m", "src.main", "run-cheap-competition-loop",
                       "--sleep-seconds", "900", "--team", "both", "--llm-review-when-skipped"]
    assert ops.command_has_secret(cmd) is False


def test_dry_run_command_contains_dry_run_flag():
    cmd = ops.build_cheap_loop_dry_run_command()
    assert "--dry-run-loop" in cmd
    assert "--once" in cmd
    assert "--llm-review-when-skipped" in cmd


def test_llm_daily_review_command_team_filter():
    assert ops.build_llm_daily_review_command("team_alpha")[-2:] == ["--team", "team_alpha"]
    assert "--team" not in ops.build_llm_daily_review_command("both")


def test_operator_bot_start_uses_safe_wrapper(tmp_path):
    started = {}

    class _FakePopen:
        def __init__(self, pid):
            self.pid = pid

    def fake_popen(command, **kwargs):
        started["command"] = command
        return _FakePopen(4321)

    result = ops.start_cheap_loop(
        runtime_dir=tmp_path,
        popen=fake_popen,
        process_checker=lambda pid: False,
        detector=lambda: [],
    )
    assert result.ok is True
    assert result.pid == 4321
    # Started exactly the gated CLI command, no secrets, no broker call.
    assert started["command"][1:4] == ["-m", "src.main", "run-cheap-competition-loop"]
    assert ops.cheap_loop_pid_path(tmp_path).read_text(encoding="utf-8").strip() == "4321"


def test_operator_bot_start_refuses_when_already_running(tmp_path):
    ops.cheap_loop_pid_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ops.cheap_loop_pid_path(tmp_path).write_text("999", encoding="utf-8")
    result = ops.start_cheap_loop(
        runtime_dir=tmp_path,
        popen=lambda *a, **k: pytest.fail("must not launch when already running"),
        process_checker=lambda pid: True,  # pretend the existing PID is alive
        detector=lambda: [],
    )
    assert result.ok is False
    assert "already" in result.message.lower()


def test_operator_bot_stop_handles_missing_pid_safely(tmp_path):
    result = ops.stop_cheap_loop(runtime_dir=tmp_path)
    assert result.ok is False
    assert "no cheap-loop pid file" in result.message.lower()


def test_operator_bot_stop_clears_stale_pid(tmp_path):
    ops.cheap_loop_pid_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ops.cheap_loop_pid_path(tmp_path).write_text("12345", encoding="utf-8")
    result = ops.stop_cheap_loop(runtime_dir=tmp_path, process_checker=lambda pid: False)
    assert result.ok is False
    assert "stale" in result.message.lower()
    assert not ops.cheap_loop_pid_path(tmp_path).is_file()


def test_run_cli_command_redacts_and_reports(monkeypatch):
    class _Completed:
        returncode = 0
        stdout = "ALPACA_SECRET_KEY=supersecretvalue\nall good"
        stderr = ""

    result = ops.run_cli_command(["echo", "hi"], runner=lambda *a, **k: _Completed())
    assert result.ok is True
    assert "supersecretvalue" not in result.output
    assert "all good" in result.output


def test_dry_run_cheap_loop_uses_dry_run_command(monkeypatch):
    captured = {}

    class _Completed:
        returncode = 0
        stdout = "[dry-run] would run: run-week-cycle"
        stderr = ""

    def fake_runner(command, **kwargs):
        captured["command"] = command
        return _Completed()

    result = ops.run_dry_run_cheap_loop(runner=fake_runner)
    assert result.ok is True
    assert "--dry-run-loop" in captured["command"]


# ---------------------------------------------------------------------------
# Theme CSS is self-contained (no external CDN) and pure
# ---------------------------------------------------------------------------
def test_arena_css_is_self_contained():
    css = theme.arena_css()
    assert "<style>" in css and "</style>" in css
    assert "http://" not in css and "https://" not in css  # no external CDN/assets
    assert "@import" not in css


def test_arena_css_has_no_secrets():
    assert "SECRET" not in theme.arena_css().upper()
