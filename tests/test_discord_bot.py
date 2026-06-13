import json
import os
import subprocess
import sys
from datetime import date, timedelta

import pytest

from src.agents.hermes_runtime import HermesGenerationResult, HermesRuntimeConfig
from src.agents.hermes_strategy_sandbox import load_hermes_sandbox_file
from src.discord_bot.bot import (
    DiscordBotConfig,
    build_ask_team_summary,
    build_review_proposals_summary,
    build_run_tournament_summary,
    build_status_summary,
    build_teams_summary,
    is_channel_allowed,
    parse_allowed_channel_ids,
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


def test_status_output():
    config = DiscordBotConfig.from_env(
        {
            "DISCORD_DEFAULT_REGISTRY": "registry.json",
            "DISCORD_DEFAULT_PROPOSAL": "proposal.json",
            "DISCORD_ALLOWED_CHANNEL_IDS": "123",
        }
    )

    output = build_status_summary(config)

    assert "ExaltedFable command center: online." in output
    assert "Trading, Alpaca calls, and order execution are disabled" in output
    assert "registry.json" in output
    assert "proposal.json" in output
    assert "1 allowed channel" in output


def test_teams_summary():
    output = build_teams_summary("docs/examples/hermes_team_registry_example.json")

    assert "Hermes teams" in output
    assert "team_alpha" in output
    assert "team_beta" in output
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


def test_cli_discord_bot_refuses_without_token():
    env = os.environ.copy()
    env.pop("DISCORD_BOT_TOKEN", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "discord-bot"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "DISCORD_BOT_TOKEN is required" in result.stderr


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


def _forbidden_generator(*_args, **_kwargs):
    raise AssertionError("Hermes generator must not be called")
