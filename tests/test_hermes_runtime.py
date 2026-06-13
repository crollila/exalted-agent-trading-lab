import json
import os
import subprocess
import sys

import pytest

from src.agents.hermes_runtime import (
    HermesGenerationRequest,
    HermesRuntimeConfig,
    build_hermes_generation_prompt,
    generate_hermes_proposals,
)
from src.agents.hermes_strategy_sandbox import PAPER_ELIGIBLE_STOCK_LONG
from src.main import run_hermes_generate_proposals_cli


def test_disabled_runtime_refuses(tmp_path):
    config = HermesRuntimeConfig(enabled=False, base_url="http://127.0.0.1:11434/v1", model="hermes")

    with pytest.raises(RuntimeError, match="disabled"):
        generate_hermes_proposals(config, _request(), tmp_path / "out.json", http_post=_forbidden_http_post)


def test_missing_base_url_or_model_refuses(tmp_path):
    with pytest.raises(RuntimeError, match="HERMES_BASE_URL"):
        generate_hermes_proposals(
            HermesRuntimeConfig(enabled=True, base_url="", model="hermes"),
            _request(),
            tmp_path / "out.json",
            http_post=_forbidden_http_post,
        )
    with pytest.raises(RuntimeError, match="HERMES_MODEL"):
        generate_hermes_proposals(
            HermesRuntimeConfig(enabled=True, base_url="http://127.0.0.1:11434/v1", model=""),
            _request(),
            tmp_path / "out.json",
            http_post=_forbidden_http_post,
        )


def test_config_reads_only_hermes_runtime_env():
    config = HermesRuntimeConfig.from_env(
        {
            "HERMES_ENABLED": "true",
            "HERMES_BASE_URL": "http://127.0.0.1:11434/v1",
            "HERMES_MODEL": "hermes-local",
            "HERMES_API_KEY": "dummy",
            "HERMES_TIMEOUT_SECONDS": "7",
            "ALPACA_API_KEY": "must-not-matter",
            "ALPACA_SECRET_KEY": "must-not-matter",
        }
    )

    assert config.enabled is True
    assert config.base_url == "http://127.0.0.1:11434/v1"
    assert config.model == "hermes-local"
    assert config.api_key == "dummy"
    assert config.timeout_seconds == 7


def test_prompt_requires_json_only_sandbox_schema():
    prompt = build_hermes_generation_prompt(_request())

    assert "Output ONLY strict JSON" in prompt
    assert "agent_id" in prompt
    assert "team_id" in prompt
    assert "strategy_id" in prompt
    assert "agent_role" in prompt
    assert "proposals" in prompt
    assert "stock_long" in prompt
    assert "short_stock" in prompt
    assert "option_long" in prompt
    assert "margin" in prompt
    assert "No secrets" in prompt
    assert "No execution claims" in prompt
    assert "paper/simulation routing only" in prompt
    assert "No markdown/prose outside JSON" in prompt


def test_successful_mocked_generation_saves_file_and_validates(tmp_path):
    output_file = tmp_path / "agent_runs" / "team_alpha_runtime_v1.json"
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(_generated_payload())

    result = generate_hermes_proposals(
        HermesRuntimeConfig(
            enabled=True,
            base_url="http://127.0.0.1:11434/v1",
            model="hermes-local",
            api_key="dummy",
            timeout_seconds=5,
        ),
        _request(),
        output_file,
        http_post=fake_post,
    )

    assert output_file.exists()
    assert json.loads(output_file.read_text(encoding="utf-8"))["team_id"] == "team_alpha"
    assert result.ok
    assert result.sandbox_result.route_counts()[PAPER_ELIGIBLE_STOCK_LONG] == 1
    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer dummy"
    assert captured["json"]["model"] == "hermes-local"
    assert captured["timeout"] == 5


def test_cli_works_with_mocked_runtime(tmp_path, monkeypatch, capsys):
    output_file = tmp_path / "agent_runs" / "team_alpha_runtime_v1.json"

    monkeypatch.setattr(
        "src.main.HermesRuntimeConfig.from_env",
        lambda: HermesRuntimeConfig(
            enabled=True,
            base_url="http://127.0.0.1:11434/v1",
            model="hermes-local",
        ),
    )
    monkeypatch.setattr(
        "src.main.generate_hermes_proposals",
        lambda config, request, output_file: generate_hermes_proposals(
            config,
            request,
            output_file,
            http_post=lambda *_args, **_kwargs: _FakeResponse(_generated_payload()),
        ),
    )

    run_hermes_generate_proposals_cli(
        team_id="team_alpha",
        agent_id="alpha_research_01",
        agent_role="research_agent",
        strategy_id="team_alpha_runtime_v1",
        output_file=output_file,
    )

    captured = capsys.readouterr()
    assert "Hermes proposal generation complete" in captured.out
    assert "paper_eligible_stock_long: 1" in captured.out
    assert output_file.exists()


def test_cli_does_not_call_alpaca_or_database(tmp_path, monkeypatch):
    output_file = tmp_path / "agent_runs" / "team_alpha_runtime_v1.json"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("hermes-generate-proposals must not call Alpaca, settings, or database")

    monkeypatch.setattr("src.main.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.main.initialize_database", forbidden)
    monkeypatch.setattr("src.main.Settings.from_env", forbidden)
    monkeypatch.setattr(
        "src.main.HermesRuntimeConfig.from_env",
        lambda: HermesRuntimeConfig(
            enabled=True,
            base_url="http://127.0.0.1:11434/v1",
            model="hermes-local",
        ),
    )
    monkeypatch.setattr(
        "src.main.generate_hermes_proposals",
        lambda config, request, output_file: generate_hermes_proposals(
            config,
            request,
            output_file,
            http_post=lambda *_args, **_kwargs: _FakeResponse(_generated_payload()),
        ),
    )

    run_hermes_generate_proposals_cli(
        team_id="team_alpha",
        agent_id="alpha_research_01",
        agent_role="research_agent",
        strategy_id="team_alpha_runtime_v1",
        output_file=output_file,
    )


def test_cli_refuses_when_runtime_disabled(tmp_path):
    env = _safe_env()
    env["HERMES_ENABLED"] = "false"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "hermes-generate-proposals",
            "--team-id",
            "team_alpha",
            "--agent-id",
            "alpha_research_01",
            "--agent-role",
            "research_agent",
            "--strategy-id",
            "team_alpha_runtime_v1",
            "--output-file",
            str(tmp_path / "out.json"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "Hermes runtime is disabled" in result.stdout


def test_review_hermes_sandbox_still_works():
    result = _run_cli(
        "review-hermes-sandbox",
        "--file",
        "docs/examples/hermes_strategy_sandbox_example.json",
    )

    assert result.returncode == 0
    assert "Hermes proposals are not execution approval" in result.stdout


def test_hermes_teams_still_works():
    result = _run_cli("hermes-teams", "--file", "docs/examples/hermes_team_registry_example.json")

    assert result.returncode == 0
    assert "registry only; no trading or LLM calls" in result.stdout


def test_hermes_tournament_round_still_works():
    result = _run_cli(
        "hermes-tournament-round",
        "--registry",
        "docs/examples/hermes_team_registry_example.json",
        "--proposal",
        "docs/examples/hermes_strategy_sandbox_example.json",
    )

    assert result.returncode == 0
    assert "Hermes Tournament Round" in result.stdout


def test_compare_strategies_still_works(tmp_path):
    result = _run_cli("compare-strategies", database_path=tmp_path / "comparison.sqlite3")

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout


def test_fixture_sweep_still_works(tmp_path):
    result = _run_cli("fixture-sweep", database_path=tmp_path / "sweep.sqlite3")

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.content),
                    }
                }
            ]
        }


def _request():
    return HermesGenerationRequest(
        team_id="team_alpha",
        agent_id="alpha_research_01",
        agent_role="research_agent",
        strategy_id="team_alpha_runtime_v1",
    )


def _generated_payload():
    return {
        "agent_id": "alpha_research_01",
        "team_id": "team_alpha",
        "strategy_id": "team_alpha_runtime_v1",
        "agent_role": "research_agent",
        "strategy_notes": "Mocked local runtime output.",
        "learning_goal": "Validate runtime adapter safety.",
        "proposals": [
            {
                "proposal_type": "stock_long",
                "symbol": "MSFT",
                "target_weight": 0.05,
                "estimated_price": 420.5,
                "thesis": "Mocked JSON-only stock proposal.",
                "confidence": 0.7,
            }
        ],
    }


def _forbidden_http_post(*_args, **_kwargs):
    raise AssertionError("HTTP must not be called")


def _run_cli(*args, database_path=None):
    return subprocess.run(
        [sys.executable, "-m", "src.main", *args],
        capture_output=True,
        text=True,
        env=_safe_env(database_path=database_path),
        check=False,
    )


def _safe_env(database_path=None):
    env = os.environ.copy()
    if database_path is not None:
        env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return env
