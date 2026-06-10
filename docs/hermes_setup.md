# Hermes Setup

Hermes comes later.

Do not start with Hermes.

First we need:

1. Database working.
2. Risk engine working.
3. Dry-run execution working.
4. Alpaca paper account working.
5. SPY benchmark report working.

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
