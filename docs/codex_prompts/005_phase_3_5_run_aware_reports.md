# Codex Prompt 005 - Phase 3.5 Run-Aware Reports

Goal:
Create formal run records and make reporting filter by `run_id`, so each dry-run or future paper-trading session can be reported independently instead of cumulatively across the whole database.

Safety rules:

- Do not start Hermes.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Do not commit unless explicitly asked.

Implementation requirements:

1. Inspect the existing SQLite schema before editing.
2. Inspect how the `runs` table currently exists or is intended to work.
3. Add run-aware behavior with the smallest clean schema/code changes needed.
4. Ensure `python -m src.main dry-run` creates or uses a formal run record.
5. Link logged records to the active run where practical:
   - portfolio snapshots
   - benchmark snapshots
   - trade proposals
   - risk decisions
   - orders
6. Update the report generator so it can generate a report for a specific `run_id`.
7. Update `python -m src.main report` so it defaults to the latest run, not the entire database.
8. Add an explicit report option such as `python -m src.main report --run-id <id>`.
9. The report output should clearly show the reported `run_id`.
10. Preserve the existing required Phase 3 metrics:
    - starting equity
    - current equity
    - strategy return
    - SPY return
    - excess return
    - max drawdown
    - trade count
    - rejected trade count
11. Add tests for run creation, latest-run isolation, explicit run-id reporting, isolated trade and rejection counts, missing run behavior, and report CLI behavior without Alpaca credentials.
12. Tests must not require internet access, Alpaca credentials, or real market data.
13. Update `STATUS.md` after implementation and tests.
14. Update `BUILD_PLAN.md` to record Phase 3.5 status.
15. Do not update `docs/risk_policy.md` unless trading permissions or risk limits change.

Verification commands:

```bash
pytest
python -m compileall src tests
python -m src.main dry-run
python -m src.main report
python -m src.main report --run-id <id>
```
