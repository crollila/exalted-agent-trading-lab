# Codex Prompt 002 — Phase 1 Risk Engine Tightening

Use this only after Prompt 001 passes.

Goal:
Make the risk engine more production-like before Alpaca paper integration.

Tasks:
1. Change validation so the risk engine returns approved order quantity and estimated trade value.
2. Remove placeholder sizing from OrderExecutor.
3. Add a RiskDecision field for:
   - approved_quantity
   - estimated_trade_value
4. Ensure OrderExecutor only executes approved quantity from RiskDecision.
5. Add tests for:
   - exact approved quantity
   - exact estimated trade value
   - sell quantity within current position
   - sell quantity above current position rejected
   - daily turnover increments handled correctly
6. Update docs/risk_policy.md.
7. Update STATUS.md.

Restrictions:
- No live trading.
- No options.
- No margin.
- No shorting.
- No real keys.
- Do not commit unless asked.

Run:
```bash
pytest
python -m src.main dry-run
```

Output:
1. Summary
2. Files changed
3. Test output
4. Notes/TODOs
