# Phase 6F - Tournament History / Experiment Ledger

Continue ExaltedFable Agent Trading Lab.

Task: Phase 6F - Tournament history / experiment ledger.

Goal:

Add a local CLI feature to review saved `compare-strategies --save` JSON artifacts over time.

Add command:

```bash
python -m src.main tournament-history
```

Also support:

```bash
python -m src.main tournament-history --output-dir data/experiments
```

Current source state:

- Phase 6E is complete.
- `compare-strategies` ranks strategies with deterministic score:

  ```text
  score = excess_return - abs(max_drawdown) - (rejected_trade_count * 0.01)
  ```

- Saved comparison artifacts include rank, score, score formula, and score explanation.
- Hermes runtime remains disabled.
- Local comparisons do not call Alpaca.

Required behavior:

- Read saved comparison JSON artifacts from the selected directory.
- Print beginner-readable tournament history.
- For each valid artifact, show:
  - experiment or artifact timestamp
  - fixture name
  - number of strategies compared
  - winning strategy ID
  - winning score
  - winning strategy return
  - winning SPY return
  - winning excess return
  - winning max drawdown
  - artifact path
- Sort history deterministically, preferably newest first.
- If no artifacts exist, print a clear no-artifacts message with no stack trace.
- If an artifact is malformed or missing expected fields, skip/report safely without crashing.
- Use temporary test fixture files; do not depend on real `data/experiments`.

Safety:

- Do not start Hermes runtime.
- Do not add LLM calls.
- Do not add live trading.
- Do not add options.
- Do not add margin.
- Do not add shorting.
- Do not add real API keys.
- Do not require internet.
- Do not require Alpaca credentials.
- Do not submit paper orders.
- Do not change scoring formula unless fixing a bug.
- Do not change risk, execution, broker, or Hermes behavior.
- Do not change `docs/risk_policy.md`.
- Do not commit unless explicitly asked.

Tests:

- one artifact
- multiple artifacts
- winner detection from ranked rows
- deterministic newest-first sorting
- no-artifact behavior
- malformed-artifact behavior
- CLI output includes winner and score
- no external services, credentials, or order submission required

Update:

- `README.md` with command
- `STATUS.md` only if tests pass
- `BUILD_PLAN.md` with Phase 6F only if tests pass
- add this prompt as `docs/codex_prompts/013_phase_6f_tournament_history.md`

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies --fixture multi_day --save
python -m src.main tournament-history
```

Also test `tournament-history` against an empty temporary directory if practical.
