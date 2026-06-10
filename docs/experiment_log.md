# Experiment Log

## Experiment name

Example: spy_buy_hold_baseline_v1

## Start date

YYYY-MM-DD

## Strategy

Describe the strategy.

## Universe

Example:

- SPY
- QQQ
- AAPL
- MSFT
- NVDA

## Starting capital

$10,000 paper

## Benchmark

SPY

## Comparison artifacts

Save local comparison artifacts with:

```bash
python -m src.main compare-strategies --fixture multi_day --save
```

Default artifact directory: `data/experiments`

| Timestamp | Fixture | JSON | CSV | Markdown | Notes |
|---|---|---|---|---|---|
| YYYY-MM-DDTHH:MM:SSZ | multi_day | path/to/results.json | path/to/results.csv | path/to/results.md | Initial comparison |

## Hypothesis

Example:

The strategy should roughly match SPY with lower turnover.

## Risk settings

- Max position:
- Min cash:
- Max turnover:
- Max new positions:

## Daily notes

| Date | Strategy Equity | SPY Benchmark Equity | Excess Return | Notes |
|---|---:|---:|---:|---|
| YYYY-MM-DD | 10000 | 10000 | 0.00% | Started |
