"""Tests for pure dashboard helpers.

These tests never launch Streamlit and never touch real Discord, Ollama, Alpaca, the
internet, or secrets.
"""

from __future__ import annotations

import json

from src.discord_bot.bot import (
    REVIEW_APPROVAL_TOKEN,
    RISK_APPROVAL_TOKEN,
    DiscordBotConfig,
)
from src.ui.dashboard_state import (
    DEFAULT_RUN_CYCLE_PROMPT,
    DashboardRunResult,
    collect_team_status,
    find_latest_note_path,
    find_latest_proposal_path,
    is_secret_key,
    list_recent_runtime_files,
    mask_secret,
    redact_secret_like_text,
    read_safe_text,
    run_cycle_block_reason,
    run_team_cycle_via_dashboard,
    team_status_table_rows,
)


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
