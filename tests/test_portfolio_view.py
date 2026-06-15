from datetime import datetime, timezone

from src.ui.portfolio_view import (
    allocation_rows,
    build_position_snapshot,
    build_team_portfolio_snapshot,
    compare_team_portfolios,
    position_table_rows,
    portfolio_history_message,
    unavailable_portfolio_snapshot,
)


def test_build_team_portfolio_snapshot_from_fake_account_and_positions():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    snapshot = build_team_portfolio_snapshot(
        "team_alpha",
        account={"equity": "100500.25", "cash": "25000", "buying_power": "50000"},
        positions=[
            {"symbol": "MSFT", "qty": "2", "market_value": "900", "avg_entry_price": "400", "unrealized_pl": "100"},
            {"symbol": "AAPL", "qty": "1", "market_value": "100", "avg_entry_price": "90", "unrealized_pl": "10"},
        ],
        market_open=False,
        now=now,
    )

    assert snapshot.available is True
    assert snapshot.equity == 100500.25
    assert snapshot.cash == 25000
    assert snapshot.buying_power == 50000
    assert snapshot.market_open is False
    assert snapshot.positions_count == 2
    assert snapshot.data_freshness == now


def test_position_rows_and_allocation_rows_are_chart_ready():
    snapshot = build_team_portfolio_snapshot(
        "team_alpha",
        account={"equity": "1000", "cash": "0", "buying_power": "0"},
        positions=[
            {"symbol": "MSFT", "qty": "2", "market_value": "900"},
            {"symbol": "AAPL", "qty": "1", "market_value": "100"},
        ],
        market_open=True,
    )

    rows = position_table_rows(snapshot)
    assert rows[0]["symbol"] == "MSFT"
    assert rows[0]["side"] == "long"

    alloc = allocation_rows(snapshot)
    assert alloc[0]["weight_pct"] == 90
    assert alloc[1]["weight_pct"] == 10


def test_compare_team_portfolios_reports_leader_and_spy_placeholder(tmp_path):
    alpha = build_team_portfolio_snapshot(
        "team_alpha",
        account={"equity": "1000", "cash": "0", "buying_power": "0"},
        market_open=False,
    )
    beta = build_team_portfolio_snapshot(
        "team_beta",
        account={"equity": "750", "cash": "0", "buying_power": "0"},
        market_open=False,
    )

    comparison = compare_team_portfolios(alpha, beta)
    assert comparison.leader == "team_alpha"
    assert comparison.difference == 250
    assert "placeholder" in comparison.spy_benchmark_status

    spy = tmp_path / "spy.md"
    spy.write_text("spy", encoding="utf-8")
    assert str(spy) in compare_team_portfolios(alpha, beta, spy_history_path=spy).spy_benchmark_status


def test_unavailable_snapshot_and_no_history_message(tmp_path):
    snapshot = unavailable_portfolio_snapshot("team_beta", "missing credentials")
    assert snapshot.available is False
    assert snapshot.positions_count == 0
    assert "missing credentials" in snapshot.message
    assert "No history yet" in portfolio_history_message([])

    report = tmp_path / "report.md"
    report.write_text("report", encoding="utf-8")
    assert str(report) in portfolio_history_message([report])


def test_build_position_snapshot_defaults_to_long_only_display():
    position = build_position_snapshot({"symbol": "MSFT", "side": "weird"})
    assert position.side == "long"
