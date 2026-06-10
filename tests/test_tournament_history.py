import json
import os
import subprocess
import sys

from src.reporting.tournament_history import format_tournament_history, load_tournament_history


def test_tournament_history_loads_one_artifact(tmp_path):
    artifact = _write_artifact(tmp_path, "one.json", timestamp="2026-06-10T20:00:00+00:00")

    history = load_tournament_history(tmp_path)

    assert len(history.entries) == 1
    entry = history.entries[0]
    assert entry.experiment_timestamp == "2026-06-10T20:00:00+00:00"
    assert entry.fixture_name == "multi_day"
    assert entry.strategy_count == 2
    assert entry.winning_strategy_id == "winner"
    assert entry.winning_score == 0.03
    assert entry.winning_strategy_return == 0.06
    assert entry.winning_spy_return == 0.03
    assert entry.winning_excess_return == 0.03
    assert entry.winning_max_drawdown == -0.01
    assert entry.artifact_path == artifact
    assert history.skipped_artifacts == []


def test_tournament_history_loads_multiple_artifacts(tmp_path):
    _write_artifact(tmp_path, "older.json", timestamp="2026-06-09T20:00:00+00:00")
    _write_artifact(tmp_path, "newer.json", timestamp="2026-06-10T20:00:00+00:00")

    history = load_tournament_history(tmp_path)

    assert len(history.entries) == 2


def test_tournament_history_detects_winner_from_ranked_rows(tmp_path):
    _write_artifact(
        tmp_path,
        "ranked.json",
        results=[
            _result_row(rank=2, strategy_id="higher_score_but_not_ranked_winner", score=0.99),
            _result_row(rank=1, strategy_id="rank_one_winner", score=0.01),
        ],
    )

    history = load_tournament_history(tmp_path)

    assert history.entries[0].winning_strategy_id == "rank_one_winner"
    assert history.entries[0].winning_score == 0.01


def test_tournament_history_sorts_newest_first_deterministically(tmp_path):
    _write_artifact(tmp_path, "b_same_time.json", timestamp="2026-06-10T20:00:00+00:00")
    _write_artifact(tmp_path, "a_same_time.json", timestamp="2026-06-10T20:00:00+00:00")
    _write_artifact(tmp_path, "newest.json", timestamp="2026-06-11T20:00:00+00:00")
    _write_artifact(tmp_path, "oldest.json", timestamp="2026-06-09T20:00:00+00:00")

    history = load_tournament_history(tmp_path)

    assert [entry.artifact_path.name for entry in history.entries] == [
        "newest.json",
        "a_same_time.json",
        "b_same_time.json",
        "oldest.json",
    ]


def test_tournament_history_no_artifact_behavior(tmp_path):
    history = load_tournament_history(tmp_path)
    output = format_tournament_history(history, output_dir=tmp_path)

    assert history.entries == []
    assert "Tournament History" in output
    assert "No valid tournament artifacts found." in output


def test_tournament_history_malformed_artifact_behavior(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not valid json", encoding="utf-8")
    _write_artifact(tmp_path, "valid.json")

    history = load_tournament_history(tmp_path)
    output = format_tournament_history(history, output_dir=tmp_path)

    assert len(history.entries) == 1
    assert len(history.skipped_artifacts) == 1
    assert history.skipped_artifacts[0].artifact_path == malformed
    assert "Skipped malformed artifacts:" in output
    assert "malformed.json" in output


def test_tournament_history_cli_output_includes_winner_and_score(tmp_path):
    _write_artifact(tmp_path, "artifact.json", timestamp="2026-06-10T20:00:00+00:00")
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [sys.executable, "-m", "src.main", "tournament-history", "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Tournament History" in result.stdout
    assert "winner" in result.stdout
    assert "0.0300" in result.stdout
    assert "Traceback" not in result.stderr


def test_tournament_history_cli_empty_directory_has_no_stack_trace(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "tournament-history", "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "No valid tournament artifacts found." in result.stdout
    assert "Traceback" not in result.stderr


def _write_artifact(
    output_dir,
    filename,
    timestamp="2026-06-10T20:00:00+00:00",
    fixture_name="multi_day",
    results=None,
):
    artifact_path = output_dir / filename
    payload = {
        "experiment_timestamp": timestamp,
        "fixture_name": fixture_name,
        "score_formula": "score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)",
        "score_explanation": "Higher is better.",
        "results": results or [
            _result_row(rank=1, strategy_id="winner", score=0.03),
            _result_row(rank=2, strategy_id="runner_up", score=0.01),
        ],
    }
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _result_row(rank, strategy_id, score):
    return {
        "rank": rank,
        "strategy_id": strategy_id,
        "run_id": f"{strategy_id}-run",
        "score": score,
        "starting_equity": 10000,
        "current_equity": 10600,
        "strategy_return": 0.06,
        "spy_return": 0.03,
        "excess_return": 0.03,
        "max_drawdown": -0.01,
        "trade_count": 2,
        "rejected_trade_count": 0,
        "score_formula": "score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)",
        "score_explanation": "Higher is better.",
    }
