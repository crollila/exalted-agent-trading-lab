# Codex Prompt 010 - Phase 6C Comparison Artifacts

Continue the ExaltedFable Agent Trading Lab repo.

Task: Phase 6C - Comparison artifacts and experiment logs.

Goal:

Make local strategy comparison save durable research artifacts so experiments can be reviewed before adding Hermes runtime.

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

1. Inspect `compare-strategies`, strategy comparison formatting, local runner, DB/run records, README, STATUS, BUILD_PLAN, and `docs/experiment_log.md`.
2. Add durable artifact output for comparison results.
3. Prefer JSON for machine-readable results, CSV for spreadsheet review, and optional Markdown for human-readable experiment notes.
4. Use a safe local output directory such as `data/experiments`.
5. Ensure generated artifacts remain ignored by git.
6. Add CLI support such as:
   - `python -m src.main compare-strategies --save`
   - `python -m src.main compare-strategies --output-dir data/experiments`
   - `python -m src.main compare-strategies --fixture multi_day --save`
7. Saved artifacts should include experiment timestamp, fixture name, strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
8. Preserve existing terminal output when artifacts are not requested.
9. Do not save API keys, environment variables, or secrets.
10. Add tests for JSON artifacts, CSV artifacts, Markdown summary if implemented, missing output directory creation, no Alpaca credential or network requirement, unchanged no-save comparison behavior, and saved artifacts for `multi_day` and `flat`.
11. Update README, STATUS, BUILD_PLAN, and `docs/experiment_log.md` as useful.
12. Do not change `docs/risk_policy.md` unless trading permissions or risk limits change.

Verification:

- `pytest`
- `python -m compileall src tests`
- `python -m src.main compare-strategies`
- `python -m src.main compare-strategies --fixture multi_day`
- `python -m src.main compare-strategies --fixture flat`
- `python -m src.main compare-strategies --fixture multi_day --save`
