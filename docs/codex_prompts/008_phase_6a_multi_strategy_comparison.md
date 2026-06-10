# Phase 6A - Local Multi-Strategy Comparison

Continue the ExaltedFable Agent Trading Lab repo.

## Goal

Add a safe local framework to run and compare multiple non-live strategies side by side using run-aware reports.

Strategies for this phase:

- cash-only baseline
- SPY buy-and-hold
- momentum_v1

Hermes remains parser-only. Do not wire Hermes runtime yet.

## Safety

- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys.
- No external LLM/API calls.
- Do not call Alpaca in tests.
- Do not require internet access.
- Do not commit unless explicitly asked.
- Strategies only create proposals.
- Risk engine remains the only approval/rejection layer.
- Execution only uses approved risk decisions.

## Implementation

1. Inspect the existing strategy base class, dry-run CLI, run records, report generator, and benchmark code.
2. Add a cash-only baseline strategy that creates zero trade proposals.
3. Add a local multi-strategy runner/helper that can run selected strategies sequentially in dry-run mode and create separate run ID records for each strategy.
4. Add CLI support with a command such as `python -m src.main compare-strategies`.
5. The command should run:
   - `cash_only`
   - `spy_buy_hold`
   - `momentum_v1`
6. Each strategy must get its own run ID.
7. The command should print a beginner-readable comparison table or summary including:
   - strategy ID
   - run ID
   - starting equity
   - current equity
   - strategy return
   - SPY return
   - excess return
   - max drawdown
   - trade count
   - rejected trade count
8. Use existing run-aware report generation where possible.
9. Do not duplicate reporting logic unnecessarily.
10. Unknown strategy names should fail cleanly if strategy selection is added.

## Tests

Add tests for:

- cash-only produces zero proposals
- comparison creates separate runs
- comparison includes all three local strategies
- reports are isolated by run
- comparison output includes required metrics
- no Alpaca credentials or network required

## Docs

- Update `STATUS.md`.
- Update `BUILD_PLAN.md` if Phase 6A is complete or scope changes.
- Update `README.md` if a CLI command is added.
- Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

## Run

```bash
pytest
python -m compileall src tests
python -m src.main dry-run
python -m src.main dry-run --strategy momentum_v1
python -m src.main report
python -m src.main compare-strategies
```

## Output

1. Summary
2. Changed files
3. Test output
4. Command output
5. Docs updated
6. Confirm `docs/risk_policy.md` unchanged
7. TODOs
8. No commit unless asked
