from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from src.brokers.order_models import BenchmarkSnapshot, PortfolioSnapshot
from src.config.settings import Settings
from src.db.database import (
    complete_run,
    create_run,
    insert_benchmark_snapshot,
    insert_daily_report,
    insert_portfolio_snapshot,
)
from src.execution.order_executor import OrderExecutor
from src.portfolio.portfolio_state import PortfolioState
from src.reporting.benchmark_report import BenchmarkReport
from src.risk.risk_rules import RiskRules
from src.risk.trade_validator import TradeValidator
from src.strategies.base import Strategy


@dataclass(frozen=True)
class LocalRunResult:
    strategy_id: str
    run_id: str
    proposal_count: int


def run_strategy_dry_run(
    strategy: Strategy,
    settings: Settings,
    database_path: Path | str | None = None,
) -> LocalRunResult:
    active_database_path = database_path or settings.database_path
    portfolio = PortfolioState(
        equity=settings.starting_equity,
        cash=settings.starting_equity,
        positions={},
        timestamp=datetime.now(timezone.utc),
    )
    run_id = create_run(
        active_database_path,
        strategy_id=strategy.strategy_id,
        strategy_name=strategy.name,
        starting_equity=settings.starting_equity,
    )
    proposals = strategy.generate_proposals(portfolio)

    validator = TradeValidator(
        rules=RiskRules(
            min_cash_pct=settings.min_cash_pct,
            max_position_pct=settings.max_position_pct,
            max_daily_turnover_pct=settings.max_daily_turnover_pct,
            max_new_positions_per_day=settings.max_new_positions_per_day,
        )
    )
    executor = OrderExecutor(database_path=active_database_path, dry_run=True, run_id=run_id)

    try:
        insert_portfolio_snapshot(
            active_database_path,
            PortfolioSnapshot(
                strategy_id=strategy.strategy_id,
                equity=portfolio.equity,
                cash=portfolio.cash,
                timestamp=portfolio.timestamp,
            ),
            run_id=run_id,
        )

        for proposal in proposals:
            decision = validator.validate(proposal=proposal, portfolio=portfolio)
            executor.handle_decision(proposal=proposal, decision=decision)

        starting_spy_price = 500.0
        current_spy_price = 500.0
        report = BenchmarkReport(
            starting_equity=settings.starting_equity,
            current_strategy_equity=portfolio.equity,
            starting_spy_price=starting_spy_price,
            current_spy_price=current_spy_price,
        ).to_dict()
        report["run_id"] = run_id

        insert_benchmark_snapshot(
            active_database_path,
            BenchmarkSnapshot(
                starting_equity=settings.starting_equity,
                current_strategy_equity=portfolio.equity,
                starting_benchmark_price=starting_spy_price,
                current_benchmark_price=current_spy_price,
                timestamp=portfolio.timestamp,
            ),
            run_id=run_id,
        )
        insert_daily_report(
            active_database_path,
            strategy_id=strategy.strategy_id,
            report_date=date.today().isoformat(),
            report=report,
            run_id=run_id,
        )
        complete_run(active_database_path, run_id)
    except Exception:
        complete_run(active_database_path, run_id, status="failed")
        raise

    return LocalRunResult(
        strategy_id=strategy.strategy_id,
        run_id=run_id,
        proposal_count=len(proposals),
    )
