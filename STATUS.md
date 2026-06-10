# STATUS

## Current state

Phase 6C comparison artifacts and experiment logs completed.

Included:

- Python source tree.
- SQLite schema.
- Pydantic models with basic validation.
- Stateful deterministic risk validator with approved quantity and estimated trade value outputs.
- Strategy interface.
- Cash-only baseline strategy.
- SPY buy-and-hold baseline.
- Simple deterministic momentum baseline strategy.
- Strict Hermes JSON proposal parser that converts valid local payloads into `TradeProposal` objects only.
- Safe Hermes parser rejection for invalid JSON, missing fields, empty symbols, non-buy actions, non-stock assets, options, invalid target weights, empty theses, out-of-range confidence, extra fields, and missing local estimated prices.
- Dry-run order executor that only uses risk-approved quantities.
- `dry-run --strategy` CLI selection for known local deterministic strategies.
- `compare-strategies` CLI command that runs `cash_only`, `spy_buy_hold`, and `momentum_v1` in separate dry-run records.
- Deterministic local `multi_day` comparison fixture for SPY, SPY buy-and-hold, and momentum strategy symbols.
- `compare-strategies --fixture multi_day` explicit fixture selection, with `multi_day` as the default and `flat` available for the old placeholder behavior.
- `compare-strategies --save` local artifact output for JSON, CSV, and Markdown experiment summaries.
- `compare-strategies --output-dir` support, defaulting to ignored runtime output under `data/experiments`.
- Saved comparison artifacts include experiment timestamp, fixture name, strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
- Multi-day simulated portfolio and benchmark snapshots that produce non-zero strategy return, SPY return, excess return, and max drawdown where appropriate.
- Cash-only comparison baseline remains zero-return with no cash yield modeled.
- Beginner-readable comparison output with strategy ID, run ID, starting equity, current equity, strategy return, SPY return, excess return, max drawdown, trade count, and rejected trade count.
- Alpaca paper client wrapper for account status, positions, market clock, and approved paper-order submission.
- `paper-status` CLI command with safe failure when credentials or paper settings are missing.
- SQLite-backed benchmark and daily report generator.
- Formal run records for dry-run sessions.
- Run-linked portfolio snapshots, benchmark snapshots, trade proposals, risk decisions, orders, and daily reports.
- `report` CLI command for beginner-readable SPY comparison metrics, defaulting to the latest run.
- Explicit run-id reports via `python -m src.main report --run-id <id>`.
- Expanded tests for risk rules, validation, sizing, execution logging, approved quantities, mocked Alpaca paper integration, benchmark reporting, run-isolated reports, deterministic momentum behavior, cash-only behavior, local strategy comparison, deterministic multi-day simulation fixtures, comparison artifacts, and performance.
- Beginner docs.
- Codex prompt workflow.

## Trading safety state

Current allowed mode:

- Dry-run only by default.
- Alpaca paper integration is wrapped and requires `ALPACA_PAPER=true`.
- Alpaca base URL must be exactly `https://paper-api.alpaca.markets`.
- No live trading.
- No options.
- No shorting.
- No margin.
- No LLM direct execution.
- Hermes is parser-only and is not wired into dry-run, paper trading, Alpaca, or any order path.
- Hermes parser tests require no network, credentials, Ollama, LM Studio, hosted LLM, or real market data.
- Local strategy comparison and saved artifacts are dry-run only and do not call Alpaca, Hermes, external LLMs, market data APIs, or network services.

## Next step

Review Phase 6C artifacts, then continue with explicit Hermes runtime prompting or broader non-live tournament variants when ready.

## Project manager rule

ChatGPT acts as:

- project manager
- prompt writer
- architecture reviewer
- risk reviewer

Codex acts as:

- coding worker
- file editor
- test runner
