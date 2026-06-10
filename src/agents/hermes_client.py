from __future__ import annotations


class HermesClient:
    '''
    Placeholder Hermes client.

    Phase 5 goal:
    - connect to local Hermes through Ollama, LM Studio, or another endpoint
    - request strict JSON
    - validate response with pydantic schemas
    - never place trades directly
    '''

    def generate_trade_proposals(self, prompt: str) -> dict:
        raise NotImplementedError("Hermes integration comes in Phase 5.")
