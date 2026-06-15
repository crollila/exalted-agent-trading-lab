"""Tests for pure dashboard helpers.

These tests never launch Streamlit and never touch real Discord, Ollama, Alpaca, the
internet, or secrets.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.hermes_runtime import (
    HermesAgentChatResult,
    HermesGenerationResult,
    HermesRuntimeConfig,
)
from src.agents.hermes_strategy_sandbox import load_hermes_sandbox_file
from src.discord_bot.bot import (
    REVIEW_APPROVAL_TOKEN,
    RISK_APPROVAL_TOKEN,
    DiscordBotConfig,
)
from src.ui.dashboard_state import (
    AGENT_HUB_AGENT_IDS,
    ASK_AGENT_MODE,
    ASK_TEAM_MODE,
    DEFAULT_AGENT_HUB_DIR,
    DEFAULT_RUN_CYCLE_PROMPT,
    NOTIFICATIONS_STATE_KEY,
    SAFE_DEFAULT_MAX_DAILY_NOTIONAL,
    SAFE_DEFAULT_MAX_ORDERS_PER_DAY,
    DashboardRunResult,
    active_notifications,
    agent_hub_ask_agent,
    agent_hub_ask_team,
    agent_hub_history_key,
    agent_hub_transcript_path,
    append_chat_message,
    clear_chat_history,
    collect_agent_stats,
    collect_team_status,
    disable_all_autonomy,
    dismiss_notifications,
    find_latest_note_path,
    find_latest_proposal_path,
    get_chat_history,
    is_secret_key,
    list_recent_runtime_files,
    mask_secret,
    push_notification,
    redact_secret_like_text,
    read_safe_text,
    reset_team_to_safe_defaults,
    run_cycle_block_reason,
    run_team_cycle_via_dashboard,
    save_agent_hub_transcript,
    team_status_table_rows,
    update_team_runtime_config,
    validate_proposal_prompt,
)

REGISTRY_PATH = "docs/examples/hermes_team_registry_example.json"
_RUNTIME = HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes")


def _autonomy_config(tmp_path, **env):
    base = {"DISCORD_AUTONOMY_CONFIG_PATH": str(tmp_path / "autonomy.json")}
    base.update(env)
    return DiscordBotConfig.from_env(base)


def _proposal_payload(team_id="team_alpha"):
    return {
        "agent_id": "alpha_research_01",
        "team_id": team_id,
        "strategy_id": "team_alpha_dashboard_v1",
        "agent_role": "research_agent",
        "proposals": [
            {
                "proposal_type": "stock_long",
                "symbol": "MSFT",
                "target_weight": 0.05,
                "estimated_price": 100.0,
                "thesis": "Conservative single stock_long idea for the dashboard test.",
                "confidence": 0.7,
            },
            {
                "proposal_type": "short_stock",
                "symbol": "RISK",
                "target_short_weight": 0.03,
                "estimated_price": 50.0,
                "thesis": "Short research only.",
                "confidence": 0.5,
                "borrow_available_assumption": True,
            },
            {"proposal_type": "unknown_type", "symbol": "NOPE"},
        ],
    }


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------
def test_secret_masking_never_reveals_value():
    assert mask_secret("sk-super-secret-1234") == "********"
    assert "secret" not in mask_secret("sk-super-secret-1234")
    assert mask_secret("") == "(not set)"
    assert mask_secret(None) == "(not set)"
    assert mask_secret("   ") == "(not set)"


def test_is_secret_key_detects_credential_names():
    assert is_secret_key("TEAM_ALPHA_ALPACA_API_KEY")
    assert is_secret_key("DISCORD_BOT_TOKEN")
    assert is_secret_key("alpaca_secret_key")
    assert not is_secret_key("TEAM_ALPHA_MAX_DAILY_NOTIONAL")
    assert not is_secret_key("team_id")


def test_redact_secret_like_text_masks_env_like_values():
    raw = (
        "TEAM_ALPHA_ALPACA_API_KEY=PKABCDEF1234567890\n"
        '"alpaca_secret_key": "verysecretvalue"\n'
        "TEAM_ALPHA_MAX_DAILY_NOTIONAL=250000\n"
        "just a normal line of prose"
    )
    redacted = redact_secret_like_text(raw)
    assert "PKABCDEF1234567890" not in redacted
    assert "verysecretvalue" not in redacted
    assert "********" in redacted
    # Non-secret values and prose are preserved.
    assert "TEAM_ALPHA_MAX_DAILY_NOTIONAL=250000" in redacted
    assert "just a normal line of prose" in redacted


def test_read_safe_text_handles_missing_and_redacts(tmp_path):
    assert read_safe_text(None) is None
    assert read_safe_text(tmp_path / "missing.md") is None

    secret_file = tmp_path / "leaky.md"
    secret_file.write_text("DISCORD_BOT_TOKEN=should-not-render\n", encoding="utf-8")
    contents = read_safe_text(secret_file)
    assert contents is not None
    assert "should-not-render" not in contents
    assert "********" in contents


def test_read_safe_text_truncates_large_files(tmp_path):
    big_file = tmp_path / "big.md"
    big_file.write_text("x" * 5000, encoding="utf-8")
    contents = read_safe_text(big_file, max_chars=100)
    assert contents is not None
    assert "... (truncated)" in contents
    assert len(contents) < 5000


# ---------------------------------------------------------------------------
# Latest proposal / note lookup
# ---------------------------------------------------------------------------
def test_find_latest_proposal_and_notes_with_temp_paths(tmp_path):
    proposal_dir = tmp_path / "agent_runs"
    notes_dir = tmp_path / "paper_cycles"
    team_notes = notes_dir / "team_alpha"
    proposal_dir.mkdir()
    team_notes.mkdir(parents=True)

    older = proposal_dir / "discord_team_alpha_v1_20260101.json"
    newer = proposal_dir / "discord_team_alpha_v1_20260615.json"
    older.write_text(json.dumps(_proposal_payload()), encoding="utf-8")
    newer.write_text(json.dumps(_proposal_payload()), encoding="utf-8")
    # Ensure newer has a strictly later mtime.
    import os
    import time

    os.utime(older, (time.time() - 100, time.time() - 100))

    assert find_latest_proposal_path("team_alpha", proposal_dir) == newer
    assert find_latest_proposal_path("team_beta", proposal_dir) is None

    risk_note = team_notes / "alpha_risk_01_20260615.md"
    review_note = team_notes / "alpha_review_01_20260615.md"
    risk_note.write_text(f"{RISK_APPROVAL_TOKEN}: true", encoding="utf-8")
    review_note.write_text(f"{REVIEW_APPROVAL_TOKEN}: true", encoding="utf-8")

    assert find_latest_note_path(notes_dir, "team_alpha", "risk") == risk_note
    assert find_latest_note_path(notes_dir, "team_alpha", "review") == review_note
    assert find_latest_note_path(notes_dir, "team_alpha", "risk") != review_note
    assert find_latest_note_path(notes_dir, "team_beta", "risk") is None


def test_list_recent_runtime_files_sorted_newest_first(tmp_path):
    directory = tmp_path / "agent_runs"
    directory.mkdir()
    first = directory / "a.json"
    second = directory / "b.md"
    first.write_text("{}", encoding="utf-8")
    second.write_text("note", encoding="utf-8")
    import os
    import time

    os.utime(first, (time.time() - 100, time.time() - 100))

    files = list_recent_runtime_files([directory, tmp_path / "missing"])
    assert files[0] == second
    assert set(files) == {first, second}


# ---------------------------------------------------------------------------
# Team status collection
# ---------------------------------------------------------------------------
def test_collect_team_status_handles_missing_runtime_files(tmp_path):
    config = DiscordBotConfig.from_env(
        {"TEAM_ALPHA_AUTONOMY_ENABLED": "false", "DISCORD_TEAM_ALPHA_CHANNEL_ID": "111"}
    )
    status = collect_team_status(
        "team_alpha",
        config,
        proposal_output_dir=tmp_path / "agent_runs",
        notes_output_dir=tmp_path / "paper_cycles",
    )

    assert status.team_id == "team_alpha"
    assert status.autonomy_enabled is False
    assert status.natural_chat_channel_id == 111
    assert status.latest_proposal_path is None
    assert status.latest_risk_note_path is None
    assert status.latest_review_note_path is None
    assert status.execution_eligible_count == 0
    assert status.simulation_only_count == 0
    assert status.rejected_count == 0
    assert status.risk_approved is False
    assert status.review_approved is False
    assert status.stock_long_eligible is False
    assert status.paper_order_status == "not checked"


def test_collect_team_status_summarizes_split_and_approvals(tmp_path):
    proposal_dir = tmp_path / "agent_runs"
    notes_dir = tmp_path / "paper_cycles"
    team_notes = notes_dir / "team_alpha"
    proposal_dir.mkdir()
    team_notes.mkdir(parents=True)
    (proposal_dir / "discord_team_alpha_v1_20260615.json").write_text(
        json.dumps(_proposal_payload()), encoding="utf-8"
    )
    (team_notes / "alpha_risk_01_20260615.md").write_text(f"{RISK_APPROVAL_TOKEN}: true", encoding="utf-8")
    (team_notes / "alpha_review_01_20260615.md").write_text(
        f"{REVIEW_APPROVAL_TOKEN}: true\n\nLooks good.", encoding="utf-8"
    )

    config = DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"})
    status = collect_team_status(
        "team_alpha",
        config,
        proposal_output_dir=proposal_dir,
        notes_output_dir=notes_dir,
    )

    assert status.execution_eligible_count == 1
    assert status.simulation_only_count == 1
    assert status.rejected_count == 1
    assert status.risk_approved is True
    assert status.review_approved is True
    assert status.stock_long_eligible is True

    rows = team_status_table_rows([status])
    assert rows[0]["team"] == "team_alpha"
    assert rows[0]["exec_eligible"] == 1
    assert rows[0]["stock_long_eligible"] == "yes"


# ---------------------------------------------------------------------------
# Run-cycle confirmation gate
# ---------------------------------------------------------------------------
def test_run_cycle_block_reason_requires_confirmation_when_autonomy_enabled():
    assert run_cycle_block_reason(autonomy_enabled=True, confirmation_checked=False) is not None
    assert run_cycle_block_reason(autonomy_enabled=True, confirmation_checked=True) is None
    assert run_cycle_block_reason(autonomy_enabled=False, confirmation_checked=False) is None
    assert run_cycle_block_reason(autonomy_enabled=False, confirmation_checked=True) is None


def test_run_team_cycle_via_dashboard_blocks_unconfirmed_autonomy_run():
    config = DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "true"})
    calls = []

    def spy_runner(team_id, prompt_text, *, config, **kwargs):
        calls.append((team_id, prompt_text))
        return "RUNNER RAN"

    result = run_team_cycle_via_dashboard(
        "team_alpha",
        DEFAULT_RUN_CYCLE_PROMPT,
        config=config,
        autonomy_enabled=True,
        confirmation_checked=False,
        runner=spy_runner,
    )

    assert isinstance(result, DashboardRunResult)
    assert result.ran is False
    assert "Autonomy is ENABLED" in result.message
    assert calls == []  # runner must not be invoked when blocked


def test_run_team_cycle_via_dashboard_runs_when_confirmed():
    config = DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "true"})
    calls = []

    def spy_runner(team_id, prompt_text, *, config, **kwargs):
        calls.append((team_id, prompt_text))
        return "RUNNER RAN"

    result = run_team_cycle_via_dashboard(
        "team_alpha",
        "Run it.",
        config=config,
        autonomy_enabled=True,
        confirmation_checked=True,
        runner=spy_runner,
    )

    assert result.ran is True
    assert result.message == "RUNNER RAN"
    assert calls == [("team_alpha", "Run it.")]


def test_dashboard_helpers_do_not_call_alpaca_orders_or_database(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("dashboard helpers must not submit Alpaca orders or hit the order DB")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)

    config = DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "true"})

    # Read-only status aggregation must not touch the broker or order DB.
    collect_team_status(
        "team_alpha",
        config,
        proposal_output_dir=tmp_path / "agent_runs",
        notes_output_dir=tmp_path / "paper_cycles",
    )

    # Blocked autonomy run must not invoke the runner (and therefore not the broker).
    blocked = run_team_cycle_via_dashboard(
        "team_alpha",
        DEFAULT_RUN_CYCLE_PROMPT,
        config=config,
        autonomy_enabled=True,
        confirmation_checked=False,
        runner=forbidden,
    )
    assert blocked.ran is False

    # Confirmed run delegates only to the provided runner, never directly to the broker.
    def fake_runner(team_id, prompt_text, *, config, **kwargs):
        return "delegated to gated cycle path"

    allowed = run_team_cycle_via_dashboard(
        "team_alpha",
        DEFAULT_RUN_CYCLE_PROMPT,
        config=config,
        autonomy_enabled=True,
        confirmation_checked=True,
        runner=fake_runner,
    )
    assert allowed.ran is True
    assert allowed.message == "delegated to gated cycle path"


# ---------------------------------------------------------------------------
# Team runtime config updates / kill switch / safe defaults
# ---------------------------------------------------------------------------
def test_update_team_runtime_config_persists_and_reads_back(tmp_path):
    config = _autonomy_config(tmp_path, TEAM_ALPHA_AUTONOMY_ENABLED="false")
    update_team_runtime_config(
        "team_alpha",
        config,
        enabled=True,
        max_paper_orders_per_day=2,
        max_daily_notional=123456.0,
    )
    reloaded = _autonomy_config(tmp_path)
    updated = reloaded.autonomy_for("team_alpha")
    assert updated.enabled is True
    assert updated.max_paper_orders_per_day == 2
    assert updated.max_daily_notional == 123456.0
    # Untouched fields keep safe defaults.
    assert updated.require_risk_agent_approval is True
    assert updated.require_review_agent_approval is True


def test_reset_team_to_safe_defaults(tmp_path):
    config = _autonomy_config(tmp_path, TEAM_ALPHA_AUTONOMY_ENABLED="true")
    reset_team_to_safe_defaults("team_alpha", config)
    reloaded = _autonomy_config(tmp_path)
    alpha = reloaded.autonomy_for("team_alpha")
    assert alpha.enabled is False
    assert alpha.max_paper_orders_per_day == SAFE_DEFAULT_MAX_ORDERS_PER_DAY
    assert alpha.max_daily_notional == SAFE_DEFAULT_MAX_DAILY_NOTIONAL["team_alpha"]

    reset_team_to_safe_defaults("team_beta", config)
    reloaded_beta = _autonomy_config(tmp_path)
    beta = reloaded_beta.autonomy_for("team_beta")
    assert beta.enabled is False
    assert beta.max_daily_notional == SAFE_DEFAULT_MAX_DAILY_NOTIONAL["team_beta"] == 0.0


def test_disable_all_autonomy_kill_switch(tmp_path):
    config = _autonomy_config(
        tmp_path,
        TEAM_ALPHA_AUTONOMY_ENABLED="true",
        TEAM_BETA_AUTONOMY_ENABLED="true",
    )
    paths = disable_all_autonomy(config)
    assert len(paths) == 2

    reloaded = _autonomy_config(tmp_path)
    assert reloaded.autonomy_for("team_alpha").enabled is False
    assert reloaded.autonomy_for("team_beta").enabled is False


# ---------------------------------------------------------------------------
# Agent stats derived from runtime files
# ---------------------------------------------------------------------------
def test_collect_agent_stats_from_runtime_files(tmp_path):
    registry_path = "docs/examples/hermes_team_registry_example.json"
    proposal_dir = tmp_path / "agent_runs"
    notes_dir = tmp_path / "paper_cycles"
    team_notes = notes_dir / "team_alpha"
    proposal_dir.mkdir()
    team_notes.mkdir(parents=True)

    # Research agent produced one proposal file (1 exec-eligible, 1 sim, 1 rejected).
    (proposal_dir / "alpha_research_run.json").write_text(
        json.dumps(_proposal_payload()), encoding="utf-8"
    )
    # Risk + review notes for the latest cycle.
    (team_notes / "alpha_risk_01_20260615.md").write_text(f"{RISK_APPROVAL_TOKEN}: true", encoding="utf-8")
    (team_notes / "alpha_review_01_20260615.md").write_text(f"{REVIEW_APPROVAL_TOKEN}: false", encoding="utf-8")

    stats = {
        stat.agent_id: stat
        for stat in collect_agent_stats(
            "team_alpha",
            registry_path=registry_path,
            proposal_output_dir=proposal_dir,
            notes_output_dir=notes_dir,
        )
    }

    research = stats["alpha_research_01"]
    assert research.role == "research_agent"
    assert research.proposal_files_generated == 1
    assert research.execution_eligible_count == 1
    assert research.simulation_only_count == 1
    assert research.rejected_count == 1
    assert research.is_estimate is True
    assert research.risk_approved is None
    assert research.review_approved is None

    risk = stats["alpha_risk_01"]
    assert risk.role == "risk_agent"
    assert risk.cycles_participated == 1
    assert risk.risk_approved is True

    review = stats["alpha_review_01"]
    assert review.role == "review_agent"
    assert review.review_approved is False


def test_collect_agent_stats_with_no_runtime_files(tmp_path):
    stats = collect_agent_stats(
        "team_alpha",
        registry_path="docs/examples/hermes_team_registry_example.json",
        proposal_output_dir=tmp_path / "agent_runs",
        notes_output_dir=tmp_path / "paper_cycles",
    )
    assert len(stats) == 3
    for stat in stats:
        assert stat.proposal_files_generated == 0
        assert stat.cycles_participated == 0
        assert stat.latest_run_path is None
        assert stat.latest_note_path is None


# ---------------------------------------------------------------------------
# Persistent notifications
# ---------------------------------------------------------------------------
def test_push_and_active_notifications_persist_until_expiry():
    state: dict = {}
    push_notification(state, "Saved settings", level="success", now=100.0, ttl_seconds=8.0)
    # Still active a few seconds later (would have flashed away with a one-frame message).
    active = active_notifications(state, now=104.0)
    assert len(active) == 1
    assert active[0]["message"] == "Saved settings"
    assert active[0]["level"] == "success"
    # Expired after the TTL window — pruned from state.
    assert active_notifications(state, now=110.0) == []
    assert state[NOTIFICATIONS_STATE_KEY] == []


def test_push_notification_normalizes_unknown_level():
    state: dict = {}
    push_notification(state, "msg", level="explode", now=0.0)
    assert active_notifications(state, now=1.0)[0]["level"] == "info"


def test_dismiss_notifications_clears_all():
    state: dict = {}
    push_notification(state, "a", now=0.0)
    push_notification(state, "b", now=0.0)
    assert len(active_notifications(state, now=1.0)) == 2
    dismiss_notifications(state)
    assert active_notifications(state, now=1.0) == []


# ---------------------------------------------------------------------------
# Agent Hub is non-trading
# ---------------------------------------------------------------------------
def test_agent_hub_agent_ids_cover_both_teams():
    assert AGENT_HUB_AGENT_IDS["team_alpha"] == ("alpha_research_01", "alpha_risk_01", "alpha_review_01")
    assert AGENT_HUB_AGENT_IDS["team_beta"] == ("beta_research_01", "beta_risk_01", "beta_review_01")


def test_agent_hub_does_not_call_alpaca_orders_or_database(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Agent Hub must not call Alpaca, order submission, or the order DB")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)

    def fake_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("Risk review: proposal-only; no orders placed.", encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=output_file.read_text(encoding="utf-8"))

    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_proposal_payload()), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    agent_output = agent_hub_ask_agent(
        "team_alpha",
        "alpha_risk_01",
        "Review today's proposal risk.",
        registry_path=REGISTRY_PATH,
        output_dir=tmp_path / "responses",
        runtime_config=_RUNTIME,
        asker=fake_asker,
    )
    assert "proposal only; no trades placed" in agent_output

    team_output = agent_hub_ask_team(
        "team_alpha",
        "alpha_research_01",
        "research_agent",
        "team_alpha_hub_v1",
        "Find one high-conviction stock_long idea.",
        output_dir=tmp_path / "agent_runs",
        runtime_config=_RUNTIME,
        generator=fake_generator,
    )
    assert "proposal only; no trades placed" in team_output


def test_validate_proposal_prompt_blocks_blank_input():
    assert validate_proposal_prompt("  Find a stock idea  ") == "Find a stock idea"
    import pytest

    for blank in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            validate_proposal_prompt(blank)


def test_proposal_mode_passes_typed_prompt_as_non_empty_learning_goal(tmp_path):
    captured = {}

    def capturing_generator(config, request, output_file):
        captured["learning_goal"] = request.learning_goal
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_proposal_payload()), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    typed = "Find one defensive stock_long idea to beat SPY."
    agent_hub_ask_team(
        "team_alpha",
        "alpha_research_01",
        "research_agent",
        "team_alpha_hub_v1",
        typed,
        output_dir=tmp_path / "agent_runs",
        runtime_config=_RUNTIME,
        generator=capturing_generator,
    )
    assert captured["learning_goal"] == typed
    assert captured["learning_goal"].strip() != ""


# ---------------------------------------------------------------------------
# Agent Hub conversational helpers
# ---------------------------------------------------------------------------
def test_agent_hub_history_key_scopes_by_team_mode_agent():
    assert agent_hub_history_key("team_alpha", ASK_TEAM_MODE) == "agent_hub::team_alpha::ask_team"
    assert (
        agent_hub_history_key("team_beta", ASK_AGENT_MODE, "beta_risk_01")
        == "agent_hub::team_beta::ask_agent::beta_risk_01"
    )
    # Human-readable mode labels normalize to the same key.
    assert agent_hub_history_key("team_alpha", "Ask team") == "agent_hub::team_alpha::ask_team"
    # Different agents produce different histories.
    assert agent_hub_history_key("team_alpha", ASK_AGENT_MODE, "alpha_risk_01") != agent_hub_history_key(
        "team_alpha", ASK_AGENT_MODE, "alpha_review_01"
    )


def test_append_get_and_clear_chat_history():
    state: dict = {}
    key = agent_hub_history_key("team_alpha", ASK_TEAM_MODE)
    assert get_chat_history(state, key) == []
    append_chat_message(state, key, "user", "hi")
    append_chat_message(state, key, "assistant", "proposal only; no trades placed")
    history = get_chat_history(state, key)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "proposal only; no trades placed"

    # A different conversation key is independent.
    other = agent_hub_history_key("team_beta", ASK_TEAM_MODE)
    assert get_chat_history(state, other) == []

    clear_chat_history(state, key)
    assert get_chat_history(state, key) == []


def test_agent_hub_transcript_path_uses_ignored_runtime_dir():
    assert DEFAULT_AGENT_HUB_DIR == Path("data/notes/agent_hub")
    key = agent_hub_history_key("team_alpha", ASK_AGENT_MODE, "alpha_risk_01")
    path = agent_hub_transcript_path(key)
    assert path.parent == DEFAULT_AGENT_HUB_DIR
    assert path.suffix == ".md"
    assert "agent_hub" in str(path)


def test_save_agent_hub_transcript_writes_and_redacts(tmp_path):
    key = agent_hub_history_key("team_alpha", ASK_TEAM_MODE)
    history = [
        {"role": "user", "content": "What's the plan?"},
        {"role": "assistant", "content": "Idea. DISCORD_BOT_TOKEN=should-not-render"},
    ]
    path = agent_hub_transcript_path(key, output_dir=tmp_path)
    saved = save_agent_hub_transcript(history, path)
    assert saved.is_file()
    text = saved.read_text(encoding="utf-8")
    assert "What's the plan?" in text
    assert "no trades placed" in text.lower()
    assert "should-not-render" not in text  # secret redacted
    assert "********" in text
