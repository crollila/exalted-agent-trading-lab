from datetime import datetime, timezone

from src.ui.data_tools import (
    agent_market_data_rules,
    build_data_source_statuses,
    data_source_rows,
    market_snapshot_context,
)
from src.ui.portfolio_view import build_team_portfolio_snapshot


def test_data_availability_status_with_fake_config():
    statuses = build_data_source_statuses(
        {
            "TEAM_ALPHA_ALPACA_API_KEY": "key",
            "TEAM_ALPHA_ALPACA_SECRET_KEY": "secret",
        }
    )
    rows = data_source_rows(statuses)

    assert rows[0]["source"] == "Alpaca paper account"
    assert rows[0]["configured"] == "yes"
    assert any(row["source"] == "Local runtime files" and row["configured"] == "yes" for row in rows)
    assert any("No uncontrolled web browsing" in row["note"] for row in rows)


def test_market_data_context_includes_provided_snapshot_only():
    snapshot = build_team_portfolio_snapshot(
        "team_alpha",
        account={"equity": "100000", "cash": "5000", "buying_power": "10000"},
        positions=[{"symbol": "MSFT", "qty": "1", "market_value": "300"}],
        market_open=True,
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    context = market_snapshot_context([snapshot])
    assert "team_alpha" in context
    assert "equity=100000.0" in context
    assert "market=open" in context
    assert "positions=1" in context


def test_agent_rules_warn_not_to_invent_missing_market_data():
    rules = agent_market_data_rules("")
    assert "No market/account/news data context was supplied" in rules
    assert "Do not scrape arbitrary websites" in rules
    assert "invent current prices" in rules
