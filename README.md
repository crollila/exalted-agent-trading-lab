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
python -m src.main dry-run --strategy momentum_v1
```

## Report examples

```bash
python -m src.main report
python -m src.main report --run-id <id>
```

## Status

See `STATUS.md`.

## Build plan

See `BUILD_PLAN.md`.
