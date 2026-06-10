from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.db.database import get_connection, get_latest_run_id, insert_daily_report, run_exists
from src.portfolio.performance import benchmark_equity_from_price, excess_return, max_drawdown, pct_return


@dataclass(frozen=True)
class ReportResult:
    ok: bool
    report: dict | None = None
    message: str = ""


def generate_daily_report(
    database_path: Path | str,
    strategy_id: str | None = None,
    run_id: str | None = None,
) -> ReportResult:
    selected_run_id = run_id or get_latest_run_id(database_path)
    if selected_run_id is None:
        return ReportResult(ok=False, message="No runs found.")

    if not run_exists(database_path, selected_run_id):
        return ReportResult(ok=False, message=f"Run not found: {selected_run_id}")

    with get_connection(database_path) as conn:
        run_row = conn.execute(
            '''
            SELECT id, strategy_id, starting_equity
            FROM runs
            WHERE id = ?
            ''',
            (selected_run_id,),
        ).fetchone()
        portfolio_rows = _fetch_portfolio_snapshots(conn, selected_run_id, strategy_id)
        if not portfolio_rows:
            return ReportResult(ok=False, message=f"No portfolio snapshots found for run: {selected_run_id}")

        benchmark_rows = conn.execute(
            '''
            SELECT benchmark_symbol, starting_equity, starting_benchmark_price,
                   current_benchmark_price, timestamp
            FROM benchmark_snapshots
            WHERE run_id = ?
            ORDER BY timestamp ASC, id ASC
            ''',
            (selected_run_id,),
        ).fetchall()
        if not benchmark_rows:
            return ReportResult(ok=False, message=f"No benchmark snapshots found for run: {selected_run_id}")

        selected_strategy_id = strategy_id or run_row["strategy_id"] or portfolio_rows[-1]["strategy_id"] or "unknown"
        equity_curve = [row["equity"] for row in portfolio_rows]
        starting_equity = equity_curve[0]
        current_equity = equity_curve[-1]
        strategy_return = pct_return(starting_equity, current_equity)

        first_benchmark = benchmark_rows[0]
        latest_benchmark = benchmark_rows[-1]
        benchmark_equity = benchmark_equity_from_price(
            starting_equity=starting_equity,
            starting_benchmark_price=first_benchmark["starting_benchmark_price"],
            current_benchmark_price=latest_benchmark["current_benchmark_price"],
        )
        spy_return = pct_return(starting_equity, benchmark_equity)
        report = {
            "report_date": date.today().isoformat(),
            "run_id": selected_run_id,
            "strategy_id": selected_strategy_id,
            "benchmark_symbol": latest_benchmark["benchmark_symbol"],
            "starting_equity": starting_equity,
            "current_equity": current_equity,
            "benchmark_equity": benchmark_equity,
            "strategy_return": strategy_return,
            "spy_return": spy_return,
            "excess_return": excess_return(strategy_return, spy_return),
            "max_drawdown": max_drawdown(equity_curve),
            "trade_count": _count_orders(conn, selected_run_id),
            "rejected_trade_count": _count_rejected_trades(conn, selected_run_id),
        }

    insert_daily_report(
        database_path=database_path,
        strategy_id=report["strategy_id"],
        report_date=report["report_date"],
        report=report,
        run_id=report["run_id"],
    )
    return ReportResult(ok=True, report=report)


def format_report(report: dict) -> str:
    return "\n".join(
        [
            "Daily Report",
            f"Run ID: {report['run_id']}",
            f"Strategy: {report['strategy_id']}",
            f"Benchmark: {report['benchmark_symbol']}",
            f"Starting equity: {_money(report['starting_equity'])}",
            f"Current equity: {_money(report['current_equity'])}",
            f"Benchmark equity: {_money(report['benchmark_equity'])}",
            f"Strategy return: {_percent(report['strategy_return'])}",
            f"SPY return: {_percent(report['spy_return'])}",
            f"Excess return: {_percent(report['excess_return'])}",
            f"Max drawdown: {_percent(report['max_drawdown'])}",
            f"Trade count: {report['trade_count']}",
            f"Rejected trade count: {report['rejected_trade_count']}",
        ]
    )


def _fetch_portfolio_snapshots(conn, run_id: str, strategy_id: str | None):
    if strategy_id is None:
        return conn.execute(
            '''
            SELECT strategy_id, equity, cash, timestamp
            FROM portfolio_snapshots
            WHERE run_id = ?
            ORDER BY timestamp ASC, id ASC
            ''',
            (run_id,),
        ).fetchall()

    return conn.execute(
        '''
        SELECT strategy_id, equity, cash, timestamp
        FROM portfolio_snapshots
        WHERE run_id = ? AND strategy_id = ?
        ORDER BY timestamp ASC, id ASC
        ''',
        (run_id, strategy_id),
    ).fetchall()


def _count_orders(conn, run_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM orders WHERE run_id = ?", (run_id,)).fetchone()[0]


def _count_rejected_trades(conn, run_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM risk_decisions WHERE run_id = ? AND approved = 0",
        (run_id,),
    ).fetchone()[0]


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"
