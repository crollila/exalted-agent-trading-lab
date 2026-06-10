# ExaltedFable Agent Trading Lab

A Python-based Alpaca paper-trading research system for testing stock-only strategies against SPY.

## Core principle

This project is not a live-money trading bot.

The architecture is:

```text
Strategy / Hermes agent -> TradeProposal -> Risk Engine -> Paper Execution -> Database Logs -> Benchmark Report
```

The LLM never directly places trades. It can only propose trades in a strict schema. Deterministic Python code approves or rejects every proposal.

## First milestone

Build a stock-only paper/dry-run bot that:

1. Runs one simple strategy.
2. Logs every decision.
3. Enforces hard risk rules.
4. Produces daily reports.
5. Compares strategy performance to SPY.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Environment

Copy `.env.example` to `.env` and fill in paper credentials only.

```bash
copy .env.example .env
```

Never commit `.env`.

## Run tests

```bash
pytest
```

## Initialize database

```bash
python -m src.main init-db
```

## Dry-run example

```bash
python -m src.main dry-run
python -m src.main dry-run --strategy cash_only
python -m src.main dry-run --strategy momentum_v1
```

## Compare local strategies

```bash
python -m src.main compare-strategies
python -m src.main compare-strategies --fixture multi_day
python -m src.main compare-strategies --include-hermes-fixtures
python -m src.main compare-strategies --fixture multi_day --save
python -m src.main compare-strategies --fixture multi_day --include-hermes-fixtures --save
python -m src.main compare-strategies --fixture flat --save --output-dir data/experiments
```

This runs `cash_only`, `spy_buy_hold`, and `momentum_v1` in separate dry-run records and prints a run-aware comparison table. The default `multi_day` fixture uses deterministic local SPY, SPY buy-and-hold, and momentum symbol prices so strategy return, SPY return, excess return, and max drawdown are non-zero where appropriate. Use `--fixture flat` for the old single-snapshot placeholder behavior.

Use `--include-hermes-fixtures` to add parser-only local Hermes JSON fixture strategies:

- `hermes_conservative_fixture`
- `hermes_aggressive_fixture`

These fixtures do not call Hermes, Ollama, LM Studio, hosted LLM APIs, Alpaca, or any network service. They only feed local Hermes-shaped JSON through the strict parser to create `TradeProposal` objects, then the normal risk engine decides what is approved.

Use `--save` to write durable local research artifacts under `data/experiments` by default:

- JSON for machine-readable review.
- CSV for spreadsheet review.
- Markdown for human-readable experiment notes.

Runtime experiment artifacts are ignored by git.

## Report examples

```bash
python -m src.main report
python -m src.main report --run-id <id>
```

## Status

See `STATUS.md`.

## Build plan

See `BUILD_PLAN.md`.
