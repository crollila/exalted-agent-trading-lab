import sqlite3

from src.brokers.order_models import AssetClass, RiskDecision, TradeAction, TradeProposal
from src.db.database import initialize_database
from src.execution.order_executor import OrderExecutor


def test_dry_run_logs_approved_order_without_submission(tmp_path):
    database_path = tmp_path / "trading_lab.sqlite3"
    initialize_database(database_path)

    proposal = TradeProposal(
        strategy_id="test",
        symbol="SPY",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        quantity=1,
        estimated_price=500,
        thesis="test",
        confidence=0.5,
    )
    decision = RiskDecision(
        proposal_id=proposal.proposal_id,
        approved=True,
        reasons=["Approved."],
        approved_quantity=1,
        approved_trade_value=500,
    )

    OrderExecutor(database_path=database_path, dry_run=True).handle_decision(proposal, decision)

    with sqlite3.connect(database_path) as conn:
        row = conn.execute("SELECT dry_run, submitted FROM orders").fetchone()

    assert row == (1, 0)


def test_rejected_decision_does_not_create_order(tmp_path):
    database_path = tmp_path / "trading_lab.sqlite3"
    initialize_database(database_path)

    proposal = TradeProposal(
        strategy_id="test",
        symbol="SPY",
        action=TradeAction.BUY,
        asset_class=AssetClass.STOCK,
        quantity=1,
        estimated_price=500,
        thesis="test",
        confidence=0.5,
    )
    decision = RiskDecision(
        proposal_id=proposal.proposal_id,
        approved=False,
        reasons=["Rejected: test."],
    )

    OrderExecutor(database_path=database_path, dry_run=True).handle_decision(proposal, decision)

    with sqlite3.connect(database_path) as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    assert order_count == 0
