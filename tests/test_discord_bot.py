import json
import os
import subprocess
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.agents.hermes_runtime import HermesAgentChatResult, HermesGenerationResult, HermesRuntimeConfig
from src.agents.hermes_strategy_sandbox import load_hermes_sandbox_file
from src.brokers.alpaca_client import PAPER_BASE_URL
from src.config.settings import Settings
from src.db.database import get_connection
from src.discord_bot.bot import (
    AgentApprovalGate,
    DiscordBotConfig,
    REVIEW_APPROVAL_TOKEN,
    RISK_APPROVAL_TOKEN,
    TeamAutonomyConfig,
    build_agent_approval_gate_from_files,
    build_ask_agent_summary,
    build_ask_team_summary,
    build_daily_team_report_now_summary,
    build_disable_autonomy_summary,
    build_enable_autonomy_summary,
    build_latest_agent_run_summary,
    build_latest_team_cycle_summary,
    build_natural_message_response_for_channel,
    build_natural_team_chat_summary,
    build_paper_trade_team_summary,
    build_review_proposals_summary,
    build_run_tournament_summary,
    build_scheduled_team_update_summary,
    build_schedule_reports_status_summary,
    build_status_summary,
    build_team_autonomy_status_summary,
    build_team_paper_cycle_summary,
    build_team_paper_status_summary,
    build_team_positions_summary,
    build_team_report_summary,
    build_teams_summary,
    is_channel_allowed,
    latest_agent_run_path,
    latest_agent_run_path_for_team,
    parse_allowed_channel_ids,
    parse_agent_approval_token,
    parse_special_channel_ids,
    parse_team_autonomy_config,
    parse_team_autonomy_flags,
    parse_team_channel_ids,
    run_discord_bot,
)


def test_missing_token_refuses(capsys):
    config = DiscordBotConfig.from_env({})

    with pytest.raises(SystemExit) as exc:
        run_discord_bot(config)

    assert exc.value.code == 1
    assert "DISCORD_BOT_TOKEN is required" in capsys.readouterr().err


def test_allowed_channel_parsing():
    assert parse_allowed_channel_ids(None) is None
    assert parse_allowed_channel_ids("") is None
    assert parse_allowed_channel_ids("123, 456,123") == frozenset({123, 456})
    assert is_channel_allowed(123, frozenset({123}))
    assert not is_channel_allowed(999, frozenset({123}))
    assert is_channel_allowed(999, None)

    with pytest.raises(ValueError, match="comma-separated integers"):
        parse_allowed_channel_ids("123,nope")


def test_team_channel_and_autonomy_parsing():
    env = {
        "DISCORD_TEAM_ALPHA_CHANNEL_ID": "111",
        "DISCORD_TEAM_BETA_CHANNEL_ID": "222",
        "DISCORD_TOURNAMENT_RESULTS_CHANNEL_ID": "333",
        "DISCORD_STRATEGY_LAB_CHANNEL_ID": "444",
        "DISCORD_PAPER_TRADING_LOG_CHANNEL_ID": "555",
        "TEAM_ALPHA_AUTONOMY_ENABLED": "true",
        "TEAM_BETA_AUTONOMY_ENABLED": "false",
        "TEAM_ALPHA_AUTONOMY_MODE": "paper_stocks_only",
        "TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY": "4",
        "TEAM_ALPHA_MAX_DAILY_NOTIONAL": "75000",
        "TEAM_ALPHA_REQUIRE_RISK_AGENT_APPROVAL": "true",
        "TEAM_ALPHA_REQUIRE_REVIEW_AGENT_APPROVAL": "true",
    }

    assert parse_team_channel_ids(env) == {"team_alpha": 111, "team_beta": 222}
    assert parse_special_channel_ids(env) == {
        "tournament_results": 333,
        "strategy_lab": 444,
        "paper_trading_log": 555,
    }
    assert parse_team_autonomy_flags(env) == {"team_alpha": True, "team_beta": False}
    autonomy_config = parse_team_autonomy_config(env)["team_alpha"]
    assert autonomy_config.enabled
    assert autonomy_config.mode == "paper_stocks_only"
    assert autonomy_config.max_paper_orders_per_day == 4
    assert autonomy_config.max_daily_notional == 75000

    config = DiscordBotConfig.from_env(env)

    assert config.team_for_channel(111) == "team_alpha"
    assert config.team_for_channel(222) == "team_beta"
    assert config.team_for_channel(333) is None
    assert config.autonomy_enabled_for("team_alpha")
    assert not config.autonomy_enabled_for("team_beta")
    assert config.special_channel_ids["paper_trading_log"] == 555


def test_status_output():
    config = DiscordBotConfig.from_env(
        {
            "DISCORD_DEFAULT_REGISTRY": "registry.json",
            "DISCORD_DEFAULT_PROPOSAL": "proposal.json",
            "DISCORD_ALLOWED_CHANNEL_IDS": "123",
            "DISCORD_TEAM_ALPHA_CHANNEL_ID": "123",
            "TEAM_ALPHA_AUTONOMY_ENABLED": "true",
        }
    )

    output = build_status_summary(config)

    assert "ExaltedFable command center: online." in output
    assert "Safe lab mode only. No live trading." in output
    assert "Alpaca paper calls are allowed only through explicit team paper commands." in output
    assert "Paper order submission is allowed only through !paper_trade_team" in output
    assert "registry.json" in output
    assert "proposal.json" in output
    assert "1 allowed channel" in output
    assert "Natural team chat channels: 1 configured." in output
    assert "team_alpha: enabled" in output
    assert "$50,000.00 notional/day" in output


def test_teams_summary():
    output = build_teams_summary("docs/examples/hermes_team_registry_example.json")

    assert "Hermes teams" in output
    assert "team_alpha" in output
    assert "team_beta" in output
    assert "alpha_research_01:research_agent" in output
    assert "alpha_risk_01:risk_agent" in output
    assert "alpha_review_01:review_agent" in output
    assert "beta_research_01:research_agent" in output
    assert "beta_risk_01:risk_agent" in output
    assert "beta_review_01:review_agent" in output
    assert "Registry only; no trading or LLM calls." in output


def test_review_proposals_summary():
    output = build_review_proposals_summary("docs/examples/hermes_strategy_sandbox_example.json")

    assert "Hermes proposal review: team_alpha/alpha_research_01" in output
    assert "paper 1" in output
    assert "short sim 1" in output
    assert "option sim 1" in output
    assert "rejected 1" in output
    assert "No execution approval." in output


def test_run_tournament_summary():
    output = build_run_tournament_summary(
        "docs/examples/hermes_team_registry_example.json",
        [
            "docs/examples/hermes_strategy_sandbox_example.json",
            "docs/examples/hermes_strategy_sandbox_team_beta_example.json",
        ],
    )

    assert "Hermes tournament round" in output
    assert "Winner: team_beta" in output
    assert "#1 team_beta" in output
    assert "no trading or execution approval" in output


def test_run_tournament_latest_uses_most_recent_agent_run(tmp_path):
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(_generated_payload(_request("old_strategy"))), encoding="utf-8")
    new_path.write_text(json.dumps(_generated_payload(_request("new_strategy"))), encoding="utf-8")
    os.utime(old_path, (1, 1))
    os.utime(new_path, (2, 2))

    assert latest_agent_run_path(tmp_path) == new_path
    assert str(new_path) in build_latest_agent_run_summary(tmp_path)

    output = build_run_tournament_summary(
        "docs/examples/hermes_team_registry_example.json",
        [new_path],
    )

    assert "Hermes tournament round" in output
    assert "Winner: team_alpha" in output


def test_ask_team_requires_hermes_runtime_config(tmp_path):
    with pytest.raises(RuntimeError, match="disabled"):
        build_ask_team_summary(
            "team_alpha",
            "alpha_research_01",
            "research_agent",
            "team_alpha_discord_v1",
            "Find a high-conviction strategy for tomorrow",
            output_dir=tmp_path,
            runtime_config=HermesRuntimeConfig(enabled=False, base_url="http://127.0.0.1:11434/v1", model="hermes"),
            generator=_forbidden_generator,
        )


def test_ask_team_calls_runtime_saves_and_returns_route_counts(tmp_path):
    calls = []

    def fake_generator(config, request, output_file):
        calls.append((config, request, output_file))
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    output = build_ask_team_summary(
        "team_alpha",
        "alpha_research_01",
        "research_agent",
        "team_alpha_discord_v1",
        "Find a high-conviction strategy for tomorrow",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
    )

    assert len(calls) == 1
    _, request, output_file = calls[0]
    assert request.learning_goal == "Find a high-conviction strategy for tomorrow"
    assert output_file.exists()
    assert output_file.parent == tmp_path
    assert json.loads(output_file.read_text(encoding="utf-8"))["strategy_id"] == "team_alpha_discord_v1"
    assert "Saved file:" in output
    assert str(output_file) in output
    assert "Team ID: team_alpha" in output
    assert "Agent ID: alpha_research_01" in output
    assert "Strategy ID: team_alpha_discord_v1" in output
    assert "paper 1" in output
    assert "short sim 1" in output
    assert "option sim 1" in output
    assert "margin sim 1" in output
    assert "rejected 1" in output
    assert "proposal only; no trades placed" in output


def test_team_paper_status_and_positions_use_team_credentials(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)
    fake_client = _FakeTradingClient(
        account=SimpleNamespace(equity="12000", cash="3000", buying_power="9000"),
        positions=[
            SimpleNamespace(
                symbol="MSFT",
                qty="2",
                market_value="850",
                cost_basis="800",
                unrealized_pl="50",
            )
        ],
    )
    settings = _settings(tmp_path)

    status = build_team_paper_status_summary(
        "team_alpha",
        settings=settings,
        client_factory=lambda _settings: fake_client,
    )
    positions = build_team_positions_summary(
        "team_alpha",
        settings=settings,
        client_factory=lambda _settings: fake_client,
    )

    assert "Equity: $12,000.00" in status
    assert "Cash: $3,000.00" in status
    assert "Buying power: $9,000.00" in status
    assert "Market: open" in status
    assert "Positions count: 1" in status
    assert "MSFT: qty 2" in positions
    assert "market value $850.00" in positions
    assert "unrealized P/L $50.00" in positions


def test_team_paper_status_missing_credentials_is_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_API_KEY", "")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_SECRET_KEY", "")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_PAPER", "")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_BASE_URL", "")

    output = build_team_paper_status_summary("team_alpha", settings=_settings(tmp_path))

    assert "paper status unavailable" in output
    assert "credentials are not configured" in output


def test_ask_agent_uses_runtime_and_saves_response(tmp_path):
    calls = []

    def fake_asker(config, request, output_file):
        calls.append((config, request, output_file))
        output_file.write_text("Risk review: proposal-only; no orders placed.", encoding="utf-8")
        return HermesAgentChatResult(
            output_file=output_file,
            response_text=output_file.read_text(encoding="utf-8"),
        )

    output = build_ask_agent_summary(
        "team_alpha",
        "alpha_risk_01",
        "Review today's proposal risk.",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        asker=fake_asker,
    )

    assert len(calls) == 1
    assert calls[0][1].agent_role == "risk_agent"
    assert calls[0][2].exists()
    assert "Risk review" in output
    assert "proposal only; no trades placed" in output


def test_natural_team_chat_asks_all_three_team_agents_and_saves_notes(tmp_path):
    calls = []

    def fake_asker(config, request, output_file):
        calls.append((request, output_file))
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(f"{request.agent_role} response", encoding="utf-8")
        return HermesAgentChatResult(
            output_file=output_file,
            response_text=output_file.read_text(encoding="utf-8"),
        )

    output = build_natural_team_chat_summary(
        "team_alpha",
        "Nate",
        "What should we learn from today's SPY tape?",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        asker=fake_asker,
    )

    assert len(calls) == 3
    assert [call[0].agent_role for call in calls] == ["research_agent", "risk_agent", "review_agent"]
    assert all(call[1].exists() for call in calls)
    assert "Team Alpha agent team" in output
    assert "Alpha Research: research_agent response" in output
    assert "Alpha Risk: risk_agent response" in output
    assert "Alpha Review: review_agent response" in output
    assert "Reminder: proposal only; no trades placed" in output


def test_natural_message_in_team_alpha_channel_routes_to_team_alpha(tmp_path):
    calls = []

    def fake_asker(config, request, output_file):
        calls.append(request)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(f"{request.team_id} {request.agent_role}", encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=output_file.read_text(encoding="utf-8"))

    output = build_natural_message_response_for_channel(
        DiscordBotConfig.from_env({"DISCORD_TEAM_ALPHA_CHANNEL_ID": "111", "DISCORD_ALLOWED_CHANNEL_IDS": "111,222"}),
        111,
        "Nate",
        "Team Alpha, what are you thinking for tomorrow?",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        asker=fake_asker,
    )

    assert output is not None
    assert "Team Alpha agent team" in output
    assert {call.team_id for call in calls} == {"team_alpha"}
    assert [call.agent_role for call in calls] == ["research_agent", "risk_agent", "review_agent"]


def test_natural_message_in_team_beta_channel_routes_to_team_beta(tmp_path):
    calls = []

    def fake_asker(config, request, output_file):
        calls.append(request)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(f"{request.team_id} {request.agent_role}", encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=output_file.read_text(encoding="utf-8"))

    output = build_natural_message_response_for_channel(
        DiscordBotConfig.from_env({"DISCORD_TEAM_BETA_CHANNEL_ID": "222", "DISCORD_ALLOWED_CHANNEL_IDS": "111,222"}),
        222,
        "Nate",
        "Team Beta, what are you thinking for tomorrow?",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        asker=fake_asker,
    )

    assert output is not None
    assert "Team Beta agent team" in output
    assert {call.team_id for call in calls} == {"team_beta"}
    assert [call.agent_role for call in calls] == ["research_agent", "risk_agent", "review_agent"]


def test_natural_chat_does_not_execute_trades(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("natural chat must not touch Alpaca, orders, or database")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)

    output = build_natural_message_response_for_channel(
        DiscordBotConfig.from_env({"DISCORD_TEAM_ALPHA_CHANNEL_ID": "111"}),
        111,
        "Nate",
        "Can we trade tomorrow?",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        asker=lambda config, request, output_file: HermesAgentChatResult(
            output_file=output_file,
            response_text=f"{request.agent_role}: proposal only",
        ),
    )

    assert "Reminder: proposal only; no trades placed" in output


def test_team_autonomy_status_and_scheduled_update_are_non_executing(tmp_path):
    config = DiscordBotConfig.from_env(
        {
            "DISCORD_TEAM_ALPHA_CHANNEL_ID": "111",
            "TEAM_ALPHA_AUTONOMY_ENABLED": "true",
            "TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY": "4",
            "TEAM_ALPHA_MAX_DAILY_NOTIONAL": "75000",
            "DISCORD_AUTONOMY_CONFIG_PATH": str(tmp_path / "autonomy.json"),
        }
    )
    proposal_file = tmp_path / "latest.json"
    proposal_file.write_text(json.dumps(_generated_payload(_request())), encoding="utf-8")

    status = build_team_autonomy_status_summary("team_alpha", config)
    scheduled = build_scheduled_team_update_summary("team_alpha", config, output_dir=tmp_path)

    assert "Autonomy: enabled" in status
    assert "Mode: paper_stocks_only" in status
    assert "Max paper orders/day: 4" in status
    assert "Max daily notional: $75,000.00" in status
    assert RISK_APPROVAL_TOKEN in status
    assert REVIEW_APPROVAL_TOKEN in status
    assert "deterministic Python risk approval required" in status
    assert str(proposal_file) in scheduled
    assert "scheduled update only; no trades placed" in scheduled


def test_enable_and_disable_autonomy_write_ignored_runtime_config(tmp_path):
    config = DiscordBotConfig.from_env(
        {
            "DISCORD_AUTONOMY_CONFIG_PATH": str(tmp_path / "team_autonomy_config.json"),
            "TEAM_ALPHA_MAX_PAPER_ORDERS_PER_DAY": "2",
            "TEAM_ALPHA_MAX_DAILY_NOTIONAL": "25000",
        }
    )

    enabled = build_enable_autonomy_summary("team_alpha", config)
    assert "team_alpha autonomy enabled" in enabled
    assert config.autonomy_for("team_alpha").enabled

    disabled = build_disable_autonomy_summary("team_alpha", config)
    assert "team_alpha autonomy disabled" in disabled
    assert not config.autonomy_for("team_alpha").enabled


def test_schedule_reports_status_is_manual_scaffold():
    config = DiscordBotConfig.from_env({"DISCORD_PAPER_TRADING_LOG_CHANNEL_ID": "999"})

    output = build_schedule_reports_status_summary(config)

    assert "Scheduled report scaffold" in output
    assert "Paper trading log channel: 999" in output
    assert "Use !daily_team_report_now" in output


def test_daily_team_report_now_summarizes_team_status_positions_and_latest_runs(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)
    _set_team_beta_env(monkeypatch)
    fake_client = _FakeTradingClient(
        account=SimpleNamespace(equity="10000", cash="9000", buying_power="9000"),
        positions=[],
    )
    alpha_path = tmp_path / "alpha.json"
    beta_path = tmp_path / "beta.json"
    alpha_path.write_text(json.dumps(_generated_payload(_request())), encoding="utf-8")
    beta_request = SimpleNamespace(
        agent_id="beta_research_01",
        team_id="team_beta",
        strategy_id="team_beta_discord_v1",
        agent_role="research_agent",
        strategy_notes="Discord test.",
        learning_goal="Find a high-conviction strategy for tomorrow",
    )
    beta_path.write_text(json.dumps(_generated_payload(beta_request)), encoding="utf-8")

    output = build_daily_team_report_now_summary(
        DiscordBotConfig.from_env({}),
        settings=_settings(tmp_path),
        client_factory=lambda _settings: fake_client,
        output_dir=tmp_path,
    )

    assert "Daily team paper report" in output
    assert "Team Alpha paper status:" in output
    assert "Team Beta paper status:" in output
    assert f"- team_alpha: {alpha_path}" in output
    assert f"- team_beta: {beta_path}" in output
    assert "Reminder: paper only, no live trading" in output


def test_team_paper_cycle_stops_when_autonomy_disabled(tmp_path):
    calls = []

    def fake_generator(config, request, output_file):
        calls.append(("generator", request, output_file))
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def fake_asker(config, request, output_file):
        calls.append(("asker", request, output_file))
        output_file.parent.mkdir(parents=True, exist_ok=True)
        token = RISK_APPROVAL_TOKEN if request.agent_role == "risk_agent" else REVIEW_APPROVAL_TOKEN
        output_file.write_text(f"Looks acceptable for Python risk.\n{token}: true", encoding="utf-8")
        return HermesAgentChatResult(
            output_file=output_file,
            response_text=output_file.read_text(encoding="utf-8"),
        )

    output = build_team_paper_cycle_summary(
        "team_alpha",
        "Prepare a conservative SPY-beating paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=fake_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert len(calls) == 3
    assert "Proposal routing split: execution_eligible_proposals 1, simulation_only_proposals 3, rejected_proposals 1." in output
    assert "Risk agent approval: yes" in output
    assert "Review agent approval: yes" in output
    assert "Risk note saved:" in output
    assert "Review note saved:" in output
    assert "stock_long subset eligible to proceed to deterministic Python risk review: yes" in output
    assert "Autonomy: disabled" in output
    assert "No paper orders submitted." in output


def test_team_paper_cycle_risk_prompt_requires_exact_approval_token(tmp_path):
    risk_prompts = []

    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def fake_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if request.agent_role == "risk_agent":
            risk_prompts.append(request.prompt_text)
            text = f"Risk handoff approved.\n{RISK_APPROVAL_TOKEN}: true"
        else:
            text = f"Review handoff approved.\n{REVIEW_APPROVAL_TOKEN}: true"
        output_file.write_text(text, encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=text)

    build_team_paper_cycle_summary(
        "team_alpha",
        "Prepare a conservative SPY-beating paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=fake_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert len(risk_prompts) == 1
    prompt = risk_prompts[0]
    assert "End your response with exactly one of:" in prompt
    assert f"{RISK_APPROVAL_TOKEN}: true" in prompt
    assert f"{RISK_APPROVAL_TOKEN}: false" in prompt
    assert "execution_eligible_proposals:" in prompt
    assert "simulation_only_proposals:" in prompt
    assert "rejected_proposals:" in prompt
    assert "only stock_long proposals are execution-eligible in this phase" in prompt
    assert "you may approve the stock_long execution-eligible subset" in prompt
    assert "Do not reject the executable stock_long subset merely because" in prompt
    assert "proposals.0 stock_long route=paper_eligible_stock_long; symbol=MSFT" in prompt
    assert "proposals.1 short_stock route=simulation_only_short" in prompt
    assert "market-price/risk-engine check can still happen later" in prompt
    # Phase 7G.3 deterministic reviewer checklist and anti-hallucination guardrails.
    assert "Deterministic reviewer checklist" in prompt
    assert "execution-eligible stock_long count: 1" in prompt
    assert "simulation-only count: 3" in prompt
    assert "rejected count: 1" in prompt
    assert (
        "proposals.0: symbol=MSFT; target_weight=0.05; estimated_price=420.5; "
        "thesis present: yes; confidence present: yes; proposal_type is stock_long: yes; "
        "sandbox route is paper: yes"
    ) in prompt
    assert "Review only the execution-eligible stock_long subset." in prompt
    assert "Do not say thesis is missing when the checklist says thesis present: yes." in prompt
    assert "Do not say confidence is missing when the checklist says confidence present: yes." in prompt
    assert "This approval is not execution; deterministic Python risk still decides final order approval." in prompt


def test_team_paper_cycle_review_prompt_requires_exact_approval_token(tmp_path):
    review_prompts = []

    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def fake_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if request.agent_role == "risk_agent":
            text = f"Risk handoff approved.\n{RISK_APPROVAL_TOKEN}: true"
        else:
            review_prompts.append(request.prompt_text)
            text = f"Review handoff approved.\n{REVIEW_APPROVAL_TOKEN}: true"
        output_file.write_text(text, encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=text)

    build_team_paper_cycle_summary(
        "team_alpha",
        "Prepare a conservative SPY-beating paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=fake_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert len(review_prompts) == 1
    prompt = review_prompts[0]
    assert "End your response with exactly one of:" in prompt
    assert f"{REVIEW_APPROVAL_TOKEN}: true" in prompt
    assert f"{REVIEW_APPROVAL_TOKEN}: false" in prompt
    assert f"risk agent gave {RISK_APPROVAL_TOKEN}: true" in prompt
    assert "Parsed risk agent approval: yes" in prompt
    assert "review only execution_eligible_proposals for paper execution" in prompt
    assert "do not reject the whole cycle just because simulation_only_proposals or rejected_proposals exist" in prompt
    assert "reject if there is no valid stock_long execution-eligible proposal" in prompt
    assert "Approval means: the stock_long execution-eligible subset may proceed to deterministic Python risk review" in prompt
    assert "proposals.0 stock_long route=paper_eligible_stock_long; symbol=MSFT" in prompt
    assert "proposals.2 option_long route=simulation_only_option" in prompt
    assert "deterministic Python risk review" in prompt
    # Phase 7G.3 deterministic reviewer checklist, parsed risk approval, and risk note text.
    assert "Deterministic reviewer checklist" in prompt
    assert "execution-eligible stock_long count: 1" in prompt
    assert (
        "proposals.0: symbol=MSFT; target_weight=0.05; estimated_price=420.5; "
        "thesis present: yes; confidence present: yes; proposal_type is stock_long: yes; "
        "sandbox route is paper: yes"
    ) in prompt
    assert "Risk note text:" in prompt
    assert "Risk handoff approved." in prompt
    assert "Review approval requires parsed risk agent approval yes." in prompt
    assert (
        "Do not invent missing fields; if the checklist says thesis present: yes or "
        "confidence present: yes, treat them as present."
    ) in prompt


def test_team_paper_cycle_does_not_count_agent_tokens_without_execution_eligible_subset(tmp_path):
    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(
                {
                    "agent_id": request.agent_id,
                    "team_id": request.team_id,
                    "strategy_id": request.strategy_id,
                    "agent_role": request.agent_role,
                    "proposals": [
                        {
                            "proposal_type": "short_stock",
                            "symbol": "RISK",
                            "target_short_weight": 0.03,
                            "estimated_price": 50,
                            "thesis": "Short research only.",
                            "confidence": 0.5,
                            "borrow_available_assumption": True,
                        },
                        {
                            "proposal_type": "margin",
                            "requested_gross_exposure": 1.2,
                            "symbols": ["MSFT", "SPY"],
                            "thesis": "Margin research only.",
                            "confidence": 0.55,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def approving_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        token = RISK_APPROVAL_TOKEN if request.agent_role == "risk_agent" else REVIEW_APPROVAL_TOKEN
        text = f"Agent emitted an approval token anyway.\n{token}: true"
        output_file.write_text(text, encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=text)

    output = build_team_paper_cycle_summary(
        "team_alpha",
        "Prepare a conservative SPY-beating paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "true"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=approving_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert "Proposal routing split: execution_eligible_proposals 0, simulation_only_proposals 2, rejected_proposals 0." in output
    assert "Risk agent approval: no" in output
    assert "Review agent approval: no" in output
    assert "stock_long subset eligible to proceed to deterministic Python risk review: no" in output
    assert "No paper orders submitted." in output


def test_agent_approval_parser_recognizes_exact_true_false_tokens_only():
    assert parse_agent_approval_token(f"Looks okay.\n{RISK_APPROVAL_TOKEN}: true", RISK_APPROVAL_TOKEN) is True
    assert parse_agent_approval_token(f"No: short execution.\n{RISK_APPROVAL_TOKEN}: false", RISK_APPROVAL_TOKEN) is False
    assert parse_agent_approval_token("I approve this for risk review.", RISK_APPROVAL_TOKEN) is None
    assert parse_agent_approval_token(f"{RISK_APPROVAL_TOKEN}: yes", RISK_APPROVAL_TOKEN) is None
    # Token may lead the note with explanatory prose after it (the real 7G.3 review-note shape).
    assert parse_agent_approval_token(f"{RISK_APPROVAL_TOKEN}: true\nextra prose", RISK_APPROVAL_TOKEN) is True


def test_review_approval_parser_handles_leading_token_and_markdown():
    # Exact real-world failing case: token on first line, explanation paragraph after.
    review_note = (
        f"{REVIEW_APPROVAL_TOKEN}: true\n\n"
        "The proposal meets all requirements, including a clear stock_long idea with a "
        "strong thesis and confidence level."
    )
    assert parse_agent_approval_token(review_note, REVIEW_APPROVAL_TOKEN) is True
    # Explicit false still parses false.
    assert (
        parse_agent_approval_token(
            f"{REVIEW_APPROVAL_TOKEN}: false\nNeeds a clearer thesis.", REVIEW_APPROVAL_TOKEN
        )
        is False
    )
    # Robust to whitespace, markdown wrappers, and case around true/false.
    assert parse_agent_approval_token(f"  **{REVIEW_APPROVAL_TOKEN}: TRUE**  ", REVIEW_APPROVAL_TOKEN) is True
    assert parse_agent_approval_token(f"- {REVIEW_APPROVAL_TOKEN} : False", REVIEW_APPROVAL_TOKEN) is False
    # Vague approval without the exact token never counts as approval.
    assert parse_agent_approval_token("I think this looks great, approved!", REVIEW_APPROVAL_TOKEN) is None
    assert parse_agent_approval_token(f"{REVIEW_APPROVAL_TOKEN}: maybe", REVIEW_APPROVAL_TOKEN) is None
    # Final verdict wins if the agent restates itself.
    assert (
        parse_agent_approval_token(
            f"{REVIEW_APPROVAL_TOKEN}: false\nOn reflection:\n{REVIEW_APPROVAL_TOKEN}: true",
            REVIEW_APPROVAL_TOKEN,
        )
        is True
    )


def test_team_paper_cycle_reports_saved_note_paths_when_approval_missing(tmp_path):
    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def vague_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        text = "This looks acceptable to me, subject to later checks."
        output_file.write_text(text, encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=text)

    output = build_team_paper_cycle_summary(
        "team_alpha",
        "Prepare a conservative SPY-beating paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=vague_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert "Risk agent approval: no" in output
    assert "Review agent approval: no" in output
    assert f"Risk note saved: {tmp_path / 'notes'}" in output
    assert f"Review note saved: {tmp_path / 'notes'}" in output
    assert "No paper order can be submitted unless both agent approvals and deterministic Python risk approval pass." in output


def test_latest_team_cycle_reports_paths_and_parsed_approvals(tmp_path):
    proposal_dir = tmp_path / "proposals"
    notes_dir = tmp_path / "notes" / "team_alpha"
    proposal_dir.mkdir()
    notes_dir.mkdir(parents=True)
    proposal_file = proposal_dir / "discord_team_alpha_discord_v1_20260614.json"
    proposal_file.write_text(json.dumps(_generated_payload(_request())), encoding="utf-8")
    risk_note = notes_dir / "alpha_risk_01_20260614.md"
    review_note = notes_dir / "alpha_review_01_20260614.md"
    risk_note.write_text(f"Risk handoff approved.\n{RISK_APPROVAL_TOKEN}: true", encoding="utf-8")
    review_note.write_text(f"Review blocks until thesis is clearer.\n{REVIEW_APPROVAL_TOKEN}: false", encoding="utf-8")

    output = build_latest_team_cycle_summary(
        "team_alpha",
        DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=proposal_dir,
        notes_output_dir=tmp_path / "notes",
        settings=_settings(tmp_path),
    )

    assert f"Latest proposal: {proposal_file}" in output
    assert "Proposal routing split: execution_eligible_proposals 1, simulation_only_proposals 3, rejected_proposals 1." in output
    assert f"Latest risk note: {risk_note}" in output
    assert f"Latest review note: {review_note}" in output
    assert "Parsed risk approval: yes" in output
    assert "Parsed review approval: no" in output
    assert "stock_long subset eligible to proceed to deterministic Python risk review: no" in output
    assert "Autonomy: disabled" in output
    assert "Paper order submission status: none recorded" in output


def test_latest_team_cycle_reports_eligible_subset_when_both_agents_approve(tmp_path):
    proposal_dir = tmp_path / "proposals"
    notes_dir = tmp_path / "notes" / "team_alpha"
    proposal_dir.mkdir()
    notes_dir.mkdir(parents=True)
    proposal_file = proposal_dir / "discord_team_alpha_discord_v1_20260614.json"
    proposal_file.write_text(json.dumps(_generated_payload(_request())), encoding="utf-8")
    risk_note = notes_dir / "alpha_risk_01_20260614.md"
    review_note = notes_dir / "alpha_review_01_20260614.md"
    risk_note.write_text(f"Risk handoff approved.\n{RISK_APPROVAL_TOKEN}: true", encoding="utf-8")
    review_note.write_text(f"Review handoff approved.\n{REVIEW_APPROVAL_TOKEN}: true", encoding="utf-8")

    output = build_latest_team_cycle_summary(
        "team_alpha",
        DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=proposal_dir,
        notes_output_dir=tmp_path / "notes",
        settings=_settings(tmp_path),
    )

    assert "Proposal routing split: execution_eligible_proposals 1, simulation_only_proposals 3, rejected_proposals 1." in output
    assert "Parsed risk approval: yes" in output
    assert "Parsed review approval: yes" in output
    assert "stock_long subset eligible to proceed to deterministic Python risk review: yes" in output


def test_latest_team_cycle_parses_review_approval_with_leading_token(tmp_path):
    proposal_dir = tmp_path / "proposals"
    notes_dir = tmp_path / "notes" / "team_alpha"
    proposal_dir.mkdir()
    notes_dir.mkdir(parents=True)
    proposal_file = proposal_dir / "discord_team_alpha_discord_v1_20260615.json"
    proposal_file.write_text(json.dumps(_generated_payload(_request())), encoding="utf-8")
    risk_note = notes_dir / "alpha_risk_01_20260615.md"
    review_note = notes_dir / "alpha_review_01_20260615.md"
    risk_note.write_text(f"{RISK_APPROVAL_TOKEN}: true\nRisk handoff approved with detail.", encoding="utf-8")
    # Real 7G.3 shape: verdict token first, explanatory paragraph after.
    review_note.write_text(
        f"{REVIEW_APPROVAL_TOKEN}: true\n\nThe proposal meets all requirements with a clear thesis "
        "and confidence level.",
        encoding="utf-8",
    )

    output = build_latest_team_cycle_summary(
        "team_alpha",
        DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=proposal_dir,
        notes_output_dir=tmp_path / "notes",
        settings=_settings(tmp_path),
    )

    assert "Parsed risk approval: yes" in output
    assert "Parsed review approval: yes" in output
    assert "stock_long subset eligible to proceed to deterministic Python risk review: yes" in output


def test_team_paper_cycle_eligibility_yes_with_leading_token_review_note(tmp_path):
    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def leading_token_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if request.agent_role == "risk_agent":
            text = f"{RISK_APPROVAL_TOKEN}: true\nRisk handoff approved with detail."
        else:
            text = (
                f"{REVIEW_APPROVAL_TOKEN}: true\n\nThe stock_long subset is well-framed "
                "and ready for deterministic Python risk review."
            )
        output_file.write_text(text, encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=text)

    output = build_team_paper_cycle_summary(
        "team_alpha",
        "Prepare a conservative SPY-beating paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "false"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=leading_token_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    # execution_eligible_proposals == 1 and both verdicts true -> eligibility yes.
    assert "Proposal routing split: execution_eligible_proposals 1, simulation_only_proposals 3, rejected_proposals 1." in output
    assert "Risk agent approval: yes" in output
    assert "Review agent approval: yes" in output
    assert "stock_long subset eligible to proceed to deterministic Python risk review: yes" in output
    # Autonomy disabled keeps it non-executing despite both approvals.
    assert "No paper orders submitted." in output


def test_team_paper_cycle_with_autonomy_submits_only_after_approval_tokens(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)

    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(
                {
                    "agent_id": request.agent_id,
                    "team_id": request.team_id,
                    "strategy_id": request.strategy_id,
                    "agent_role": request.agent_role,
                    "proposals": [
                        {
                            "proposal_type": "stock_long",
                            "symbol": "MSFT",
                            "target_weight": 0.05,
                            "estimated_price": 100,
                            "thesis": "Approved paper-cycle stock-long candidate.",
                            "confidence": 0.7,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def approving_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        token = RISK_APPROVAL_TOKEN if request.agent_role == "risk_agent" else REVIEW_APPROVAL_TOKEN
        output_file.write_text(f"Approved for deterministic Python risk.\n{token}: true", encoding="utf-8")
        return HermesAgentChatResult(
            output_file=output_file,
            response_text=output_file.read_text(encoding="utf-8"),
        )

    fake_client = _FakeTradingClient(
        account=SimpleNamespace(equity="10000", cash="9000", buying_power="9000"),
        positions=[],
    )

    output = build_team_paper_cycle_summary(
        "team_alpha",
        "Run the approved stock-long paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "true"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=approving_asker,
        settings=_settings(tmp_path),
        client_factory=lambda _settings: fake_client,
    )

    assert "Autonomy: enabled" in output
    assert "Deterministic paper gate result:" in output
    assert "Submitted paper order count: 1" in output
    assert len(fake_client.submitted_orders) == 1


def test_team_paper_cycle_does_not_execute_when_review_rejects_even_with_autonomy(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)

    def fake_generator(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(
                {
                    "agent_id": request.agent_id,
                    "team_id": request.team_id,
                    "strategy_id": request.strategy_id,
                    "agent_role": request.agent_role,
                    "proposals": [
                        {
                            "proposal_type": "stock_long",
                            "symbol": "MSFT",
                            "target_weight": 0.05,
                            "estimated_price": 100,
                            "thesis": "Valid stock-long candidate with full fields.",
                            "confidence": 0.7,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    def risk_yes_review_no_asker(config, request, output_file):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if request.agent_role == "risk_agent":
            text = f"Risk handoff approved.\n{RISK_APPROVAL_TOKEN}: true"
        else:
            text = f"Review blocks this cycle.\n{REVIEW_APPROVAL_TOKEN}: false"
        output_file.write_text(text, encoding="utf-8")
        return HermesAgentChatResult(output_file=output_file, response_text=text)

    output = build_team_paper_cycle_summary(
        "team_alpha",
        "Run the stock-long paper cycle.",
        config=DiscordBotConfig.from_env({"TEAM_ALPHA_AUTONOMY_ENABLED": "true"}),
        proposal_output_dir=tmp_path / "proposals",
        notes_output_dir=tmp_path / "notes",
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
        asker=risk_yes_review_no_asker,
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert "Autonomy: enabled" in output
    assert "Risk agent approval: yes" in output
    assert "Review agent approval: no" in output
    assert "No paper orders submitted." in output
    assert "Submitted paper order count" not in output


def test_paper_trade_team_requires_agent_approval_gate(tmp_path):
    output = build_paper_trade_team_summary(
        "team_alpha",
        tmp_path / "proposal.json",
        settings=_settings(tmp_path),
        client_factory=_forbidden_client_factory,
    )

    assert "Risk and review agent approvals are required" in output
    assert "no trades placed" in output


def test_agent_approval_gate_from_files_requires_exact_true_tokens(tmp_path):
    risk_note = tmp_path / "risk.md"
    review_note = tmp_path / "review.md"
    risk_note.write_text(f"Risk approves Python risk handoff.\n{RISK_APPROVAL_TOKEN}: true", encoding="utf-8")
    review_note.write_text(f"Review does not approve.\n{REVIEW_APPROVAL_TOKEN}: false", encoding="utf-8")

    gate = build_agent_approval_gate_from_files(risk_note, review_note)

    assert gate.risk_agent_approved
    assert not gate.review_agent_approved
    assert not gate.approved


def test_paper_trade_team_submits_only_approved_stock_longs_and_logs(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(
        json.dumps(
            {
                "agent_id": "alpha_research_01",
                "team_id": "team_alpha",
                "strategy_id": "team_alpha_discord_v1",
                "agent_role": "research_agent",
                "proposals": [
                    {
                        "proposal_type": "stock_long",
                        "symbol": "MSFT",
                        "target_weight": 0.05,
                        "estimated_price": 100,
                        "thesis": "Approved local paper stock-long candidate.",
                        "confidence": 0.7,
                    },
                    {
                        "proposal_type": "option_long_call",
                        "underlying_symbol": "SPY",
                        "option_type": "call",
                        "strike": 500,
                        "expiration_date": (date.today() + timedelta(days=45)).isoformat(),
                        "side": "buy_to_open",
                        "max_premium": 250,
                        "thesis": "Options review only.",
                        "confidence": 0.6,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    fake_client = _FakeTradingClient(
        account=SimpleNamespace(equity="10000", cash="9000", buying_power="9000"),
        positions=[],
    )
    settings = _settings(tmp_path)

    output = build_paper_trade_team_summary(
        "team_alpha",
        proposal_file,
        approval_gate=AgentApprovalGate(
            risk_agent_approved=True,
            review_agent_approved=True,
            source="test approvals",
        ),
        settings=settings,
        client_factory=lambda _settings: fake_client,
    )

    assert "Approved count: 1" in output
    assert "Rejected count: 1" in output
    assert "Submitted paper order count: 1" in output
    assert "Approval source: test approvals" in output
    assert "paper options execution not enabled yet" in output
    assert len(fake_client.submitted_orders) == 1
    with get_connection(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE submitted = 1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0] == 1


def test_paper_trade_team_respects_daily_order_cap(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(
        json.dumps(
            {
                "agent_id": "alpha_research_01",
                "team_id": "team_alpha",
                "strategy_id": "team_alpha_discord_v1",
                "agent_role": "research_agent",
                "proposals": [
                    {
                        "proposal_type": "stock_long",
                        "symbol": "MSFT",
                        "target_weight": 0.05,
                        "estimated_price": 100,
                        "thesis": "First capped stock-long candidate.",
                        "confidence": 0.7,
                    },
                    {
                        "proposal_type": "stock_long",
                        "symbol": "AAPL",
                        "target_weight": 0.05,
                        "estimated_price": 100,
                        "thesis": "Second capped stock-long candidate.",
                        "confidence": 0.7,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    fake_client = _FakeTradingClient(
        account=SimpleNamespace(equity="10000", cash="9000", buying_power="9000"),
        positions=[],
    )

    output = build_paper_trade_team_summary(
        "team_alpha",
        proposal_file,
        approval_gate=AgentApprovalGate(
            risk_agent_approved=True,
            review_agent_approved=True,
            source="test approvals",
        ),
        autonomy_config=TeamAutonomyConfig(
            team_id="team_alpha",
            max_paper_orders_per_day=1,
            max_daily_notional=50000,
        ),
        settings=_settings(tmp_path),
        client_factory=lambda _settings: fake_client,
    )

    assert "Submitted paper order count: 1" in output
    assert "daily paper order cap reached" in output
    assert len(fake_client.submitted_orders) == 1


def test_paper_trade_team_respects_daily_notional_cap(monkeypatch, tmp_path):
    _set_team_alpha_env(monkeypatch)
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(
        json.dumps(
            {
                "agent_id": "alpha_research_01",
                "team_id": "team_alpha",
                "strategy_id": "team_alpha_discord_v1",
                "agent_role": "research_agent",
                "proposals": [
                    {
                        "proposal_type": "stock_long",
                        "symbol": "MSFT",
                        "target_weight": 0.05,
                        "estimated_price": 100,
                        "thesis": "First notional capped stock-long candidate.",
                        "confidence": 0.7,
                    },
                    {
                        "proposal_type": "stock_long",
                        "symbol": "AAPL",
                        "target_weight": 0.05,
                        "estimated_price": 100,
                        "thesis": "Second notional capped stock-long candidate.",
                        "confidence": 0.7,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    fake_client = _FakeTradingClient(
        account=SimpleNamespace(equity="10000", cash="9000", buying_power="9000"),
        positions=[],
    )

    output = build_paper_trade_team_summary(
        "team_alpha",
        proposal_file,
        approval_gate=AgentApprovalGate(
            risk_agent_approved=True,
            review_agent_approved=True,
            source="test approvals",
        ),
        autonomy_config=TeamAutonomyConfig(
            team_id="team_alpha",
            max_paper_orders_per_day=3,
            max_daily_notional=600,
        ),
        settings=_settings(tmp_path),
        client_factory=lambda _settings: fake_client,
    )

    assert "Submitted paper order count: 1" in output
    assert "daily paper notional cap would be exceeded" in output
    assert len(fake_client.submitted_orders) == 1


def test_team_report_explains_missing_benchmark_data(tmp_path):
    output = build_team_report_summary("team_alpha", settings=_settings(tmp_path))

    assert "No local paper report data" in output


def test_discord_helpers_do_not_call_alpaca_orders_or_database(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Discord bot command summaries must stay local and non-executing")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)

    build_status_summary(DiscordBotConfig.from_env({}))
    build_teams_summary("docs/examples/hermes_team_registry_example.json")
    build_review_proposals_summary("docs/examples/hermes_strategy_sandbox_example.json")
    build_run_tournament_summary(
        "docs/examples/hermes_team_registry_example.json",
        ["docs/examples/hermes_strategy_sandbox_example.json"],
    )


def test_ask_team_does_not_call_alpaca_orders_or_database(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("ask_team must not call Alpaca, orders, or database")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)

    def fake_generator(config, request, output_file):
        output_file.write_text(json.dumps(_generated_payload(request)), encoding="utf-8")
        return HermesGenerationResult(
            output_file=output_file,
            raw_json=output_file.read_text(encoding="utf-8"),
            sandbox_result=load_hermes_sandbox_file(output_file),
        )

    output = build_ask_team_summary(
        "team_alpha",
        "alpha_research_01",
        "research_agent",
        "team_alpha_discord_v1",
        "Find a high-conviction strategy for tomorrow",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        generator=fake_generator,
    )

    assert "proposal only; no trades placed" in output


def test_ask_agent_does_not_call_alpaca_orders_or_database(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("ask_agent must not call Alpaca, orders, or database")

    monkeypatch.setattr("src.brokers.alpaca_client.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.execution.order_executor.OrderExecutor", forbidden)
    monkeypatch.setattr("src.db.database.initialize_database", forbidden)

    def fake_asker(config, request, output_file):
        output_file.write_text("Risk review: proposal-only; no orders placed.", encoding="utf-8")
        return HermesAgentChatResult(
            output_file=output_file,
            response_text=output_file.read_text(encoding="utf-8"),
        )

    output = build_ask_agent_summary(
        "team_alpha",
        "alpha_risk_01",
        "Review today's proposal risk.",
        output_dir=tmp_path,
        runtime_config=HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model="hermes"),
        asker=fake_asker,
    )

    assert "proposal only; no trades placed" in output


def test_cli_discord_bot_refuses_without_token(tmp_path):
    env = os.environ.copy()
    env.pop("DISCORD_BOT_TOKEN", None)
    env["PYTHONPATH"] = os.getcwd()

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "discord-bot"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 1
    assert "DISCORD_BOT_TOKEN is required" in result.stderr


def test_cli_loads_dotenv_before_discord_bot_config(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("DISCORD_BOT_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["python", "discord-bot"])

    import src.main as main_module

    def fake_run_discord_bot_cli():
        assert os.getenv("DISCORD_BOT_TOKEN") == "dotenv-token"
        raise SystemExit(23)

    monkeypatch.setattr(main_module, "run_discord_bot_cli", fake_run_discord_bot_cli)

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 23


def test_cli_dotenv_does_not_override_existing_environment(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("DISCORD_BOT_TOKEN=dotenv-token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "real-env-token")
    monkeypatch.setattr(sys, "argv", ["python", "discord-bot"])

    import src.main as main_module

    def fake_run_discord_bot_cli():
        assert os.getenv("DISCORD_BOT_TOKEN") == "real-env-token"
        raise SystemExit(24)

    monkeypatch.setattr(main_module, "run_discord_bot_cli", fake_run_discord_bot_cli)

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 24


def _generated_payload(request):
    return {
        "agent_id": request.agent_id,
        "team_id": request.team_id,
        "strategy_id": request.strategy_id,
        "agent_role": request.agent_role,
        "strategy_notes": request.strategy_notes,
        "learning_goal": request.learning_goal,
        "proposals": [
            {
                "proposal_type": "stock_long",
                "symbol": "MSFT",
                "target_weight": 0.05,
                "estimated_price": 420.5,
                "thesis": "Local Discord ask_team stock proposal.",
                "confidence": 0.7,
            },
            {
                "proposal_type": "short_stock",
                "symbol": "RISK",
                "target_short_weight": 0.03,
                "estimated_price": 50,
                "thesis": "Local Discord ask_team short simulation proposal.",
                "confidence": 0.5,
                "borrow_available_assumption": True,
            },
            {
                "proposal_type": "option_long",
                "contract": {
                    "underlying_symbol": "SPY",
                    "option_type": "call",
                    "expiration": (date.today() + timedelta(days=45)).isoformat(),
                    "strike": 600,
                },
                "action": "buy_to_open",
                "contracts": 1,
                "premium": 4.25,
                "estimated_total_premium": 425,
                "thesis": "Local Discord ask_team option simulation proposal.",
                "confidence": 0.6,
                "liquidity_open_interest_assumption": "Sufficient for fixture.",
                "assignment_exercise_risk_note": "Can expire worthless.",
            },
            {
                "proposal_type": "margin",
                "requested_gross_exposure": 1.2,
                "symbols": ["MSFT", "SPY"],
                "thesis": "Local Discord ask_team margin simulation proposal.",
                "confidence": 0.55,
            },
            {"proposal_type": "unknown_type", "symbol": "NOPE"},
        ],
    }


def _request(strategy_id="team_alpha_discord_v1"):
    return SimpleNamespace(
        agent_id="alpha_research_01",
        team_id="team_alpha",
        strategy_id=strategy_id,
        agent_role="research_agent",
        strategy_notes="Discord test.",
        learning_goal="Find a high-conviction strategy for tomorrow",
    )


class _FakeTradingClient:
    def __init__(self, account, positions, market_open=True):
        self.account = account
        self.positions = positions
        self.market_open = market_open
        self.submitted_orders = []

    def get_account(self):
        return self.account

    def get_all_positions(self):
        return self.positions

    def get_clock(self):
        return SimpleNamespace(is_open=self.market_open)

    def submit_order(self, order_request):
        self.submitted_orders.append(order_request)
        return SimpleNamespace(id="paper-order-1")


def _settings(tmp_path):
    return Settings(
        alpaca_api_key=None,
        alpaca_secret_key=None,
        alpaca_paper=True,
        alpaca_base_url=PAPER_BASE_URL,
        database_path=tmp_path / "discord.sqlite3",
        dry_run=True,
        starting_equity=10000,
        min_cash_pct=0.10,
        max_position_pct=0.20,
        max_daily_turnover_pct=0.30,
        max_new_positions_per_day=5,
    )


def _set_team_alpha_env(monkeypatch):
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_PAPER", "true")
    monkeypatch.setenv("TEAM_ALPHA_ALPACA_BASE_URL", PAPER_BASE_URL)


def _set_team_beta_env(monkeypatch):
    monkeypatch.setenv("TEAM_BETA_ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("TEAM_BETA_ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("TEAM_BETA_ALPACA_PAPER", "true")
    monkeypatch.setenv("TEAM_BETA_ALPACA_BASE_URL", PAPER_BASE_URL)


def _forbidden_generator(*_args, **_kwargs):
    raise AssertionError("Hermes generator must not be called")


def _forbidden_client_factory(*_args, **_kwargs):
    raise AssertionError("paper cycle must not create an Alpaca client when autonomy is disabled")
