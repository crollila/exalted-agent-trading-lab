"""Kill switch semantics (isolated to tmp paths) and the trade ledger."""

from __future__ import annotations

import pytest

from src.kill_switch import (
    KillSwitchEngaged,
    assert_clear,
    disengage,
    engage,
    is_engaged,
    read_kill_switch,
)
from src.ledger import read_trades, record_trade


def test_kill_switch_default_disengaged(tmp_path):
    path = tmp_path / "ks.json"
    state = read_kill_switch(path)
    assert not state.engaged
    assert_clear(path)  # no raise


def test_kill_switch_engage_blocks_and_disengage_restores(tmp_path):
    path = tmp_path / "ks.json"
    engage("testing", path)
    assert is_engaged(path)
    with pytest.raises(KillSwitchEngaged, match="testing"):
        assert_clear(path)
    disengage(path)
    assert_clear(path)


def test_kill_switch_unreadable_file_fails_closed(tmp_path):
    path = tmp_path / "ks.json"
    path.write_text("{broken", encoding="utf-8")
    state = read_kill_switch(path)
    assert state.engaged
    with pytest.raises(KillSwitchEngaged):
        assert_clear(path)


def test_ledger_roundtrip_and_date_filter(tmp_path):
    record_trade(
        tmp_path, "team_alpha",
        symbol="NVDA", action="buy", order_side="buy", qty=10,
        est_price=200.0, est_notional=2000.0,
        thesis="semis lead", exit_plan="stop -5%", confidence=0.7,
        submitted=True, order_id="abc", status="accepted", error=None,
    )
    rows = read_trades(tmp_path, "team_alpha")
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "NVDA" and row["submitted"] is True
    assert row["thesis"] == "semis lead"

    assert read_trades(tmp_path, "team_alpha", date="1999-01-01") == []
    assert read_trades(tmp_path, "team_alpha", date=row["date"]) == rows
    assert read_trades(tmp_path, "team_beta") == []


def test_ledger_tolerates_corrupt_lines(tmp_path):
    record_trade(
        tmp_path, "team_alpha",
        symbol="SPY", action="buy", order_side="buy", qty=1,
        est_price=500.0, est_notional=500.0,
        thesis="t", exit_plan="e", confidence=0.5,
        submitted=False, order_id=None, status=None, error="broker down",
    )
    path = tmp_path / "ledger" / "team_alpha_trades.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt line\n")
    assert len(read_trades(tmp_path, "team_alpha")) == 1
