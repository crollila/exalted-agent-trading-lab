# Phase 6D - Hermes Fixture Strategies in Local Comparison

Continue the ExaltedFable Agent Trading Lab repo.

Goal: add safe local Hermes fixture strategies to the comparison framework without calling any real LLM, Hermes runtime, Alpaca, or network service.

Hermes must remain parser-only. This phase should test Hermes-shaped local JSON fixtures flowing through the existing parser, risk engine, dry-run execution, run-aware reporting, and strategy comparison.

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
- Do not commit unless explicitly asked.
- Hermes fixture strategies may only create `TradeProposal` objects.
- Risk engine remains the only approval/rejection layer.
- Execution only uses approved risk decisions.

Implementation:

1. Inspect existing Hermes parser, `Strategy` base class, local runner, comparison CLI, fixtures, and tests.
2. Add local Hermes fixture strategy support, for example `hermes_conservative_fixture` and `hermes_aggressive_fixture`.
3. Use hardcoded/local Hermes-shaped JSON fixtures and the existing Hermes parser.
4. Conservative fixture should produce low target weights within policy limits.
5. Aggressive fixture may produce higher target weights but still within current policy limits.
6. Both must be stock-only, long-only, buy-only proposal generators.
7. Add a comparison option such as `python -m src.main compare-strategies --include-hermes-fixtures`.
8. Saved artifacts should include Hermes fixture results when selected.
9. Add tests for valid fixture proposals, parser usage, invalid fixture payload rejection, comparison inclusion, saved artifact inclusion, and no credential/network/LLM requirement.
10. Update README, STATUS, BUILD_PLAN, and Hermes docs as useful.
11. Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies
python -m src.main compare-strategies --fixture multi_day
python -m src.main compare-strategies --fixture multi_day --save
python -m src.main compare-strategies --fixture multi_day --include-hermes-fixtures
python -m src.main compare-strategies --fixture multi_day --include-hermes-fixtures --save
```
