"""Proposal attribution + effectiveness tracking. No real network."""

from __future__ import annotations

from src.competition.attribution import (
    ProposalAttribution,
    load_team_attribution,
    performance_feedback,
    record_attributions,
)


def _attr(**over):
    base = dict(
        proposal_id="p1", team_id="team_alpha", strategy_id="s", asset_type="stock_long",
        symbol="NVDA", thesis="t", cycle_id="c1",
    )
    base.update(over)
    return ProposalAttribution(**base)


def test_records_source_ids(tmp_path):
    record_attributions([_attr(research_source_ids=["r1", "r2"])], attribution_dir=tmp_path)
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert loaded[0].research_source_ids == ["r1", "r2"]


def test_non_string_order_id_serializes(tmp_path):
    import uuid

    record_attributions([_attr(order_id=uuid.uuid4())], attribution_dir=tmp_path)
    loaded = load_team_attribution("team_alpha", attribution_dir=tmp_path)
    assert isinstance(loaded[0].order_id, str)


def test_computes_return_and_excess_when_prices_exist():
    entry = _attr(entry_price=100.0, current_price=110.0, spy_return=0.05)
    entry.compute_outcome()
    assert abs(entry.return_pct - 0.10) < 1e-9
    assert abs(entry.excess_return_vs_spy - 0.05) < 1e-9
    assert entry.thesis_outcome == "worked"


def test_short_outcome_inverts_return():
    entry = _attr(asset_type="stock_short", entry_price=100.0, current_price=90.0, spy_return=0.0)
    entry.compute_outcome()
    assert entry.return_pct > 0  # short profits when price falls
    assert entry.thesis_outcome == "worked"


def test_pending_when_prices_unavailable():
    entry = _attr(entry_price=100.0, current_price=None)
    entry.compute_outcome()
    assert entry.return_pct is None
    assert entry.thesis_outcome == "pending"


def test_performance_feedback_best_worst(tmp_path):
    record_attributions(
        [
            _attr(proposal_id="a", symbol="NVDA", asset_type="stock_long", entry_price=100, current_price=120, spy_return=0.0),
            _attr(proposal_id="b", symbol="TSLA", asset_type="stock_short", entry_price=100, current_price=120, spy_return=0.0),
            _attr(proposal_id="c", symbol="META", asset_type="stock_long", routing="rejected"),
        ],
        attribution_dir=tmp_path,
    )
    fb = performance_feedback("team_alpha", attribution_dir=tmp_path)
    assert fb["best_symbol"] == "NVDA"  # +20%
    assert fb["worst_symbol"] == "TSLA"  # short into a +20% move => -20%
    assert any(w["symbol"] == "NVDA" for w in fb["recent_winners"])
    assert any(item["symbol"] == "META" for item in fb["rejected_recent"])
