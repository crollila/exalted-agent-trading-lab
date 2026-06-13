import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from src.agents.hermes_tournament_round import (
    SCORE_FORMULA,
    format_hermes_tournament_round,
    run_hermes_tournament_round,
    save_hermes_tournament_round_artifacts,
)
from src.main import run_hermes_tournament_round_cli


def test_one_proposal_tournament_works(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = _write_json(tmp_path / "alpha.json", _proposal("team_alpha", "alpha_research_01", "alpha_v1"))

    result = run_hermes_tournament_round(registry_path, [proposal_path])

    assert result.winner is not None
    assert result.winner.team_id == "team_alpha"
    assert result.rows[0].team_id == "team_alpha"
    assert result.rows[0].paper_eligible_stock_long_count == 1
    assert result.rows[0].simulation_only_short_count == 1
    assert result.rows[0].simulation_only_option_count == 1
    assert result.rows[0].rejected_count == 1
    assert result.rows[0].score == 3


def test_multi_team_tournament_scores_and_ranks(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    alpha_path = _write_json(tmp_path / "alpha.json", _proposal("team_alpha", "alpha_research_01", "alpha_v1"))
    beta_path = _write_json(tmp_path / "beta.json", _proposal("team_beta", "beta_mutator_01", "beta_v1", rejected=False))

    result = run_hermes_tournament_round(registry_path, [alpha_path, beta_path])

    assert [ranking.team_id for ranking in result.rankings] == ["team_beta", "team_alpha"]
    assert result.rankings[0].score == 4
    assert result.rankings[1].score == 3


def test_unknown_team_id_is_handled_safely(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = _write_json(tmp_path / "unknown.json", _proposal("team_missing", "agent-x", "missing_v1"))

    result = run_hermes_tournament_round(registry_path, [proposal_path])

    assert result.rows[0].team_id == "team_missing"
    assert result.rows[0].rejected_count == 2
    assert result.rows[0].score == 2
    assert any("Unknown team_id" in error for error in result.errors)


def test_score_formula_is_reported_and_applied(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = _write_json(tmp_path / "alpha.json", _proposal("team_alpha", "alpha_research_01", "alpha_v1"))

    result = run_hermes_tournament_round(registry_path, [proposal_path])

    assert result.score_formula == SCORE_FORMULA
    assert result.rows[0].score == (1 * 2) + (2 * 1) - (1 * 1)


def test_tie_breakers_are_deterministic(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    alpha_path = _write_json(
        tmp_path / "alpha.json",
        _proposal("team_alpha", "alpha_research_01", "alpha_v1", include_short=False, rejected=False),
    )
    beta_path = _write_json(
        tmp_path / "beta.json",
        _proposal("team_beta", "beta_mutator_01", "beta_v1", include_short=False, rejected=False),
    )

    result = run_hermes_tournament_round(registry_path, [beta_path, alpha_path])

    assert [ranking.team_id for ranking in result.rankings] == ["team_alpha", "team_beta"]


def test_json_and_markdown_save_work_and_create_output_dir(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = _write_json(tmp_path / "alpha.json", _proposal("team_alpha", "alpha_research_01", "alpha_v1"))
    result = run_hermes_tournament_round(
        registry_path,
        [proposal_path],
        generated_at=datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
    )

    artifacts = save_hermes_tournament_round_artifacts(result, output_dir=tmp_path / "missing" / "experiments")

    assert artifacts.json_path.exists()
    assert artifacts.markdown_path.exists()
    payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    assert payload["score_formula"] == SCORE_FORMULA
    assert payload["rankings"][0]["team_id"] == "team_alpha"
    assert "# Hermes Tournament Round" in markdown
    assert "routing score only, not profitability" in markdown


def test_malformed_proposal_is_handled_safely(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = tmp_path / "bad.json"
    proposal_path.write_text("{not json", encoding="utf-8")

    result = run_hermes_tournament_round(registry_path, [proposal_path])

    assert result.rows[0].team_id == "invalid"
    assert result.rows[0].rejected_count == 1
    assert result.rows[0].score == -1
    assert "Invalid JSON" in result.errors[0]


def test_format_prints_winner_ranking_and_disclaimer(tmp_path):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = _write_json(tmp_path / "alpha.json", _proposal("team_alpha", "alpha_research_01", "alpha_v1"))
    output = format_hermes_tournament_round(run_hermes_tournament_round(registry_path, [proposal_path]))

    assert "Winner: team_alpha" in output
    assert "Team rankings" in output
    assert "routing score only, not profitability" in output


def test_cli_works_without_credentials(tmp_path):
    env = _safe_env()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "hermes-tournament-round",
            "--registry",
            "docs/examples/hermes_team_registry_example.json",
            "--proposal",
            "docs/examples/hermes_strategy_sandbox_example.json",
            "--proposal",
            "docs/examples/hermes_strategy_sandbox_team_beta_example.json",
            "--save",
            "--output-dir",
            str(tmp_path / "artifacts"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes Tournament Round" in result.stdout
    assert "Winner: team_beta" in result.stdout
    assert "Saved Hermes tournament round artifacts:" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_does_not_call_hermes_alpaca_network_llm_or_database(tmp_path, monkeypatch):
    registry_path = _write_json(tmp_path / "registry.json", _registry())
    proposal_path = _write_json(tmp_path / "alpha.json", _proposal("team_alpha", "alpha_research_01", "alpha_v1"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("hermes-tournament-round must stay local and side-effect free")

    monkeypatch.setattr("src.main.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.main.initialize_database", forbidden)
    monkeypatch.setattr("src.main.Settings.from_env", forbidden)

    run_hermes_tournament_round_cli(registry_path, [proposal_path])


def test_review_hermes_sandbox_still_works():
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
        env=_safe_env(),
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes proposals are not execution approval" in result.stdout


def test_hermes_teams_still_works():
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
        env=_safe_env(),
        check=False,
    )

    assert result.returncode == 0
    assert "registry only; no trading or LLM calls" in result.stdout


def test_compare_strategies_still_works(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "compare-strategies"],
        capture_output=True,
        text=True,
        env=_safe_env(database_path=tmp_path / "comparison.sqlite3"),
        check=False,
    )

    assert result.returncode == 0
    assert "Strategy Comparison" in result.stdout


def test_fixture_sweep_still_works(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "fixture-sweep"],
        capture_output=True,
        text=True,
        env=_safe_env(database_path=tmp_path / "sweep.sqlite3"),
        check=False,
    )

    assert result.returncode == 0
    assert "Fixture Sweep Tournament" in result.stdout


def _registry():
    return {
        "teams": [
            {
                "team_id": "team_alpha",
                "team_name": "Alpha",
                "description": "Alpha team.",
                "active": True,
                "agents": [
                    {
                        "agent_id": "alpha_research_01",
                        "team_id": "team_alpha",
                        "agent_name": "Alpha Scout",
                        "role": "research_agent",
                        "description": "Finds ideas.",
                        "active": True,
                    }
                ],
            },
            {
                "team_id": "team_beta",
                "team_name": "Beta",
                "description": "Beta team.",
                "active": True,
                "agents": [
                    {
                        "agent_id": "beta_mutator_01",
                        "team_id": "team_beta",
                        "agent_name": "Beta Mutator",
                        "role": "strategy_mutator",
                        "description": "Mutates ideas.",
                        "active": True,
                    }
                ],
            },
        ]
    }


def _proposal(team_id, agent_id, strategy_id, *, include_short=True, rejected=True):
    proposals = [
        {
            "proposal_type": "stock_long",
            "symbol": "MSFT",
            "target_weight": 0.05,
            "estimated_price": 420.5,
            "thesis": "Local stock proposal.",
            "confidence": 0.7,
        },
        {
            "proposal_type": "option_long",
            "contract": {
                "underlying_symbol": "SPY",
                "option_type": "call",
                "expiration": "2031-01-17",
                "strike": 600,
            },
            "action": "buy_to_open",
            "contracts": 1,
            "premium": 4.25,
            "estimated_total_premium": 425,
            "thesis": "Local option proposal.",
            "confidence": 0.6,
            "liquidity_open_interest_assumption": "Sufficient for fixture.",
            "assignment_exercise_risk_note": "Can expire worthless.",
        },
    ]
    if include_short:
        proposals.append(
            {
                "proposal_type": "short_stock",
                "symbol": "RISK",
                "target_short_weight": 0.03,
                "estimated_price": 50,
                "thesis": "Local short proposal.",
                "confidence": 0.5,
                "borrow_available_assumption": True,
            }
        )
    if rejected:
        proposals.append({"proposal_type": "unknown_type", "symbol": "NOPE"})
    return {
        "agent_id": agent_id,
        "team_id": team_id,
        "strategy_id": strategy_id,
        "agent_role": "research_agent",
        "proposals": proposals,
    }


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _safe_env(database_path=None):
    env = os.environ.copy()
    if database_path is not None:
        env["DATABASE_PATH"] = str(database_path)
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    return env
