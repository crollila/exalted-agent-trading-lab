from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
        _ensure_risk_decision_columns(conn)
        _ensure_run_id_columns(conn)
        conn.commit()


def _ensure_risk_decision_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(risk_decisions)").fetchall()}
    if "approved_quantity" not in columns:
        conn.execute("ALTER TABLE risk_decisions ADD COLUMN approved_quantity REAL")
    if "estimated_trade_value" not in columns:
        conn.execute("ALTER TABLE risk_decisions ADD COLUMN estimated_trade_value REAL NOT NULL DEFAULT 0")


def _ensure_run_id_columns(conn: sqlite3.Connection) -> None:
    tables = (
        "portfolio_snapshots",
        "positions",
        "trade_proposals",
        "risk_decisions",
        "orders",
        "benchmark_snapshots",
        "daily_reports",
    )
    for table in tables:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "run_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")


def create_run(
    database_path: Path | str,
    strategy_id: str,
    starting_equity: float,
    strategy_name: str | None = None,
    run_id: str | None = None,
    started_at: datetime | None = None,
    status: str = "running",
) -> str:
    active_run_id = run_id or str(uuid4())
    started = started_at or datetime.now(timezone.utc)
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT OR IGNORE INTO strategies (id, name, description)
            VALUES (?, ?, ?)
            ''',
            (strategy_id, strategy_name or strategy_id, None),
        )
        conn.execute(
            '''
            INSERT INTO runs (
                id, strategy_id, starting_equity, started_at, status
            ) VALUES (?, ?, ?, ?, ?)
            ''',
            (
                active_run_id,
                strategy_id,
                starting_equity,
                started.isoformat(),
                status,
            ),
        )
        conn.commit()
    return active_run_id


def complete_run(
    database_path: Path | str,
    run_id: str,
    status: str = "completed",
    ended_at: datetime | None = None,
) -> None:
    ended = ended_at or datetime.now(timezone.utc)
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            UPDATE runs
            SET status = ?, ended_at = ?
            WHERE id = ?
            ''',
            (status, ended.isoformat(), run_id),
        )
        conn.commit()


def get_latest_run_id(database_path: Path | str) -> str | None:
    with get_connection(database_path) as conn:
        row = conn.execute(
            '''
            SELECT id
            FROM runs
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            '''
        ).fetchone()
    return None if row is None else row["id"]


def run_exists(database_path: Path | str, run_id: str) -> bool:
    with get_connection(database_path) as conn:
        row = conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
    return row is not None


def insert_trade_proposal(database_path: Path | str, proposal: TradeProposal, run_id: str | None = None) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT OR REPLACE INTO trade_proposals (
                proposal_id, run_id, strategy_id, symbol, action, asset_class,
                target_weight, quantity, estimated_price, thesis, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                proposal.proposal_id,
                run_id,
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


def insert_risk_decision(database_path: Path | str, decision: RiskDecision, run_id: str | None = None) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO risk_decisions (
                run_id, proposal_id, approved, reasons_json, approved_quantity,
                estimated_trade_value, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                run_id,
                decision.proposal_id,
                int(decision.approved),
                json.dumps(decision.reasons),
                decision.approved_quantity,
                decision.estimated_trade_value,
                decision.created_at.isoformat(),
            ),
        )
        conn.commit()


def insert_order(database_path: Path | str, order: OrderRequest, submitted: bool, run_id: str | None = None) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO orders (
                run_id, proposal_id, symbol, action, quantity, order_type,
                limit_price, dry_run, submitted, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                run_id,
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


def insert_portfolio_snapshot(database_path: Path | str, snapshot: PortfolioSnapshot, run_id: str | None = None) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO portfolio_snapshots (
                run_id, strategy_id, equity, cash, timestamp
            ) VALUES (?, ?, ?, ?, ?)
            ''',
            (
                run_id,
                snapshot.strategy_id,
                snapshot.equity,
                snapshot.cash,
                snapshot.timestamp.isoformat(),
            ),
        )
        conn.commit()


def insert_benchmark_snapshot(database_path: Path | str, snapshot: BenchmarkSnapshot, run_id: str | None = None) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO benchmark_snapshots (
                run_id, benchmark_symbol, starting_equity, current_strategy_equity,
                starting_benchmark_price, current_benchmark_price, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                run_id,
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
    run_id: str | None = None,
) -> None:
    with get_connection(database_path) as conn:
        conn.execute(
            '''
            INSERT INTO daily_reports (
                run_id, strategy_id, report_date, report_json
            ) VALUES (?, ?, ?, ?)
            ''',
            (
                run_id,
                strategy_id,
                report_date,
                json.dumps(report, sort_keys=True),
            ),
        )
        conn.commit()
