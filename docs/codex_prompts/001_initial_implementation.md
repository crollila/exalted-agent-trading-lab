# Codex Prompt 001 — Initial Implementation Review and Completion

We are starting a new project called ExaltedFable Agent Trading Lab.

Goal:
Build a Python-based Alpaca paper-trading research system that tests stock-only trading strategies against SPY. This is not live trading. The first milestone is a simple paper/dry-run trading bot that runs one strategy, logs every decision, and produces a daily report comparing performance to SPY.

Important safety architecture:
- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys in source files.
- The LLM must never directly place trades.
- Strategy modules can only create trade proposals.
- A deterministic risk engine must approve/reject every proposal.
- Execution code can only submit approved paper orders.
- Include dry-run mode so orders can be logged without being sent.

Current state:
A starter source tree has already been created.

Your task:
1. Inspect the full project.
2. Fix any bugs.
3. Improve code quality where needed.
4. Ensure tests pass.
5. Do not add live trading.
6. Do not add options.
7. Do not add real API keys.
8. Do not commit unless explicitly asked.

Implementation requirements to verify:
1. Pydantic models exist for:
   - TradeProposal
   - RiskDecision
   - OrderRequest
   - PortfolioSnapshot
   - BenchmarkSnapshot

2. Risk rules exist and tests cover:
   - reject non-stock trades
   - reject options
   - reject shorts/sell orders that exceed current position
   - reject any single position above 20% portfolio weight
   - reject if cash would fall below 10%
   - reject if more than 5 new positions would be opened in a day
   - reject if daily turnover would exceed 30%

3. SQLite schema exists for:
   - strategies
   - runs
   - portfolio_snapshots
   - positions
   - trade_proposals
   - risk_decisions
   - orders
   - benchmark_snapshots
   - daily_reports

4. database.py includes:
   - initialize_database()
   - get_connection()
   - insert_trade_proposal()
   - insert_risk_decision()
   - insert_order()
   - insert_portfolio_snapshot()
   - insert_benchmark_snapshot()

5. There is a Strategy base class.

6. spy_buy_hold.py is a baseline strategy that proposes buying SPY only when not already invested.

7. momentum_v1.py is a deterministic placeholder and does not call external APIs.

8. order_executor.py supports:
   - dry_run=True: logs the order but does not submit
   - dry_run=False: leaves safe TODO/stub for Alpaca paper only

9. benchmark_report.py calculates:
   - strategy return
   - SPY return
   - excess return vs SPY
   - current equity
   - benchmark equity

10. Tests exist for:
   - risk rules
   - position sizing
   - trade validation
   - performance calculations

11. Docs exist:
   - alpaca_setup.md
   - hermes_setup.md
   - risk_policy.md
   - experiment_log.md

12. STATUS.md and BUILD_PLAN.md are accurate.

Run:
```bash
pytest
python -m src.main init-db
python -m src.main dry-run
```

Output:
1. Brief implementation summary
2. Files changed
3. Test results
4. Command results
5. Any assumptions or TODOs
