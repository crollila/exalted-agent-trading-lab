# Codex Prompt 009 - Phase 6B Deterministic Multi-Day Simulation

Continue the ExaltedFable Agent Trading Lab repo.

Task: Phase 6B - Deterministic multi-day simulation fixtures.

Goal:

Make local strategy comparison produce meaningful non-zero strategy/SPY returns using deterministic fixture price data, without live trading, Alpaca, network calls, or LLM calls.

Safety:

- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys.
- No external LLM/API calls.
- No Alpaca calls in tests.
- No internet access required.
- Do not wire Hermes runtime.
- Strategies only create proposals.
- Risk engine remains the only approval/rejection layer.
- Execution only uses approved risk decisions.

Implementation:

1. Inspect local runner, report generator, strategy comparison, portfolio snapshots, benchmark snapshots, and existing tests.
2. Add deterministic local multi-day fixture data for SPY and strategy symbols.
3. Add a simulation/helper that can create multiple portfolio snapshots and benchmark snapshots per run.
4. Keep this local and deterministic; no market data API.
5. Update `compare-strategies` or add a safe option such as `python -m src.main compare-strategies --fixture multi_day`.
6. The comparison output should show non-zero values where appropriate: strategy return, SPY return, excess return, and max drawdown.
7. Preserve existing metrics: strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
8. Keep `cash_only` as a zero-return baseline unless cash yield is explicitly modeled. Do not model cash yield in this phase.
9. Ensure SPY benchmark return comes from deterministic SPY fixture movement.
10. Ensure `momentum_v1` and SPY buy-and-hold have deterministic, explainable simulated equity movement.
11. Add tests for fixture movement, strategy movement, excess return, max drawdown, cash-only baseline, comparison output, run isolation, and no credential/network requirement.
12. Update `STATUS.md`.
13. Update `BUILD_PLAN.md`.
14. Update `README.md` if CLI behavior changes.
15. Do not change `docs/risk_policy.md` unless permissions/risk limits change.

Verification:

- `pytest`
- `python -m compileall src tests`
- `python -m src.main dry-run`
- `python -m src.main dry-run --strategy momentum_v1`
- `python -m src.main report`
- `python -m src.main compare-strategies`
- `python -m src.main compare-strategies --fixture multi_day`
