# Model Provider Setup

The lab can use OpenAI, Anthropic/Claude, or a local Ollama model for the
research/proposal/review/learning steps. Providers return **structured JSON only**
and never have broker access. Keys are read from your local (git-ignored) `.env`
and are never logged, printed, or committed.

## Choosing a provider

Set in `.env`:

```env
EXALTED_LLM_PROVIDER=openai   # openai | anthropic | ollama
```

### OpenAI

```env
EXALTED_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...          # local .env only; never commit
OPENAI_MODEL=gpt-5.4-mini
```

To drive the weekly competition with the provider, also set
`WEEK_COMPETITION_PROPOSAL_SOURCE=llm` (or pass `--proposal-source llm`). Team prompts are
distinct (Alpha = aggressive momentum; Beta = contrarian/mean-reversion) and contain only
allowlisted, provenance-tagged context — never secrets. The LLM emits proposal JSON only and
never sizes orders or touches the broker.

### Anthropic / Claude

```env
EXALTED_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...   # local .env only; never commit
ANTHROPIC_MODEL=claude-opus-4-8
```

### Ollama / local

```env
EXALTED_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=llama3.2
```

Ollama uses an OpenAI-compatible local endpoint; no hosted secret is sent.

## Safety guarantees

- If the selected hosted provider's key is missing, construction fails with a clear
  message — it does not silently fall back or call the network.
- Providers return proposal JSON, review JSON, or learning notes only. Non-JSON
  output is rejected.
- Providers never receive secrets and never get broker/order tools.
- Tests mock providers completely; no test requires real credentials or network.
- The UI **Model Provider Setup** page shows only whether a key is configured
  (a boolean), never the value, and never prefills secret fields.

## How LLM output is used

LLM output is advisory. It may propose trades, but the deterministic risk engine
computes approved size and the kill-switch-guarded broker wrapper is the only path
to a paper order. LLMs never place trades.
