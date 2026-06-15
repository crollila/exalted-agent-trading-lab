"""Phase 7N: cheap cycle gate, review-only mode, daily SPY attribution + review.

Deterministic; mocked data only. No network, no real Alpaca, no OpenAI calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import src.main as main
from src.competition.attribution import ProposalAttribution
from src.competition.cycle_gate import (
    CheapCycleGateConfig,
    GateDecision,
    evaluate_cheap_cycle_gate,
)
from src.competition.daily_review import (
    DEFAULT_REVIEWS_DIR,
    bucket_for,
    build_daily_team_review,
    compute_daily_spy_attribution,
    daily_review_context,
    export_daily_team_review,
    load_latest_daily_team_review,
)
from src.competition.portfolio_manager import PortfolioManagerConfig
from src.competition.risk_engine import AccountContext
from src.competition.scorecard import TeamScorecard, save_scorecard
from src.competition.week_competition import run_week_cycle
from src.config.permissions import TradingPermissions
from src.learning.team_memory import TeamLearningLedger


def _now():
    return datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _ago(minutes: int) -> str:
    return (_now() - timedelta(minutes=minutes)).isoformat()


def cfg(**o):
    return CheapCycleGateConfig(enabled=True, **o)


# --- cheap cycle gate -------------------------------------------------------


def test_gate_disabled_always_runs():
    d = evaluate_cheap_cycle_gate("team_alpha", config=CheapCycleGateConfig(enabled=False), now=_now())
    assert d.should_run_full_cycle is True
    assert "disabled" in d.reason.lower()


def test_gate_skips_when_nothing_changed_and_interval_too_short():
    d = evaluate_cheap_cycle_gate(
        "team_alpha", config=cfg(), last_full_cycle_at=_ago(5), now=_now()
    )
    assert d.should_run_full_cycle is False
    assert d.recommended_wait_minutes > 0
    assert "interval_not_elapsed" in d.trigger_flags


def test_gate_runs_after_minimum_interval():
    d = evaluate_cheap_cycle_gate(
        "team_alpha", config=cfg(), last_full_cycle_at=_ago(45), now=_now()
    )
    assert d.should_run_full_cycle is True
    assert "interval_elapsed" in d.trigger_flags


def test_alpha_has_shorter_interval_than_beta():
    config = cfg()
    assert config.interval_for("team_alpha") < config.interval_for("team_beta")
    # At 35m elapsed: alpha (30m) runs, beta (45m) still waits -> alpha more exploratory.
    alpha = evaluate_cheap_cycle_gate("team_alpha", config=config, last_full_cycle_at=_ago(35), now=_now())
    beta = evaluate_cheap_cycle_gate("team_beta", config=config, last_full_cycle_at=_ago(35), now=_now())
    assert alpha.should_run_full_cycle is True
    assert beta.should_run_full_cycle is False


def test_major_spy_move_triggers_full_cycle_when_enabled():
    d = evaluate_cheap_cycle_gate(
        "team_beta",
        config=cfg(force_full_cycle_on_major_move=True, major_spy_move_threshold_pct=0.5),
        last_full_cycle_at=_ago(5),  # well within interval
        spy_move_pct=0.9,
        now=_now(),
    )
    assert d.should_run_full_cycle is True
    assert "major_spy_move" in d.trigger_flags


def test_major_spy_move_ignored_when_disabled():
    d = evaluate_cheap_cycle_gate(
        "team_beta",
        config=cfg(force_full_cycle_on_major_move=False),
        last_full_cycle_at=_ago(5),
        spy_move_pct=2.0,
        now=_now(),
    )
    assert d.should_run_full_cycle is False


def test_low_buying_power_recommends_review_not_forced_orders():
    # force_full_cycle_on_low_buying_power defaults False -> review, not full cycle.
    d = evaluate_cheap_cycle_gate(
        "team_alpha",
        config=cfg(),
        last_full_cycle_at=_ago(5),  # interval not elapsed
        low_buying_power=True,
        now=_now(),
    )
    assert d.should_run_full_cycle is False  # not forced into a full trading cycle
    assert d.recommend_review_only is True
    assert "low_buying_power_review" in d.trigger_flags


def test_broker_rejections_force_full_cycle():
    d = evaluate_cheap_cycle_gate(
        "team_alpha", config=cfg(), last_full_cycle_at=_ago(2), broker_rejections=3, now=_now()
    )
    assert d.should_run_full_cycle is True
    assert "broker_rejections" in d.trigger_flags


def test_gate_config_from_env_defaults():
    c = CheapCycleGateConfig.from_env(env={})
    assert c.enabled is False
    assert c.min_full_cycle_interval_minutes_alpha == 30
    assert c.min_full_cycle_interval_minutes_beta == 45
    assert c.force_full_cycle_on_major_move is True
    assert c.major_spy_move_threshold_pct == 0.5
    assert c.force_full_cycle_on_low_buying_power is False


# --- review-only cycle ------------------------------------------------------


def _dirs(tmp_path):
    return {
        "competition_dir": tmp_path / "comp",
        "scorecard_dir": tmp_path / "sc",
        "learning_dir": tmp_path / "learn",
        "kill_switch_path": str(tmp_path / "ks.json"),
        "attribution_dir": tmp_path / "attr",
    }


def perms(**o):
    base = {"MAX_DAILY_ORDERS_PER_TEAM": 10, "ENABLE_PAPER_SHORTING": "true"}
    base.update(o)
    return TradingPermissions.from_env(env={k: str(v) for k, v in base.items()})


def healthy_account():
    return AccountContext(equity=1_000_000.0, cash=1_000_000.0, buying_power=2_000_000.0)


class _ExplodingClient:
    """Any broker submission attempt is a test failure in review-only mode."""

    def submit_paper_order(self, order):
        raise AssertionError("review-only must not submit broker orders")

    submit_paper_short_order = submit_paper_order
    submit_paper_margin_order = submit_paper_order
    submit_paper_option_order = submit_paper_order


def test_review_only_does_not_call_broker_execution(tmp_path):
    d = _dirs(tmp_path)
    result = run_week_cycle(
        "team_alpha",
        permissions=perms(),
        account=healthy_account(),
        client=_ExplodingClient(),
        dry_run=False,  # would normally submit; review_only must still skip
        review_only=True,
        **d,
    )
    assert result.review_only is True
    assert result.no_trade is True
    assert result.execution_records == []
    assert sum(1 for r in result.execution_records if r.submitted) == 0


def test_review_only_still_updates_memory_and_scorecard(tmp_path):
    d = _dirs(tmp_path)
    run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        dry_run=True, review_only=True, **d,
    )
    from src.competition.scorecard import load_latest_scorecard

    assert load_latest_scorecard("team_alpha", d["scorecard_dir"]) is not None
    ledger = TeamLearningLedger.load("team_alpha", d["learning_dir"])
    assert len(ledger.reviews) == 1
    # Review-only does NOT reset the full-cycle timer.
    assert ledger.last_full_cycle_at == ""


def test_full_cycle_marks_last_full_cycle_at(tmp_path):
    d = _dirs(tmp_path)
    run_week_cycle(
        "team_alpha", permissions=perms(), account=healthy_account(),
        dry_run=True, review_only=False, **d,
    )
    ledger = TeamLearningLedger.load("team_alpha", d["learning_dir"])
    assert ledger.last_full_cycle_at != ""


# --- daily SPY attribution --------------------------------------------------


def _entry(**over):
    base = dict(
        proposal_id="p", team_id="team_alpha", strategy_id="s", asset_type="stock_long",
        symbol="NVDA", thesis="t", cycle_id="c1",
    )
    base.update(over)
    return ProposalAttribution(**base)


def _scorecard(**over):
    base = dict(
        team_id="team_alpha", week_start="x", week_end="y",
        starting_equity=1000.0, current_equity=1100.0, spy_benchmark_return=0.05,
    )
    base.update(over)
    card = TeamScorecard(**base)
    card.compute_excess_return()
    return card


def test_daily_spy_attribution_computes_excess():
    card = _scorecard(current_equity=1100.0, spy_benchmark_return=0.05)  # team +10%, spy +5%
    a = compute_daily_spy_attribution("team_alpha", scorecard=card, entries=[])
    assert a.team_return == pytest.approx(0.10)
    assert a.spy_return == pytest.approx(0.05)
    assert a.excess_return == pytest.approx(0.05)


def test_daily_spy_attribution_top_winners_losers():
    entries = [
        _entry(symbol="NVDA", asset_type="stock_long", return_pct=0.20, excess_return_pct=0.18),
        _entry(symbol="TSLA", asset_type="stock_short", return_pct=-0.15, excess_return_pct=-0.17),
        _entry(symbol="MSFT", asset_type="stock_long", return_pct=0.03, excess_return_pct=0.01),
    ]
    a = compute_daily_spy_attribution("team_alpha", scorecard=_scorecard(), entries=entries)
    assert a.top_winners[0]["symbol"] == "NVDA"
    assert a.top_losers[0]["symbol"] == "TSLA"
    assert a.top_winners[0]["bucket"] == "semis_ai"


def test_broker_rejections_appear_as_drag():
    entries = [
        _entry(symbol="NVDA", asset_type="stock_long", return_pct=-0.02, excess_return_pct=-0.05),
        _entry(symbol="AMD", broker_rejected=True, failure_category="insufficient_buying_power"),
    ]
    card = _scorecard(current_equity=950.0, spy_benchmark_return=0.02)  # underperform
    a = compute_daily_spy_attribution("team_alpha", scorecard=card, entries=entries)
    assert a.broker_rejections == 1
    assert "insufficient_buying_power" in a.broker_rejection_categories
    assert "broker_rejections" in a.drivers


def test_symbol_bucket_grouping():
    assert bucket_for("SPY") == "index_etf"
    assert bucket_for("NVDA") == "semis_ai"
    assert bucket_for("MSFT") == "megacap_software_cloud"
    assert bucket_for("TSLA") == "high_beta_auto_ev"
    assert bucket_for("WIDGET") == "unknown"


def test_daily_attribution_missing_data_does_not_crash():
    a = compute_daily_spy_attribution("team_beta", scorecard=None, entries=[])
    assert a.team_return is None
    assert a.top_winners == []
    assert a.explanation  # still produces an explanation string


# --- daily review artifact (temp dirs) --------------------------------------


def test_daily_review_artifact_written_under_temp_dir(tmp_path):
    sc, attr, learn, reviews = (tmp_path / d for d in ("sc", "attr", "learn", "reviews"))
    save_scorecard(_scorecard(current_equity=900.0, spy_benchmark_return=0.05), scorecard_dir=sc)
    # one refreshed attribution entry
    attr.mkdir(parents=True)
    (attr / "team_alpha_attribution.jsonl").write_text(
        json.dumps(_entry(symbol="TSLA", asset_type="stock_short", return_pct=-0.1,
                          excess_return_pct=-0.15, refreshed_at="2026-06-15T00:00:00+00:00",
                          outcome_status="failed").as_dict()) + "\n",
        encoding="utf-8",
    )
    review = export_daily_team_review(
        "team_alpha", scorecard_dir=sc, attribution_dir=attr, learning_dir=learn, reviews_dir=reviews
    )
    assert (reviews / "team_alpha_latest.json").exists()
    assert review.spy_relative_result.startswith("trailed")  # team 900 vs 1000 start, spy +5%
    loaded = load_latest_daily_team_review("team_alpha", reviews_dir=reviews)
    assert loaded is not None
    assert loaded.recommended_mode in ("exploration", "conservation")


def test_daily_review_context_compact_and_present(tmp_path):
    reviews, learn = tmp_path / "reviews", tmp_path / "learn"
    review = build_daily_team_review(
        "team_alpha",
        attribution=compute_daily_spy_attribution(
            "team_alpha",
            scorecard=_scorecard(current_equity=1100.0, spy_benchmark_return=0.03),
            entries=[_entry(symbol="NVDA", asset_type="stock_long", return_pct=0.2, excess_return_pct=0.17)],
        ),
        feedback={"outcome_feedback": {"worked_count": 2, "failed_count": 0}},
        ledger=TeamLearningLedger(team_id="team_alpha", mode="exploration"),
    )
    from src.competition.daily_review import save_daily_team_review

    save_daily_team_review(review, reviews_dir=reviews)
    ctx = daily_review_context("team_alpha", reviews_dir=reviews, learning_dir=learn)
    assert ctx["available"] is True
    assert "spy_relative_result" in ctx
    assert "never bypass risk" in ctx["note"].lower()
    # Compact: small number of keys.
    assert len(ctx) <= 9


def test_daily_review_context_safe_without_data(tmp_path):
    ctx = daily_review_context("team_beta", reviews_dir=tmp_path / "reviews", learning_dir=tmp_path / "learn")
    assert ctx["available"] is False


def test_llm_context_includes_daily_review(tmp_path):
    from src.competition.daily_review import save_daily_team_review
    from src.competition.llm_cycle import build_llm_context

    reviews = tmp_path / "reviews"
    review = build_daily_team_review(
        "team_alpha",
        attribution=compute_daily_spy_attribution(
            "team_alpha", scorecard=_scorecard(), entries=[]
        ),
        feedback={},
        ledger=TeamLearningLedger(team_id="team_alpha"),
    )
    save_daily_team_review(review, reviews_dir=reviews)
    ctx = build_llm_context(
        "team_alpha", client=None, price_fn=None,
        attribution_dir=tmp_path / "attr", reviews_dir=reviews, learning_dir=tmp_path / "learn",
    )
    assert "daily_review" in ctx
    assert ctx["daily_review"]["available"] is True


# --- CLI smoke + no secrets -------------------------------------------------


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    main.main()


def test_cli_cheap_cycle_gate_no_secrets(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ALPACA_SECRET_KEY", "SECRET-SHOULD-NOT-PRINT")
    monkeypatch.setenv("CHEAP_CYCLE_GATE_ENABLED", "true")
    # Point team memory / scorecards at empty temp dirs by patching loaders.
    monkeypatch.setattr(main, "load_latest_scorecard", lambda *a, **k: None)
    monkeypatch.setattr(main, "performance_feedback", lambda *a, **k: {})
    monkeypatch.setattr(
        main.TeamLearningLedger, "load", classmethod(lambda cls, team, *a, **k: cls(team_id=team))
    )
    _run_cli(monkeypatch, ["cheap-cycle-gate", "--team", "team_alpha"])
    out = capsys.readouterr().out
    assert "should_run_full_cycle" in out
    assert "SECRET-SHOULD-NOT-PRINT" not in out


def test_cli_daily_spy_attribution_runs(monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_SECRET_KEY", "SECRET-SHOULD-NOT-PRINT")
    _run_cli(monkeypatch, ["daily-spy-attribution", "--team", "team_alpha"])
    out = capsys.readouterr().out
    assert "Daily SPY attribution" in out
    assert "SECRET-SHOULD-NOT-PRINT" not in out


def test_cli_review_only_flag_dispatches(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main, "run_week_cycle_cli",
        lambda team, proposal_source, review_only: captured.update(
            team=team, src=proposal_source, review=review_only
        ),
    )
    _run_cli(monkeypatch, ["run-week-cycle", "--team", "team_alpha", "--proposal-source", "llm", "--review-only"])
    assert captured == {"team": "team_alpha", "src": "llm", "review": True}


# --- spreads still refuse safely -------------------------------------------


def test_spreads_still_refuse_safely():
    from src.brokers.options_adapter import OptionsExecutionAdapter

    adapter = OptionsExecutionAdapter(enabled=True, enable_spreads=False)
    assert adapter.single_leg_enabled is True
    assert adapter.spreads_enabled is False
