import json
import os
import subprocess
import sys

import pytest

from src.reporting.tournament_champion import format_tournament_champion, load_tournament_champion


def test_tournament_champion_from_one_artifact(tmp_path):
    _write_artifact(
        tmp_path,
        "one.json",
        results=[
            _result_row(rank=1, strategy_id="alpha", score=0.04, excess_return=0.05, max_drawdown=-0.02),
            _result_row(rank=2, strategy_id="beta", score=0.02, excess_return=0.03, max_drawdown=-0.01),
        ],
    )

    result = load_tournament_champion(tmp_path)
    summary = result.summary

    assert summary is not None
    assert summary.champion_strategy_id == "alpha"
    assert summary.valid_tournaments_reviewed == 1
    assert summary.champion_wins == 1
    assert summary.champion_win_rate == pytest.approx(1.0)
    assert summary.most_recent_win_timestamp == "2026-06-10T20:00:00+00:00"
    assert summary.fixtures_appeared == ("multi_day",)


def test_tournament_champion_from_multiple_artifacts(tmp_path):
    _write_artifact(tmp_path, "one.json", timestamp="2026-06-10T20:00:00+00:00", winner="alpha")
    _write_artifact(tmp_path, "two.json", timestamp="2026-06-11T20:00:00+00:00", winner="beta")

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.valid_tournaments_reviewed == 2


def test_tournament_champion_most_wins_determines_champion(tmp_path):
    _write_artifact(tmp_path, "one.json", timestamp="2026-06-10T20:00:00+00:00", winner="alpha", winner_score=0.01)
    _write_artifact(tmp_path, "two.json", timestamp="2026-06-11T20:00:00+00:00", winner="alpha", winner_score=0.01)
    _write_artifact(tmp_path, "three.json", timestamp="2026-06-12T20:00:00+00:00", winner="beta", winner_score=0.99)

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.champion_strategy_id == "alpha"
    assert result.summary.champion_wins == 2


def test_tournament_champion_uses_deterministic_tie_breakers(tmp_path):
    _write_artifact(tmp_path, "one.json", winner="z_alpha_last", winner_score=0.04, winner_excess_return=0.04)
    _write_artifact(tmp_path, "two.json", winner="higher_average_score", winner_score=0.05, winner_excess_return=0.01)
    _write_artifact(tmp_path, "three.json", winner="higher_best_score", winner_score=0.04, winner_excess_return=0.02)
    _write_artifact(tmp_path, "four.json", winner="higher_average_excess", winner_score=0.04, winner_excess_return=0.03)
    _write_artifact(tmp_path, "five.json", winner="lower_worst_drawdown", winner_score=0.04, winner_excess_return=0.02)
    _write_artifact(tmp_path, "six.json", winner="a_alpha_first", winner_score=0.04, winner_excess_return=0.02)

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.champion_strategy_id == "higher_average_score"


def test_tournament_champion_calculates_average_score(tmp_path):
    _write_artifact(tmp_path, "one.json", winner="alpha", winner_score=0.02)
    _write_artifact(tmp_path, "two.json", winner="beta", runner_up="alpha", runner_up_score=0.06)

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.champion_strategy_id == "alpha"
    assert result.summary.champion_average_score == pytest.approx(0.04)


def test_tournament_champion_calculates_best_score(tmp_path):
    _write_artifact(tmp_path, "one.json", winner="alpha", winner_score=0.02)
    _write_artifact(tmp_path, "two.json", winner="beta", runner_up="alpha", runner_up_score=0.06)

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.champion_best_score == pytest.approx(0.06)


def test_tournament_champion_calculates_average_excess_return(tmp_path):
    _write_artifact(tmp_path, "one.json", winner="alpha", winner_excess_return=0.01)
    _write_artifact(tmp_path, "two.json", winner="beta", runner_up="alpha", runner_up_excess_return=0.05)

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.champion_average_excess_return == pytest.approx(0.03)


def test_tournament_champion_calculates_worst_drawdown(tmp_path):
    _write_artifact(tmp_path, "one.json", winner="alpha", winner_max_drawdown=-0.01)
    _write_artifact(tmp_path, "two.json", winner="alpha", winner_max_drawdown=-0.07)

    result = load_tournament_champion(tmp_path)

    assert result.summary is not None
    assert result.summary.champion_worst_max_drawdown == pytest.approx(-0.07)


def test_tournament_champion_no_artifact_behavior(tmp_path):
    missing_dir = tmp_path / "missing"
    result = load_tournament_champion(missing_dir)
    output = format_tournament_champion(result, output_dir=missing_dir)

    assert result.summary is None
    assert "No tournament artifacts found." in output
    assert "Traceback" not in output


def test_tournament_champion_all_malformed_artifact_behavior(tmp_path):
    (tmp_path / "bad.json").write_text("{bad json", encoding="utf-8")

    result = load_tournament_champion(tmp_path)
    output = format_tournament_champion(result, output_dir=tmp_path)

    assert result.summary is None
    assert len(result.history.skipped_artifacts) == 1
    assert "No valid tournament artifacts found." in output
    assert "Skipped malformed artifacts:" in output


def test_tournament_champion_mixed_valid_malformed_artifact_behavior(tmp_path):
    (tmp_path / "bad.json").write_text("{bad json", encoding="utf-8")
    _write_artifact(tmp_path, "good.json", winner="alpha")

    result = load_tournament_champion(tmp_path)
    output = format_tournament_champion(result, output_dir=tmp_path)

    assert result.summary is not None
    assert result.summary.champion_strategy_id == "alpha"
    assert result.summary.skipped_artifact_count == 1
    assert "Skipped/malformed artifact count: 1" in output
    assert "bad.json" in output


def test_tournament_champion_cli_output_includes_champion_wins_win_rate_and_average_score(tmp_path):
    _write_artifact(tmp_path, "good.json", winner="alpha", winner_score=0.04)
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "tournament-champion", "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Tournament Champion" in result.stdout
    assert "Champion strategy ID: alpha" in result.stdout
    assert "Champion wins: 1" in result.stdout
    assert "Champion win rate: 100.00%" in result.stdout
    assert "Champion average score: 0.0400" in result.stdout
    assert "Traceback" not in result.stderr


def _write_artifact(
    output_dir,
    filename,
    timestamp="2026-06-10T20:00:00+00:00",
    fixture_name="multi_day",
    winner="alpha",
    winner_score=0.04,
    winner_excess_return=0.04,
    winner_max_drawdown=-0.01,
    runner_up="beta",
    runner_up_score=0.02,
    runner_up_excess_return=0.02,
    runner_up_max_drawdown=-0.01,
    results=None,
):
    artifact_path = output_dir / filename
    payload = {
        "experiment_timestamp": timestamp,
        "fixture_name": fixture_name,
        "score_formula": "score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)",
        "score_explanation": "Higher is better.",
        "results": results or [
            _result_row(
                rank=1,
                strategy_id=winner,
                score=winner_score,
                excess_return=winner_excess_return,
                max_drawdown=winner_max_drawdown,
            ),
            _result_row(
                rank=2,
                strategy_id=runner_up,
                score=runner_up_score,
                excess_return=runner_up_excess_return,
                max_drawdown=runner_up_max_drawdown,
            ),
        ],
    }
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _result_row(rank, strategy_id, score, excess_return=0.03, max_drawdown=-0.01):
    return {
        "rank": rank,
        "strategy_id": strategy_id,
        "run_id": f"{strategy_id}-run",
        "score": score,
        "starting_equity": 10000,
        "current_equity": 10600,
        "strategy_return": 0.06,
        "spy_return": 0.03,
        "excess_return": excess_return,
        "max_drawdown": max_drawdown,
        "trade_count": 2,
        "rejected_trade_count": 0,
        "score_formula": "score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)",
        "score_explanation": "Higher is better.",
    }
