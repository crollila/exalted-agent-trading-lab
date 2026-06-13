import json
from datetime import datetime, timezone

from src.reporting.fixture_sweep import format_fixture_sweep, save_fixture_sweep_artifacts, summarize_fixture_sweep
from src.reporting.fixture_sweep_leaderboard_export import export_fixture_sweep_leaderboard
from src.reporting.leaderboard_export import export_strategy_leaderboard
from src.reporting.strategy_comparison import SCORE_EXPLANATION, SCORE_FORMULA
from src.reporting.strategy_status import load_latest_strategy_statuses, set_strategy_status
from src.reporting.tournament_champion import format_tournament_champion, load_tournament_champion


def test_status_registry_parsing_reuse_returns_latest_status(tmp_path):
    registry_path = tmp_path / "notes" / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="active",
        reason="Initial local baseline",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc),
    )
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs cross-fixture improvement",
        registry_path=registry_path,
        status_timestamp=datetime(2026, 6, 13, 4, 0, tzinfo=timezone.utc),
    )

    assert load_latest_strategy_statuses(registry_path) == {"momentum_v1": "retest"}


def test_missing_status_registry_defaults_to_unknown(tmp_path):
    summary = summarize_fixture_sweep(_sample_ranked_results())

    output = format_fixture_sweep(summary, status_by_strategy=load_latest_strategy_statuses(tmp_path / "missing.md"))

    assert "strategy ID | status" in output
    assert "momentum_v1 | unknown" in output


def test_fixture_sweep_output_includes_status():
    summary = summarize_fixture_sweep(_sample_ranked_results())

    output = format_fixture_sweep(summary, status_by_strategy={"momentum_v1": "retest"})

    assert "strategy ID | status" in output
    assert "momentum_v1 | retest" in output


def test_tournament_champion_output_includes_status(tmp_path):
    _write_tournament_artifact(tmp_path, "one.json", winner="momentum_v1")

    champion = load_tournament_champion(tmp_path)
    output = format_tournament_champion(
        champion,
        output_dir=tmp_path,
        status_by_strategy={"momentum_v1": "retest"},
    )

    assert "Champion strategy ID: momentum_v1" in output
    assert "Champion strategy status: retest" in output


def test_exported_leaderboard_includes_status(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_tournament_artifact(artifacts_dir, "one.json", winner="momentum_v1")
    registry_path = tmp_path / "notes" / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs cross-fixture improvement",
        registry_path=registry_path,
    )
    report_path = tmp_path / "reports" / "leaderboard.md"

    export_strategy_leaderboard(
        output_dir=artifacts_dir,
        report_path=report_path,
        status_registry_path=registry_path,
    )

    markdown = report_path.read_text(encoding="utf-8")
    assert "Champion strategy status: `retest`" in markdown
    assert "| momentum_v1 | retest | 1 | 1 | 100.00%" in markdown


def test_exported_fixture_sweep_leaderboard_includes_status(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_sweep_artifact(artifacts_dir, "fixture_sweep_one.json", champion="momentum_v1")
    registry_path = tmp_path / "notes" / "strategy_status.md"
    set_strategy_status(
        strategy_id="momentum_v1",
        status="retest",
        reason="Needs cross-fixture improvement",
        registry_path=registry_path,
    )
    report_path = tmp_path / "reports" / "fixture_sweep_leaderboard.md"

    export_fixture_sweep_leaderboard(
        output_dir=artifacts_dir,
        report_path=report_path,
        status_registry_path=registry_path,
    )

    markdown = report_path.read_text(encoding="utf-8")
    assert "Champion strategy status: `retest`" in markdown
    assert "| momentum_v1 | retest | 2 | 2 | 100.00%" in markdown


def test_saved_fixture_sweep_artifact_includes_status(tmp_path):
    summary = summarize_fixture_sweep(_sample_ranked_results())

    artifacts = save_fixture_sweep_artifacts(
        summary=summary,
        output_dir=tmp_path,
        generated_at=datetime(2026, 6, 13, 4, 30, tzinfo=timezone.utc),
        status_by_strategy={"momentum_v1": "retest"},
    )

    payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
    assert payload["strategy_statuses"]["momentum_v1"] == "retest"
    assert payload["strategy_statuses"]["cash_only"] == "unknown"

    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    assert "| momentum_v1 | retest |" in markdown


def test_status_annotation_does_not_filter_strategy_execution():
    summary = summarize_fixture_sweep(_sample_ranked_results())

    output = format_fixture_sweep(summary, status_by_strategy={"momentum_v1": "retired"})

    assert "momentum_v1 | retired" in output
    assert "cash_only   | unknown" in output
    assert {aggregate.strategy_id for aggregate in summary.strategy_aggregates} == {"cash_only", "momentum_v1"}


def _sample_ranked_results():
    return {
        "multi_day": [
            _ranked_row("momentum_v1", rank=1, score=0.03, excess_return=0.04, max_drawdown=-0.01),
            _ranked_row("cash_only", rank=2, score=0.0, excess_return=0.0, max_drawdown=0.0),
        ],
        "bear_trend": [
            _ranked_row("cash_only", rank=1, score=0.04, excess_return=0.04, max_drawdown=0.0),
            _ranked_row("momentum_v1", rank=2, score=-0.01, excess_return=0.01, max_drawdown=-0.04),
        ],
    }


def _ranked_row(strategy_id, rank, score, excess_return, max_drawdown):
    return {
        "rank": rank,
        "strategy_id": strategy_id,
        "score": score,
        "excess_return": excess_return,
        "max_drawdown": max_drawdown,
    }


def _write_tournament_artifact(output_dir, filename, winner="momentum_v1"):
    artifact_path = output_dir / filename
    payload = {
        "experiment_timestamp": "2026-06-13T04:00:00+00:00",
        "fixture_name": "multi_day",
        "score_formula": SCORE_FORMULA,
        "score_explanation": SCORE_EXPLANATION,
        "results": [
            _tournament_row(rank=1, strategy_id=winner, score=0.04, excess_return=0.04, max_drawdown=-0.01),
            _tournament_row(rank=2, strategy_id="cash_only", score=0.02, excess_return=0.02, max_drawdown=-0.01),
        ],
    }
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _tournament_row(rank, strategy_id, score, excess_return, max_drawdown):
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
    }


def _write_sweep_artifact(output_dir, filename, champion="momentum_v1"):
    artifact_path = output_dir / filename
    payload = {
        "sweep_timestamp": "2026-06-13T04:00:00+00:00",
        "fixtures_included": ["multi_day", "bear_trend"],
        "score_formula": SCORE_FORMULA,
        "score_explanation": SCORE_EXPLANATION,
        "overall_champion": _aggregate_row(champion, fixture_count=2, wins=2, average_score=0.035),
        "per_fixture_winners": [
            {"fixture_name": "multi_day", "strategy_id": champion, "score": 0.04},
            {"fixture_name": "bear_trend", "strategy_id": champion, "score": 0.03},
        ],
        "strategy_aggregates": [
            _aggregate_row(champion, fixture_count=2, wins=2, average_score=0.035),
            _aggregate_row("cash_only", fixture_count=2, wins=0, average_score=0.015),
        ],
    }
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _aggregate_row(strategy_id, fixture_count, wins, average_score):
    return {
        "strategy_id": strategy_id,
        "fixture_count": fixture_count,
        "wins": wins,
        "average_score": average_score,
        "average_excess_return": 0.05,
        "worst_max_drawdown": -0.02,
    }
