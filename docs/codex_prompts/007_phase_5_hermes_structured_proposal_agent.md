# Phase 5 - Hermes Structured Proposal Agent

Continue the ExaltedFable Agent Trading Lab repo.

## Goal

Add Hermes as a strict structured proposal parser/generator only. Hermes must never place orders, call Alpaca, bypass risk, or execute trades.

## Safety

- No live trading.
- No options.
- No margin.
- No shorting.
- No real API keys.
- No external LLM/API calls in tests.
- Do not require Hermes/Ollama/LM Studio for tests.
- Do not commit unless explicitly asked.
- Hermes may only create `TradeProposal` objects.
- Risk engine remains the only approval/rejection layer.
- Invalid JSON must fail safely with no traceback.

## Source Of Truth

Read and preserve the rules in:

- `PROJECT_RULES.md`
- `BUILD_PLAN.md`
- `STATUS.md`
- `README.md`
- `docs/risk_policy.md`
- `docs/hermes_setup.md`
- `docs/codex_workflow.md`

## Implement

Add a Hermes proposal parser module under `src/agents/`.

Parse strict JSON shaped like:

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

Convert valid proposals into existing `TradeProposal` objects. Because the Hermes JSON does not include prices and `TradeProposal` requires `estimated_price`, keep conversion local and explicit by requiring a caller-supplied estimated-price mapping.

Reject safely:

- invalid JSON
- missing fields
- empty symbol
- non-buy action for now
- non-stock asset class
- options
- `target_weight <= 0` or above risk-policy-compatible bounds
- empty thesis
- confidence outside 0-1

Prefer parser/unit tests only. Do not wire Hermes into dry-run execution yet unless it is parse-only/local fixture and still proposal-only.

## Update

- `STATUS.md`
- `BUILD_PLAN.md` if Phase 5 is complete or scope changes
- `docs/hermes_setup.md` if useful
- Do not change `docs/risk_policy.md` unless permissions/risk limits change

## Run

```bash
pytest
python -m compileall src tests
python -m src.main dry-run
python -m src.main dry-run --strategy momentum_v1
python -m src.main report
```

## Output

1. Summary
2. Changed files
3. Test output
4. Command output
5. Docs updated
6. Confirm `docs/risk_policy.md` unchanged
7. TODOs
8. No commit unless asked
