"""Tests for the read-only position review + portfolio health (Phase 7V)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from src.competition import position_review as pr
from src.competition.position_review import build_team_portfolio_review
from src.config.portfolio_limits import PortfolioLimits
from src.reporting.portfolio_review_report import render_review_markdown, format_review_terminal


def _limits(**overrides) -> PortfolioLimits:
    return replace(PortfolioLimits(), **overrides)


def _pos(symbol, qty, avg, current, side="long"):
    mv = qty * current
    cost = qty * avg
    return {
        "symbol": symbol, "qty": qty, "side": side,
        "avg_entry_price": avg, "current_price": current,
        "market_value": mv, "cost_basis": cost,
        "unrealized_pl": mv - cost,
        "unrealized_plpc": (current - avg) / avg if avg else 0.0,
    }


def _attr(symbol, thesis, asset_type="stock_long"):
    return SimpleNamespace(
        symbol=symbol, thesis=thesis, asset_type=asset_type,
        proposal_id=f"prop_{symbol}", timestamp="2026-06-20T10:00:00+00:00",
    )


def test_winner_held_with_visible_reason():
    review = build_team_portfolio_review(
        "team_alpha", equity=100_000, cash=50_000, buying_power=50_000,
        raw_positions=[_pos("AAPL", 100, 100.0, 108.0)],  # +8%, weight 10.8%
        attribution_entries=[_attr("AAPL", "AI leadership")],
        limits=_limits(),
    )
    pos = review.positions[0]
    assert pos.recommended_action == pr.ACTION_HOLD
    assert pos.reason  # a hold must carry a visible reason
    assert pos.thesis_status == pr.THESIS_INTACT


def test_deep_loser_recommended_exit():
    review = build_team_portfolio_review(
        "t", equity=100_000, cash=50_000, buying_power=50_000,
        raw_positions=[_pos("LOSS", 100, 100.0, 80.0)],  # -20% -> invalidated -> exit
        attribution_entries=[_attr("LOSS", "momentum")],
        limits=_limits(),
    )
    pos = review.positions[0]
    assert pos.recommended_action == pr.ACTION_EXIT
    assert pos.thesis_status == pr.THESIS_INVALIDATED


def test_overweight_position_recommended_trim():
    review = build_team_portfolio_review(
        "t", equity=100_000, cash=10_000, buying_power=10_000,
        raw_positions=[_pos("BIG", 500, 100.0, 102.0)],  # weight 51% > 25% alert
        attribution_entries=[_attr("BIG", "thesis")],
        limits=_limits(),
    )
    assert review.positions[0].recommended_action == pr.ACTION_TRIM


def test_short_position_is_watch_only_no_management():
    review = build_team_portfolio_review(
        "t", equity=100_000, cash=50_000, buying_power=50_000,
        raw_positions=[_pos("SHRT", -100, 50.0, 45.0, side="short")],
        attribution_entries=[],
        limits=_limits(),
    )
    pos = review.positions[0]
    assert pos.recommended_action == pr.ACTION_WATCH
    assert "long stock" in pos.reason  # explicitly does not manage shorts


def test_negative_cash_blocks_new_buys():
    review = build_team_portfolio_review(
        "team_alpha", equity=80_000, cash=-150_000, buying_power=0.0,
        raw_positions=[_pos("NVDA", 1000, 200.0, 195.0)],
        attribution_entries=[_attr("NVDA", "AI")],
        limits=_limits(),
    )
    assert review.health.block_new_buys is True
    assert review.health.negative_cash is True
    assert review.health.zero_buying_power is True
    assert any("Negative cash" in p for p in review.health.critical_problems)


def test_low_bp_blocks_buys_but_review_still_recommends_reductions():
    # The whole point: a no-buying-power team is NOT stuck — it can still recommend trims/exits.
    review = build_team_portfolio_review(
        "team_alpha", equity=80_000, cash=-150_000, buying_power=0.0,
        raw_positions=[
            _pos("NVDA", 1000, 200.0, 195.0),   # overweight -> trim
            _pos("LOSS", 100, 100.0, 70.0),     # -30% -> exit
        ],
        attribution_entries=[_attr("NVDA", "AI"), _attr("LOSS", "momentum")],
        limits=_limits(),
    )
    actions = {p.symbol: p.recommended_action for p in review.positions}
    assert actions["LOSS"] == pr.ACTION_EXIT
    assert actions["NVDA"] == pr.ACTION_TRIM
    assert review.health.block_new_buys is True


def test_missing_thesis_flagged_as_problem():
    review = build_team_portfolio_review(
        "t", equity=100_000, cash=50_000, buying_power=50_000,
        raw_positions=[_pos("MYST", 10, 100.0, 101.0)],
        attribution_entries=[],  # no thesis on file
        limits=_limits(),
    )
    assert review.positions[0].thesis_status == pr.THESIS_UNKNOWN
    assert any("Missing/stale local thesis" in p for p in review.health.critical_problems)


def test_review_orders_existing_positions_worst_first():
    review = build_team_portfolio_review(
        "t", equity=100_000, cash=50_000, buying_power=50_000,
        raw_positions=[_pos("WIN", 10, 100.0, 130.0), _pos("LOSE", 10, 100.0, 70.0)],
        attribution_entries=[],
        limits=_limits(),
    )
    # Worst (most negative return) first so the report leads with what needs action.
    assert review.positions[0].symbol == "LOSE"


def test_report_has_no_secret_like_values():
    review = build_team_portfolio_review(
        "team_alpha", equity=100_000, cash=50_000, buying_power=50_000,
        raw_positions=[_pos("AAPL", 100, 100.0, 108.0)],
        attribution_entries=[_attr("AAPL", "thesis")],
        limits=_limits(),
    )
    for rendered in (render_review_markdown(review), format_review_terminal(review)):
        low = rendered.lower()
        for needle in ("secret", "api_key", "apikey", "authorization", "bearer", "password"):
            assert needle not in low


def test_thesis_status_classification():
    assert pr.classify_thesis_status(True, 0.05, stop_loss_pct=-0.15, weakening_pct=-0.05) == pr.THESIS_INTACT
    assert pr.classify_thesis_status(True, -0.08, stop_loss_pct=-0.15, weakening_pct=-0.05) == pr.THESIS_WEAKENING
    assert pr.classify_thesis_status(True, -0.20, stop_loss_pct=-0.15, weakening_pct=-0.05) == pr.THESIS_INVALIDATED
    assert pr.classify_thesis_status(False, 0.05, stop_loss_pct=-0.15, weakening_pct=-0.05) == pr.THESIS_UNKNOWN
