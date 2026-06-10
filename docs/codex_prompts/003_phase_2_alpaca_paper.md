# Codex Prompt 003 — Phase 2 Alpaca Paper Integration

Use this only after Phase 1 passes.

Goal:
Add safe Alpaca paper integration.

Tasks:
1. Implement AlpacaClientWrapper using alpaca-py.
2. Enforce ALPACA_PAPER=true.
3. Refuse to run if base URL is not the paper endpoint.
4. Add methods:
   - get_account()
   - get_positions()
   - is_market_open()
   - submit_paper_order(order_request)
5. Add a command:
   - python -m src.main paper-status
6. paper-status should print:
   - account equity
   - cash
   - buying power
   - market open/closed
   - positions count
7. Tests must mock Alpaca. Tests must not require credentials.
8. Update docs/alpaca_setup.md.
9. Update STATUS.md.

Restrictions:
- Paper only.
- No live endpoint.
- No options.
- No margin.
- No shorting.
- Do not submit orders in tests.
- Do not commit unless asked.

Run:
```bash
pytest
python -m src.main paper-status
```

Output:
1. Summary
2. Files changed
3. Test output
4. Manual setup needed
