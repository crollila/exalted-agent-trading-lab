# Phase 6G - Tournament Champion Report

Continue ExaltedFable Agent Trading Lab.

Task: Phase 6G - Tournament champion report.

Goal:

Add a local CLI feature that reviews saved tournament-history artifacts and summarizes the current champion strategy across saved tournament runs.

Add command:

```bash
python -m src.main tournament-champion
```

Also support:

```bash
python -m src.main tournament-champion --output-dir data/experiments
```

Current source state:

- Phase 6F tournament history ledger is complete.
- Current local research loop is:

  ```text
  compare-strategies -> rank strategies -> save artifacts -> review tournament-history
  ```

- Hermes runtime remains disabled.
- Local Hermes fixture strategies are parser-only and hardcoded.
- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys.
- No LLM direct execution.

Required behavior:

- Read saved ranked comparison JSON artifacts from the selected directory.
- Reuse or build on Phase 6F tournament-history logic where reasonable.
- Determine strategy-level champion summary across all valid artifacts.
- Print beginner-readable champion report.

At minimum, output should include:

- champion strategy ID
- number of valid tournaments reviewed
- number of wins for the champion
- champion win rate
- champion best score
- champion average score across appearances
- champion average excess return across appearances
- champion worst max drawdown across appearances
- most recent win timestamp if available
- fixtures where the champion appeared
- skipped/malformed artifact count, if any

Ranking rules:

Champion should be the strategy with the most rank-1 wins.

Tie-breakers should be deterministic:

1. more wins
2. higher average score
3. higher best score
4. higher average excess return
5. lower worst max drawdown
6. strategy ID alphabetical

Graceful behavior:

- If no artifacts exist, print a clear no-artifacts message with no stack trace.
- If artifacts exist but none are valid, print a clear no-valid-artifacts message with no stack trace.
- If some artifacts are malformed, skip/report them safely without crashing.

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
- Do not change scoring formula.
- Do not change risk, execution, broker, or Hermes behavior.
- Do not change `docs/risk_policy.md`.
- Do not commit unless explicitly asked.

Tests:

- champion from one artifact
- champion from multiple artifacts
- most wins determines champion
- deterministic tie-breakers
- average score calculation
- best score calculation
- average excess return calculation
- worst drawdown calculation
- no-artifact behavior
- all-malformed-artifact behavior
- mixed valid/malformed artifact behavior
- CLI output includes champion, wins, win rate, and average score
- no external services, credentials, or order submission required

Update:

- `README.md` with command
- `STATUS.md` only if tests pass
- `BUILD_PLAN.md` with Phase 6G only if tests pass
- add this prompt as `docs/codex_prompts/014_phase_6g_tournament_champion.md`

Run:

```bash
pytest
python -m compileall src tests
python -m src.main compare-strategies --fixture multi_day --save
python -m src.main tournament-history
python -m src.main tournament-champion
```

Also test `tournament-champion` against an empty temporary directory if practical.
