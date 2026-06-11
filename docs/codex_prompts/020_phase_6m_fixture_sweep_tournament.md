# Phase 6M - Fixture Sweep Tournament

## Goal

Add a local command that runs strategy comparison across all deterministic fixtures and summarizes cross-fixture robustness.

Add command:

```bash
python -m src.main fixture-sweep
```

Also support:

```bash
python -m src.main fixture-sweep --include-hermes-fixtures
python -m src.main fixture-sweep --save
python -m src.main fixture-sweep --output-dir data/experiments
```

## Required behavior

- Run comparisons across all deterministic fixtures except `flat` unless there is a good reason to include it.
- Include these fixtures:
  - `multi_day`
  - `bull_trend`
  - `bear_trend`
  - `sideways_chop`
  - `volatile_reversal`
  - `spy_outperformance`
  - `momentum_crash`
- For each fixture, compare the same local strategies used by `compare-strategies`.
- Reuse existing comparison/scoring logic where possible.
- Produce beginner-readable CLI output.

At minimum, output:

- fixture name
- winning strategy for each fixture
- winning score for each fixture
- each strategy's wins across fixtures
- each strategy's average score
- each strategy's average excess return
- each strategy's worst max drawdown
- overall robust champion
- score/scoring explanation
- safety disclaimer that this is local deterministic research, not live trading

## Champion rules

Overall robust champion should be chosen by:

1. most fixture wins
2. higher average score
3. higher average excess return
4. lower worst max drawdown severity
5. strategy ID alphabetical

## Save behavior

- If `--save` is passed, write JSON/CSV/Markdown sweep artifacts under output-dir.
- Runtime artifacts must remain ignored and not committed.
- Saved sweep artifact should include:
  - timestamp
  - fixtures included
  - per-fixture winners
  - per-strategy aggregate robustness metrics
  - overall champion
  - score formula/explanation

## Do not

- Start Hermes runtime.
- Add LLM calls.
- Add live trading.
- Add options.
- Add margin.
- Add shorting.
- Add real API keys.
- Require internet or Alpaca credentials.
- Submit paper orders.
- Change scoring formula.
- Change risk/execution/broker/Hermes behavior.
- Change `docs/risk_policy.md`.
- Commit unless explicitly asked.

## Tests

Add tests for:

- fixture-sweep runs all expected fixtures
- excludes flat by default
- per-fixture winners calculated
- aggregate strategy wins calculated
- average score calculated
- average excess return calculated
- worst drawdown calculated by severity
- deterministic tie-breakers
- include-hermes-fixtures behavior
- save JSON/CSV/Markdown artifacts
- CLI output includes robust champion
- no external services or credentials required

## Docs

- Update `README.md` with fixture-sweep command.
- Update `STATUS.md` only if tests pass.
- Update `BUILD_PLAN.md` with Phase 6M only if tests pass.
- Add this prompt as `docs/codex_prompts/020_phase_6m_fixture_sweep_tournament.md`.
- Do not change `docs/risk_policy.md`.

## Verification

Run:

```bash
pytest
python -m compileall src tests
python -m src.main fixture-sweep
python -m src.main fixture-sweep --include-hermes-fixtures
python -m src.main fixture-sweep --save
```
