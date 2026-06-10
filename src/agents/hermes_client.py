from __future__ import annotations


class HermesClient:
    '''
    Placeholder Hermes client.

    Future runtime goal:
    - connect to local Hermes through Ollama, LM Studio, or another endpoint
    - request strict JSON
    - never place trades directly

    Phase 5 only adds local strict JSON parsing. Tests must not require Hermes,
    Ollama, LM Studio, hosted LLMs, network access, or API keys.
    '''

    def generate_trade_proposals(self, prompt: str) -> dict:
        raise NotImplementedError("Hermes runtime integration is not enabled.")
