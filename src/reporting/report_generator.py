from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.db.database import get_connection, insert_daily_report
from src.portfolio.performance import benchmark_equity_from_price, excess_return, max_drawdown, pct_return


@dataclass(frozen=True)
class ReportResult:
    ok: bool
    report: dict | None = None
    message: str = ""


def generate_daily_report(database_path: Path | str, strategy_id: str | None = None) -> ReportResult:
    with get_connection(database_path) as conn:
        portfolio_rows = _fetch_portfolio_snapshots(conn, strategy_id)
        if not portfolio_rows:
            return ReportResult(ok=False, message="No portfolio snapshots found.")

        benchmark_rows = conn.execute(
            '''
            SELECT benchmark_symbol, starting_equity, starting_benchmark_price,
                   current_benchmark_price, timestamp
            FROM benchmark_snapshots
            ORDER BY timestamp ASC, id ASC
            '''
        ).fetchall()
        if not benchmark_rows:
            return ReportResult(ok=False, message="No benchmark snapshots found.")

        selected_strategy_id = strategy_id or portfolio_rows[-1]["strategy_id"] or "unknown"
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
            "strategy_id": selected_strategy_id,
            "benchmark_symbol": latest_benchmark["benchmark_symbol"],
            "starting_equity": starting_equity,
            "current_equity": current_equity,
            "benchmark_equity": benchmark_equity,
            "strategy_return": strategy_return,
            "spy_return": spy_return,
            "excess_return": excess_return(strategy_return, spy_return),
            "max_drawdown": max_drawdown(equity_curve),
            "trade_count": _count_orders(conn),
            "rejected_trade_count": _count_rejected_trades(conn),
        }

    insert_daily_report(
        database_path=database_path,
        strategy_id=report["strategy_id"],
        report_date=report["report_date"],
        report=report,
    )
    return ReportResult(ok=True, report=report)


def format_report(report: dict) -> str:
    return "\n".join(
        [
            "Daily Report",
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


def _fetch_portfolio_snapshots(conn, strategy_id: str | None):
    if strategy_id is None:
        return conn.execute(
            '''
            SELECT strategy_id, equity, cash, timestamp
            FROM portfolio_snapshots
            ORDER BY timestamp ASC, id ASC
            '''
        ).fetchall()

    return conn.execute(
        '''
        SELECT strategy_id, equity, cash, timestamp
        FROM portfolio_snapshots
        WHERE strategy_id = ?
        ORDER BY timestamp ASC, id ASC
        ''',
        (strategy_id,),
    ).fetchall()


def _count_orders(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def _count_rejected_trades(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM risk_decisions WHERE approved = 0").fetchone()[0]


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"
