# Phase 6T - Shorting Design Models

Continue ExaltedFable Agent Trading Lab.

## Goal

Design shorting proposal and risk models for a future paper-short research path without enabling short execution.

This is a future design/modeling phase only. It must not enable shorting, margin, options, live trading, or broker short calls.

## Required behavior

- Review `PROJECT_RULES.md`, `STATUS.md`, `BUILD_PLAN.md`, `README.md`, `docs/risk_policy.md`, and `docs/advanced_permissions_plan.md`.
- Design model changes needed to represent short proposals separately from long stock buys.
- Prefer design docs, schema notes, and tests for rejected/disabled behavior before any behavior change.
- If code models are introduced, they must be inert and rejected by current execution paths unless an explicit future permission level is added.
- Document required fields such as short thesis, borrow availability assumption, max loss estimate, and cover condition.
- Document risk controls for max short exposure, gross exposure, net exposure, max loss per short position, and forced-cover behavior.
- Ensure current default behavior remains stock-only, long-only, no-margin, no-options, no-shorting, and dry-run first.

## Do not

- Enable shorting.
- Add broker short calls.
- Add live trading.
- Add margin.
- Add options.
- Modify execution behavior to place or simulate short orders unless a later prompt explicitly approves simulator implementation.
- Modify broker/order submission behavior.
- Wire Hermes runtime.
- Let LLM/Hermes place trades.
- Add real API keys.
- Commit unless explicitly asked.

## Tests

If code or schema changes are made, add tests proving:

- Shorting remains rejected by default.
- Missing short permission fails closed.
- Existing long-only strategy behavior is unchanged.
- No broker calls are made.
- No external credentials, market data, Hermes runtime, or network service is required.

## Documentation

- Update `STATUS.md` only after verification.
- Update `BUILD_PLAN.md` only after verification.
- Update `README.md` only if user-facing docs change.
- Update `docs/risk_policy.md` only if an actual permission or risk rule changes.

## Verification

Run:

```bash
pytest
python -m compileall src tests
git status --short
git diff --stat
```
