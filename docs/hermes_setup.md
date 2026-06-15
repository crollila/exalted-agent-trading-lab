# Hermes Setup

Hermes runtime integration is disabled by default and must be explicitly enabled before any local generation call.

The repo can validate strict Hermes-shaped JSON and convert valid local payloads into reviewable proposal objects. By default it does not call Hermes, Ollama, LM Studio, hosted LLM APIs, Alpaca, or any broker.

## Role of Hermes

Hermes will only produce structured trade proposals.

In this app, **Hermes** means the agent runtime interface. A common local setup points that
interface at an **Ollama** model running on your machine. Local Ollama usually has no
per-message API fee, but it still uses your CPU/GPU, memory, and electricity. It is only as
useful as the model, prompts, data, tools, and evaluation you provide.

Hermes/Ollama does **not** have internet access by default. If agents need market data,
account status, news, RSS, or SEC information, the app must fetch that data and pass it into
the prompt as tool context. Agents should say data is missing rather than inventing current
prices, news, market status, or catalysts.

The app's "learning" is not model self-training. It means operator-visible runtime memory:
saved goals, lessons, evidence paths, scorecards, and decisions that can be included in
future prompts. It does not automatically edit code, change trading permissions, enable
live trading, or train model weights.

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

Phase 7C adds local tournament rounds that consume only registry JSON and proposal JSON:

```bash
python -m src.main hermes-tournament-round --registry docs/examples/hermes_team_registry_example.json --proposal docs/examples/hermes_strategy_sandbox_example.json --proposal docs/examples/hermes_strategy_sandbox_team_beta_example.json
```

Tournament rounds load local files, route proposals through the sandbox router, score teams by routing counts, and rank teams deterministically. The score is routing score only, not profitability, trading approval, broker readiness, or risk approval. With `--save`, artifacts are local ignored research outputs under `data/experiments` by default.

Phase 7D adds an opt-in Hermes runtime adapter. Configure only a local or OpenAI-compatible Hermes endpoint:

```bash
set HERMES_ENABLED=true
set HERMES_BASE_URL=http://127.0.0.1:11434/v1
set HERMES_MODEL=<model>
set HERMES_API_KEY=dummy-local-key
```

Then generate a local proposal file:

```bash
python -m src.main hermes-generate-proposals --team-id team_alpha --agent-id alpha_research_01 --agent-role research_agent --strategy-id team_alpha_runtime_v1 --output-file data/agent_runs/team_alpha_runtime_v1.json
```

The generation command writes raw Hermes JSON locally, validates it with the sandbox router, and prints route counts. Generated files under `data/agent_runs` are ignored runtime artifacts.

Review and route generated files with:

```bash
python -m src.main review-hermes-sandbox --file data/agent_runs/team_alpha_runtime_v1.json
python -m src.main hermes-tournament-round --registry docs/examples/hermes_team_registry_example.json --proposal data/agent_runs/team_alpha_runtime_v1.json
```

The runtime prompt requires strict JSON only. It bans secrets, execution claims, broker credentials, order placement language, live trading, Markdown, and prose outside JSON. Hermes output remains proposal JSON for paper/simulation routing only. It must not receive broker credentials, Alpaca access, real API keys, or direct order authority.

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
