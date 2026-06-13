# Hermes Setup

Hermes runtime integration remains disabled.

The repo can validate strict Hermes-shaped JSON and convert valid local payloads into reviewable proposal objects. It does not call Hermes, Ollama, LM Studio, hosted LLM APIs, Alpaca, or any broker.

## Role of Hermes

Hermes will only produce structured trade proposals.

Bad:

```text
Hermes -> Alpaca order
```

Good:

```text
Hermes -> TradeProposal JSON -> Risk Engine -> Paper Order
```

Phase 5 stops at:

```text
Hermes-shaped JSON -> TradeProposal objects
```

Phase 6D adds local Hermes fixture strategies for comparison only:

```text
Local Hermes-shaped fixture JSON -> Hermes parser -> TradeProposal objects -> Risk Engine -> Dry-run logs/reports
```

These fixtures are not Hermes runtime wiring. They use hardcoded JSON in the repo and require no model, endpoint, API key, Alpaca account, or network access.

Run them in local comparison with:

```bash
python -m src.main compare-strategies --include-hermes-fixtures
python -m src.main compare-strategies --fixture multi_day --include-hermes-fixtures --save
```

Phase 7A adds a local Hermes multi-agent strategy sandbox router:

```text
Strict local Hermes JSON -> sandbox router -> route summary only
```

Hermes agents may propose stock longs, shorts, options, margin ideas, or invalid experimental ideas in strict local JSON. The sandbox classifies them without approving execution:

- `stock_long` -> `paper_eligible_stock_long`
- `short_stock` -> `simulation_only_short`
- `option_long` -> `simulation_only_option`
- `margin` -> `simulation_only_margin`
- unknown or malformed proposals -> `rejected`

Review a local sandbox file with:

```bash
python -m src.main review-hermes-sandbox --file docs/examples/hermes_strategy_sandbox_example.json
```

The review command reads a local JSON file only. It does not initialize the database, call Alpaca, call Hermes, call an LLM, fetch network data, submit orders, write orders, or change portfolio state. Its output states that Hermes proposals are not execution approval.

Phase 7B adds local Hermes team registry files before any runtime integration:

```bash
python -m src.main hermes-teams --file docs/examples/hermes_team_registry_example.json
```

The registry records team IDs, agent IDs, roles, active/inactive status, optional strategy family, latest strategy placeholders, and learning notes. It is registry metadata only. It does not call Hermes, call Alpaca, call LLMs, call brokers, submit orders, write orders, or grant execution authority.

Hermes runtime remains disabled. A future Hermes process, if added, must output strict local JSON for human/Codex review and must not receive broker credentials, Alpaca access, API keys, or direct order authority.

## Parser requirements

The parser rejects safely and returns no proposals for:

- invalid JSON
- missing or extra fields
- empty symbol
- non-buy action
- non-stock asset class
- options
- target weight at or below 0
- target weight above the current max position policy
- empty thesis
- confidence outside 0-1
- missing local estimated price

The strict JSON payload does not contain prices. Conversion to the existing `TradeProposal` model therefore requires a local `estimated_prices` mapping supplied by the caller. Tests use local fixtures only.

## Possible local setup options later

- Ollama
- LM Studio
- direct hosted endpoint, if available

## Expected output shape

```json
{
  "strategy_id": "hermes_wealth_advisor_v1",
  "proposals": [
    {
      "symbol": "MSFT",
      "action": "buy",
      "asset_class": "stock",
      "target_weight": 0.08,
      "thesis": "Positive momentum and strong balance sheet.",
      "confidence": 0.72
    }
  ],
  "portfolio_notes": "Maintain cash reserve."
}
```
