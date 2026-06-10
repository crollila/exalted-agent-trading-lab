from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.brokers.order_models import (
    BenchmarkSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    RiskDecision,
    TradeProposal,
)


def get_connection(database_path: Path | str) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database(database_path: Path | str) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    schema_sql = schema_path.read_text(encoding="utf-8")
    with get_connection(database_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()


def insert_trade_proposal(database_path: Path | str, proposal: TradeProposal) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT OR REPLACE INTO trade_proposals (
                proposal_id, strategy_id, symbol, action, asset_class,
                target_weight, quantity, estimated_price, thesis, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                proposal.proposal_id,
                proposal.strategy_id,
                proposal.symbol,
                proposal.action.value,
                proposal.asset_class.value,
                proposal.target_weight,
                proposal.quantity,
                proposal.estimated_price,
                proposal.thesis,
                proposal.confidence,
                proposal.created_at.isoformat(),
            ),
        )
        conn.commit()


def insert_risk_decision(database_path: Path | str, decision: RiskDecision) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO risk_decisions (
                proposal_id, approved, reasons_json, created_at
            ) VALUES (?, ?, ?, ?)
            ''',
            (
                decision.proposal_id,
                int(decision.approved),
                json.dumps(decision.reasons),
                decision.created_at.isoformat(),
            ),
        )
        conn.commit()


def insert_order(database_path: Path | str, order: OrderRequest, submitted: bool) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO orders (
                proposal_id, symbol, action, quantity, order_type,
                limit_price, dry_run, submitted, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                order.proposal_id,
                order.symbol,
                order.action.value,
                order.quantity,
                order.order_type,
                order.limit_price,
                int(order.dry_run),
                int(submitted),
                order.created_at.isoformat(),
            ),
        )
        conn.commit()


def insert_portfolio_snapshot(database_path: Path | str, snapshot: PortfolioSnapshot) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO portfolio_snapshots (
                strategy_id, equity, cash, timestamp
            ) VALUES (?, ?, ?, ?)
            ''',
            (
                snapshot.strategy_id,
                snapshot.equity,
                snapshot.cash,
                snapshot.timestamp.isoformat(),
            ),
        )
        conn.commit()


def insert_benchmark_snapshot(database_path: Path | str, snapshot: BenchmarkSnapshot) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO benchmark_snapshots (
                benchmark_symbol, starting_equity, current_strategy_equity,
                starting_benchmark_price, current_benchmark_price, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                snapshot.benchmark_symbol,
                snapshot.starting_equity,
                snapshot.current_strategy_equity,
                snapshot.starting_benchmark_price,
                snapshot.current_benchmark_price,
                snapshot.timestamp.isoformat(),
            ),
        )
        conn.commit()


def insert_daily_report(
    database_path: Path | str,
    strategy_id: str,
    report_date: str,
    report: dict,
) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO daily_reports (
                strategy_id, report_date, report_json
            ) VALUES (?, ?, ?)
            ''',
            (
                strategy_id,
                report_date,
                json.dumps(report, sort_keys=True),
            ),
        )
        conn.commit()
