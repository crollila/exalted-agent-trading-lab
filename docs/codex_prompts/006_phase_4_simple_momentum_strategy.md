# Codex Prompt 006 - Phase 4 Simple Momentum Strategy

Goal:
Add a deterministic, non-LLM baseline momentum strategy that can be compared against SPY and future Hermes strategies.

Safety rules:

- Do not start Hermes.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Do not commit unless explicitly asked.
- Strategy code may only generate trade proposals.
- The deterministic risk engine remains the only approval or rejection layer.
- Execution may only use approved risk decisions.

Implementation requirements:

1. Inspect the existing strategy base class and momentum placeholder before editing.
2. Implement `momentum_v1.py` as a deterministic baseline strategy.
3. Generate stock-only long proposals only.
4. Do not call external APIs in tests.
5. Use a small deterministic input shape for close prices, such as injected local price history.
6. Rank symbols by recent percentage return.
7. Propose buying top positive-momentum symbols.
8. Skip symbols with non-positive momentum.
9. Respect a simple max target weight per symbol compatible with the risk policy.
10. Every proposal must include:
    - strategy ID
    - symbol
    - buy action
    - stock asset class
    - target weight or quantity
    - estimated price
    - thesis
    - confidence
11. Add tests for positive momentum, negative or flat momentum, non-stock filtering, target weights, deterministic ordering, and offline behavior.
12. Add safe CLI support only if it fits cleanly:
    - default behavior remains safe
    - only known local strategies are selectable
    - unknown strategy names fail cleanly
    - run-aware reporting still works
13. Update `STATUS.md` after implementation and tests.
14. Update `BUILD_PLAN.md` if Phase 4 is completed or scope changes.
15. Do not update `docs/risk_policy.md` unless trading permissions or risk limits change.

Verification commands:

```bash
pytest
python -m compileall src tests
python -m src.main dry-run
python -m src.main report
python -m src.main dry-run --strategy momentum_v1
python -m src.main report
```
