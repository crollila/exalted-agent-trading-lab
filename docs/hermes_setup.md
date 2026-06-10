# Hermes Setup

Hermes Phase 5 is parser-only.

The repo can validate strict Hermes-shaped JSON and convert valid local payloads into `TradeProposal` objects. It does not call Hermes, Ollama, LM Studio, hosted LLM APIs, Alpaca, or any broker.

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

Hermes is not wired into dry-run execution yet.

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
