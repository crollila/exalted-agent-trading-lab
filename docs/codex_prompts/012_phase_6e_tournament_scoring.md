# Phase 6E - Tournament Scoring and Ranking

Continue the ExaltedFable Agent Trading Lab repo.

Task: Phase 6E - Tournament scoring and ranking.

Goal:

Improve `compare-strategies` so it does not only print raw metrics, but also ranks strategies using deterministic tournament scoring.

Current source state:

- Phase 6D is complete.
- Local strategy comparison exists.
- Deterministic multi-day fixtures exist.
- Saved comparison artifacts exist.
- Parser-only Hermes fixture strategies exist.
- Hermes runtime is still disabled.
- No live trading.
- No options.
- No margin.
- No shorting.
- No LLM direct execution.
- Alpaca paper integration exists, but local comparisons must not call Alpaca.

Safety:

- Do not start Hermes runtime.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Do not require internet access.
- Do not require Alpaca credentials.
- Do not commit unless explicitly asked.
- Strategies and LLM agents can only create trade proposals.
- The deterministic risk engine validates proposals.
- Execution submits only approved paper orders.
- Every proposal, approval/rejection, order, fill, and portfolio snapshot must be logged.
- LLM must never directly place trades.

Implementation:

1. Inspect current comparison, reporting, artifact, fixture, database, model, and CLI code before editing.
2. Add deterministic scoring for each compared strategy using existing report metrics.
3. Use a simple, local, beginner-readable default formula:

   ```text
   score = excess_return - max_drawdown_penalty - rejected_trade_penalty
   ```

   Where:

   - `excess_return` comes from existing report metrics.
   - `max_drawdown_penalty` penalizes larger drawdowns.
   - `rejected_trade_penalty` penalizes rejected trades.
   - Trade count may be shown but should not automatically reward overtrading.

4. Make the formula explicit in code and output.
5. Add rank numbers to `compare-strategies` output.
6. Include at minimum these fields in comparison output:
   - rank
   - strategy ID
   - run ID
   - score
   - starting equity
   - current equity
   - strategy return
   - SPY return
   - excess return
   - max drawdown
   - trade count
   - rejected trade count
7. Update saved artifacts from `compare-strategies --save` so JSON, CSV, and Markdown include:
   - rank
   - score
   - score formula or score explanation
   - all prior required metrics
8. Ranking should sort best score first. If scores tie, use deterministic tie-breakers such as:
   - higher excess return
   - lower max drawdown
   - fewer rejected trades
   - strategy ID alphabetical
9. Add tests covering:
   - score calculation
   - drawdown penalty behavior
   - rejected trade penalty behavior
   - ranking order
   - deterministic tie-breakers
   - CLI comparison output includes rank and score
   - saved JSON includes rank and score
   - saved CSV includes rank and score
   - saved Markdown includes rank, score, and formula/explanation
   - no internet, Alpaca credentials, Hermes runtime, or real market data required
10. Update `STATUS.md` after implementation and tests. It should say Phase 6E tournament scoring/ranking is implemented only if tests pass.
11. Update `BUILD_PLAN.md` to add Phase 6E under Phase 6 and mark it complete only if tests pass.
12. Add this prompt as the next numbered file under `docs/codex_prompts/`.
13. Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies
python -m src.main compare-strategies --fixture multi_day
python -m src.main compare-strategies --fixture multi_day --include-hermes-fixtures
python -m src.main compare-strategies --fixture multi_day --save
```
