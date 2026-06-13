import json
import os
import subprocess
import sys

import pytest

from src.agents.hermes_team_registry import (
    HermesAgentRole,
    format_hermes_team_registry,
    parse_hermes_team_registry_json,
)
from src.main import run_hermes_teams


def test_valid_registry_parses():
    registry = parse_hermes_team_registry_json(json.dumps(_valid_registry()))

    assert len(registry.teams) == 2
    assert registry.teams[0].team_id == "team_alpha"
    assert registry.teams[0].agents[0].agent_id == "alpha_research_01"
    assert registry.teams[0].agents[0].role == HermesAgentRole.RESEARCH_AGENT


def test_duplicate_team_id_is_rejected():
    payload = _valid_registry()
    payload["teams"][1]["team_id"] = "team_alpha"
    for agent in payload["teams"][1]["agents"]:
        agent["team_id"] = "team_alpha"

    with pytest.raises(ValueError, match="duplicate team_id"):
        parse_hermes_team_registry_json(json.dumps(payload))


def test_duplicate_agent_id_is_rejected_across_registry():
    payload = _valid_registry()
    payload["teams"][1]["agents"][0]["agent_id"] = "alpha_research_01"

    with pytest.raises(ValueError, match="duplicate agent_id"):
        parse_hermes_team_registry_json(json.dumps(payload))


def test_invalid_role_is_rejected():
    payload = _valid_registry()
    payload["teams"][0]["agents"][0]["role"] = "broker_agent"

    with pytest.raises(ValueError, match="role"):
        parse_hermes_team_registry_json(json.dumps(payload))


def test_mismatched_agent_team_id_is_rejected():
    payload = _valid_registry()
    payload["teams"][0]["agents"][0]["team_id"] = "team_beta"

    with pytest.raises(ValueError, match="must match parent team_id"):
        parse_hermes_team_registry_json(json.dumps(payload))


def test_empty_team_agents_are_rejected():
    payload = _valid_registry()
    payload["teams"][0]["agents"] = []

    with pytest.raises(ValueError, match="agents"):
        parse_hermes_team_registry_json(json.dumps(payload))


def test_missing_team_id_and_agent_id_are_rejected():
    missing_team = _valid_registry()
    del missing_team["teams"][0]["team_id"]
    missing_agent = _valid_registry()
    del missing_agent["teams"][0]["agents"][0]["agent_id"]

    with pytest.raises(ValueError, match="team_id"):
        parse_hermes_team_registry_json(json.dumps(missing_team))
    with pytest.raises(ValueError, match="agent_id"):
        parse_hermes_team_registry_json(json.dumps(missing_agent))


def test_extra_unknown_fields_are_rejected():
    payload = _valid_registry()
    payload["teams"][0]["broker_api_key"] = "must-not-exist"

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        parse_hermes_team_registry_json(json.dumps(payload))


def test_format_prints_teams_agents_status_roles_and_safety_line():
    output = format_hermes_team_registry(parse_hermes_team_registry_json(json.dumps(_valid_registry())))

    assert "registry only; no trading or LLM calls" in output
    assert "team_alpha (Alpha Research Desk) [active]" in output
    assert "alpha_review_01 (Alpha Reviewer) [inactive] role=review_agent" in output
    assert "team_beta (Beta Strategy Forge) [active]" in output
    assert "beta_execution_01 (Beta Execution Shadow) [inactive] role=execution_agent" in output


def test_cli_works_without_credentials():
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "hermes-teams",
            "--file",
            "docs/examples/hermes_team_registry_example.json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes Team Registry" in result.stdout
    assert "registry only; no trading or LLM calls" in result.stdout
    assert "team_alpha" in result.stdout
    assert "team_beta" in result.stdout
    assert "role=research_agent" in result.stdout
    assert "role=portfolio_manager" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_does_not_call_alpaca_hermes_network_llm_or_database(tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps(_valid_registry()), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("hermes-teams must stay local and side-effect free")

    monkeypatch.setattr("src.main.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.main.initialize_database", forbidden)
    monkeypatch.setattr("src.main.Settings.from_env", forbidden)

    run_hermes_teams(registry_file)


def test_review_hermes_sandbox_still_works_without_credentials():
    env = _safe_env()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "review-hermes-sandbox",
            "--file",
            "docs/examples/hermes_strategy_sandbox_example.json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes proposals are not execution approval" in result.stdout


def test_compare_strategies_still_works(tmp_path):
    env = _safe_env(database_path=tmp_path / "comparison.sqlite3")
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout


def test_fixture_sweep_still_works(tmp_path):
    env = _safe_env(database_path=tmp_path / "sweep.sqlite3")
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "fixture-sweep"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout


def _valid_registry():
    return {
        "registry_notes": "Local registry only.",
        "teams": [
            {
                "team_id": "team_alpha",
                "team_name": "Alpha Research Desk",
                "description": "Balanced research team.",
                "active": True,
                "strategy_family": "quality_momentum",
                "learning_notes": "Track rejected ideas.",
                "agents": [
                    {
                        "agent_id": "alpha_research_01",
                        "team_id": "team_alpha",
                        "agent_name": "Alpha Scout",
                        "role": "research_agent",
                        "description": "Finds candidate ideas.",
                        "active": True,
                        "model_hint": "local-placeholder",
                        "strengths": ["theme discovery"],
                        "weaknesses": ["momentum bias"],
                        "latest_strategy_id": "alpha_quality_v1",
                        "learning_notes": "Improve scenario notes.",
                    },
                    {
                        "agent_id": "alpha_review_01",
                        "team_id": "team_alpha",
                        "agent_name": "Alpha Reviewer",
                        "role": "review_agent",
                        "description": "Summarizes outcomes.",
                        "active": False,
                    },
                ],
            },
            {
                "team_id": "team_beta",
                "team_name": "Beta Strategy Forge",
                "description": "Experimental mutation team.",
                "active": True,
                "strategy_family": "adaptive_sandbox",
                "agents": [
                    {
                        "agent_id": "beta_mutator_01",
                        "team_id": "team_beta",
                        "agent_name": "Beta Mutator",
                        "role": "strategy_mutator",
                        "description": "Creates variants.",
                        "active": True,
                    },
                    {
                        "agent_id": "beta_execution_01",
                        "team_id": "team_beta",
                        "agent_name": "Beta Execution Shadow",
                        "role": "execution_agent",
                        "description": "Non-executing execution-plan identity.",
                        "active": False,
                    },
                ],
            },
        ],
    }


def _safe_env(database_path=None):
    env = os.environ.copy()
    if database_path is not None:
        env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return env
