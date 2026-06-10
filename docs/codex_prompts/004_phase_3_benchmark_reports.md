# Codex Prompt 004 - Phase 3 Benchmark Reports

Goal:
Improve benchmark and daily reporting against SPY before Hermes or advanced strategy work.

Safety rules:

- Do not start Hermes.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Strategies and LLM agents can only create trade proposals.
- The deterministic risk engine validates proposals.
- Execution submits only approved paper orders.
- Every proposal, approval or rejection, order, fill, and portfolio snapshot must be logged.
- The LLM must never directly place trades.

Required metrics:

- starting equity
- current equity
- strategy return
- SPY return
- excess return
- max drawdown
- trade count
- rejected trade count

Implementation requirements:

1. Inspect reporting, benchmark, database, models, and CLI code before editing.
2. Generate a clear daily report from logged SQLite portfolio snapshots, benchmark snapshots, proposals, risk decisions, and orders.
3. Calculate starting equity from the first relevant portfolio snapshot.
4. Calculate current equity from the latest relevant portfolio snapshot.
5. Calculate strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
6. Prefer deterministic local calculations using already logged SQLite data.
7. Avoid external market data in tests.
8. Make only the smallest safe schema addition if needed.
9. Add a CLI command such as `python -m src.main report`.
10. The report command should print required metrics in a beginner-readable format.
11. Add tests for all required metrics, insufficient data behavior, and report helper/CLI behavior without Alpaca credentials.
12. Tests must not require internet access, Alpaca credentials, or real market data.
13. Update `STATUS.md` after tests pass.
14. Update `BUILD_PLAN.md` if Phase 3 is completed or scope changes.
15. Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

Verification commands:

```bash
pytest
python -m compileall src tests
python -m src.main dry-run
python -m src.main report
```

If report requires initialized data:

```bash
python -m src.main init-db
python -m src.main dry-run
python -m src.main report
```
