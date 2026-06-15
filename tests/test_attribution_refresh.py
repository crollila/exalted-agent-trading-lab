"""Proposal attribution outcome refresh. Deterministic; no network/credentials/market hours.

Covers: CLI runs with mocked data, backward-compatible loading of old rows,
worked/failed/mixed/pending thresholding, SPY-relative excess over the same
period, missing-price skips, summary counts, LLM outcome feedback wiring, no
secrets in output, and spreads still refusing safely.
"""

from __future__ import annotations

import json

import pytest

import src.main as main
from src.competition.attribution import (
    DEFAULT_OUTCOME_THRESHOLD,
    ProposalAttribution,
    load_team_attribution,
    performance_feedback,
    refresh_team_attribution,
)


def _attr(**over):
    base = dict(
        proposal_id="p1", team_id="team_alpha", strategy_id="s", asset_type="stock_long",
        symbol="NVDA", thesis="t", cycle_id="c1",
    )
    base.update(over)
    return ProposalAttribution(**base)


def _price_fn(prices: dict[str, float]):
    def fn(symbol: str) -> float:
        return prices[symbol.upper()]

    return fn


# --- backward compatibility -------------------------------------------------


def test_old_record_without_new_fields_loads(tmp_path):
    """A pre-refresh JSONL row (no outcome_status/refreshed_at/etc.) still loads."""

    old_row = {
        "proposal_id": "old1", "team_id": "team_alpha", "strategy_id": "s",
        "asset_type": "stock_long", "symbol": "AAPL", "thesis": "t", "cycle_id": "c0",
        "entry_price": 100.0, "thesis_outcome": "worked", "return_pct": 0.1,
    }
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(old_row) + "\n", encoding="utf-8")

    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].symbol == "AAPL"
    assert loaded[0].outcome_status == "worked"  # mirrored from legacy thesis_outcome
    assert loaded[0].refreshed_at is None


def test_unknown_extra_keys_are_ignored(tmp_path):
    row = {**_attr().as_dict(), "some_future_field": 123}
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].proposal_id == "p1"


# --- thresholding -----------------------------------------------------------


def test_pending_becomes_worked_when_excess_positive(tmp_path):
    main_dir = tmp_path
    path = main_dir / "team_alpha_attribution.jsonl"
    path.write_text(
        json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8"
    )
    summary = refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 120.0}),
        spy_start_price=400.0,
        spy_current_price=404.0,  # SPY +1%, NVDA +20% -> excess +19%
        attribution_dir=main_dir,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=main_dir)
    assert loaded[0].outcome_status == "worked"
    assert loaded[0].return_pct == pytest.approx(0.20)
    assert loaded[0].excess_return_pct == pytest.approx(0.19)
    assert summary.worked == 1
    assert summary.refreshed == 1


def test_pending_becomes_failed_when_excess_negative(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8")
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 90.0}),  # -10%
        spy_start_price=400.0,
        spy_current_price=420.0,  # SPY +5% -> excess -15%
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].outcome_status == "failed"
    assert loaded[0].excess_return_pct == pytest.approx(-0.15)


def test_near_flat_excess_becomes_mixed(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    # NVDA +1%, SPY +0.9% -> excess +0.1% < default threshold (0.5%) -> mixed.
    path.write_text(json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8")
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 101.0}),
        spy_start_price=400.0,
        spy_current_price=403.6,
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].outcome_status == "mixed"
    assert abs(loaded[0].excess_return_pct) < DEFAULT_OUTCOME_THRESHOLD


def test_short_inverts_return(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(
        json.dumps(_attr(asset_type="stock_short", symbol="XYZ", entry_price=100.0).as_dict()) + "\n",
        encoding="utf-8",
    )
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"XYZ": 90.0}),  # price fell 10% -> short +10%
        spy_start_price=400.0,
        spy_current_price=400.0,  # SPY flat -> excess +10%
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].return_pct == pytest.approx(0.10)
    assert loaded[0].outcome_status == "worked"


# --- pending / skip reasons -------------------------------------------------


def test_missing_price_keeps_pending_with_reason(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8")

    def boom(symbol):
        raise RuntimeError("no data")

    summary = refresh_team_attribution(
        "team_alpha",
        price_fn=boom,
        spy_start_price=400.0,
        spy_current_price=404.0,
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].outcome_status == "pending"
    assert loaded[0].refresh_skip_reason is not None
    assert summary.pending == 1
    assert summary.skipped and "current price" in summary.skipped[0].reason


def test_missing_spy_keeps_pending(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8")
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 120.0}),
        spy_start_price=None,  # no benchmark recorded
        spy_current_price=None,
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].outcome_status == "pending"
    assert loaded[0].return_pct == pytest.approx(0.20)  # return still computed
    assert "SPY" in loaded[0].refresh_skip_reason


def test_options_left_pending(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(
        json.dumps(_attr(asset_type="option_long_call", entry_price=100.0).as_dict()) + "\n",
        encoding="utf-8",
    )
    summary = refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 120.0}),
        spy_start_price=400.0,
        spy_current_price=404.0,
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].outcome_status == "pending"
    assert "option" in loaded[0].refresh_skip_reason.lower()
    assert summary.pending == 1


def test_missing_entry_price_keeps_pending(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(_attr(entry_price=None).as_dict()) + "\n", encoding="utf-8")
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 120.0}),
        spy_start_price=400.0,
        spy_current_price=404.0,
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].outcome_status == "pending"
    assert "entry" in loaded[0].refresh_skip_reason


# --- summary + pnl ----------------------------------------------------------


def test_summary_best_worst_and_counts(tmp_path):
    rows = [
        _attr(proposal_id="a", symbol="NVDA", entry_price=100.0),
        _attr(proposal_id="b", symbol="TSLA", entry_price=100.0),
    ]
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text("\n".join(json.dumps(r.as_dict()) for r in rows) + "\n", encoding="utf-8")
    summary = refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 130.0, "TSLA": 80.0}),
        spy_start_price=400.0,
        spy_current_price=400.0,  # SPY flat
        attribution_dir=tmp_path,
    )
    assert summary.scanned == 2
    assert summary.refreshed == 2
    assert summary.worked == 1
    assert summary.failed == 1
    assert summary.best.symbol == "NVDA"
    assert summary.worst.symbol == "TSLA"
    assert summary.spy_return_pct == pytest.approx(0.0)


def test_unrealized_pnl_uses_quantity(tmp_path):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(
        json.dumps(_attr(entry_price=100.0, quantity=10.0).as_dict()) + "\n", encoding="utf-8"
    )
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 110.0}),
        spy_start_price=400.0,
        spy_current_price=400.0,
        attribution_dir=tmp_path,
    )
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].unrealized_pnl == pytest.approx(100.0)  # (110-100) * 10


def test_refresh_empty_team_is_safe(tmp_path):
    summary = refresh_team_attribution(
        "team_beta",
        price_fn=_price_fn({}),
        spy_start_price=400.0,
        spy_current_price=404.0,
        attribution_dir=tmp_path,
    )
    assert summary.scanned == 0
    assert summary.refreshed == 0


# --- LLM context wiring -----------------------------------------------------


def test_llm_context_includes_outcome_feedback_when_refreshed(tmp_path):
    from src.competition.llm_cycle import build_llm_context

    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(
        json.dumps(_attr(entry_price=100.0, research_source_ids=["r1"]).as_dict()) + "\n",
        encoding="utf-8",
    )
    refresh_team_attribution(
        "team_alpha",
        price_fn=_price_fn({"NVDA": 130.0}),
        spy_start_price=400.0,
        spy_current_price=400.0,
        attribution_dir=tmp_path,
    )
    context = build_llm_context("team_alpha", client=None, price_fn=None, attribution_dir=tmp_path)
    ofb = context["performance_feedback"]["outcome_feedback"]
    assert ofb["refreshed_count"] == 1
    assert ofb["worked_count"] == 1
    assert "r1" in ofb["winning_themes"]
    assert "Research feedback only" in ofb["note"]


def test_llm_context_does_not_crash_without_outcomes(tmp_path):
    from src.competition.llm_cycle import build_llm_context

    context = build_llm_context("team_beta", client=None, price_fn=None, attribution_dir=tmp_path)
    ofb = context["performance_feedback"]["outcome_feedback"]
    assert ofb["refreshed_count"] == 0
    assert ofb["best_recent_worked"] == []


# --- CLI --------------------------------------------------------------------


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(main, "load_cli_dotenv", lambda: None)
    monkeypatch.setattr("sys.argv", ["prog", *argv])
    main.main()


def test_cli_refresh_runs_with_mocked_data(monkeypatch, tmp_path, capsys):
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8")

    monkeypatch.setattr(main, "_market_data_price_fn", lambda settings: _price_fn({"NVDA": 130.0, "SPY": 404.0}))
    monkeypatch.setattr(
        main, "load_competition_state",
        lambda *a, **k: type("S", (), {"starting_spy_price": 400.0})(),
    )
    # Redirect both teams' attribution to tmp by patching the default dir.
    monkeypatch.setattr(
        main, "refresh_team_attribution",
        lambda team, **kw: refresh_team_attribution(team, attribution_dir=tmp_path, **kw),
    )

    _run_cli(monkeypatch, ["refresh-proposal-attribution", "--team", "team_alpha"])
    out = capsys.readouterr().out
    assert "Records scanned: 1" in out
    assert "Worked: 1" in out
    assert "Best proposal by excess: NVDA" in out


def test_cli_refresh_fails_safely_without_market_data(monkeypatch, capsys):
    monkeypatch.setattr(main, "_market_data_price_fn", lambda settings: None)
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, ["refresh-proposal-attribution"])
    out = capsys.readouterr().out
    assert "Market data unavailable" in out


def test_cli_refresh_help_no_secrets(monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_SECRET_KEY", "SECRET-SHOULD-NOT-PRINT")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SECRET-SHOULD-NOT-PRINT")
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, ["refresh-proposal-attribution", "--help"])
    out = capsys.readouterr().out
    assert "SECRET-SHOULD-NOT-PRINT" not in out


def test_no_secrets_in_refresh_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ALPACA_SECRET_KEY", "SECRET-SHOULD-NOT-PRINT")
    path = tmp_path / "team_alpha_attribution.jsonl"
    path.write_text(json.dumps(_attr(entry_price=100.0).as_dict()) + "\n", encoding="utf-8")

    monkeypatch.setattr(main, "_market_data_price_fn", lambda settings: _price_fn({"NVDA": 130.0, "SPY": 404.0}))
    monkeypatch.setattr(
        main, "load_competition_state",
        lambda *a, **k: type("S", (), {"starting_spy_price": 400.0})(),
    )
    monkeypatch.setattr(
        main, "refresh_team_attribution",
        lambda team, **kw: refresh_team_attribution(team, attribution_dir=tmp_path, **kw),
    )
    _run_cli(monkeypatch, ["refresh-proposal-attribution", "--team", "team_alpha"])
    out = capsys.readouterr().out
    assert "SECRET-SHOULD-NOT-PRINT" not in out


# --- spreads still refuse safely -------------------------------------------


def test_spreads_still_refuse_safely():
    from src.brokers.options_adapter import OptionsExecutionAdapter

    adapter = OptionsExecutionAdapter(enabled=True, enable_spreads=False)
    assert adapter.single_leg_enabled is True
    assert adapter.spreads_enabled is False
