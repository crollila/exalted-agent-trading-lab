"""LLM provider abstraction (Part 10).

A thin, structured-output-only boundary over OpenAI, Anthropic/Claude, and
Ollama/local models. Hard rules enforced here:

* Providers return strict JSON only (proposal JSON, review JSON, or learning
  notes). Non-JSON output is rejected.
* No real API calls in tests — providers are injected/mocked.
* If the selected hosted provider's key is missing, construction fails safely
  with a clear message.
* Providers never receive secrets and never get broker/order tools.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from dotenv import load_dotenv

SUPPORTED_PROVIDERS = ("openai", "anthropic", "ollama")
ALLOWED_OUTPUT_KINDS = ("proposal", "review", "learning")


class LLMProviderError(RuntimeError):
    """Raised for provider misconfiguration or invalid structured output."""


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str = "openai"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-8"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "llama3.2"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMProviderConfig":
        if env is None:
            load_dotenv()
            env = os.environ
        return cls(
            provider=(env.get("EXALTED_LLM_PROVIDER", "openai").strip().lower() or "openai"),
            openai_api_key=(env.get("OPENAI_API_KEY") or None),
            openai_model=(env.get("OPENAI_MODEL") or "gpt-4o-mini"),
            anthropic_api_key=(env.get("ANTHROPIC_API_KEY") or None),
            anthropic_model=(env.get("ANTHROPIC_MODEL") or "claude-opus-4-8"),
            ollama_base_url=(env.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1"),
            ollama_model=(env.get("OLLAMA_MODEL") or "llama3.2"),
        )


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        """Return a raw JSON string. Must not perform any side effects."""


def parse_structured_output(raw: str, kind: str) -> dict[str, Any]:
    """Validate and parse a provider's JSON output."""

    if kind not in ALLOWED_OUTPUT_KINDS:
        raise LLMProviderError(f"Unsupported output kind: {kind}. Allowed: {ALLOWED_OUTPUT_KINDS}.")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LLMProviderError(f"Provider returned non-JSON {kind} output.") from exc
    if not isinstance(data, dict):
        raise LLMProviderError(f"Provider {kind} output must be a JSON object.")
    return data


@dataclass
class OpenAIProvider:
    config: LLMProviderConfig
    client_factory: Any | None = None
    name: str = "openai"

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        if not self.config.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is missing. Cannot use the OpenAI provider.")
        client = self._client()
        response = client.chat.completions.create(
            model=self.config.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(self.config)
        from openai import OpenAI

        return OpenAI(api_key=self.config.openai_api_key)


@dataclass
class AnthropicProvider:
    config: LLMProviderConfig
    client_factory: Any | None = None
    name: str = "anthropic"

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        if not self.config.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is missing. Cannot use the Anthropic provider.")
        client = self._client()
        message = client.messages.create(
            model=self.config.anthropic_model,
            max_tokens=2048,
            system=system_prompt + "\nRespond with a single valid JSON object and nothing else.",
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(self.config)
        from anthropic import Anthropic

        return Anthropic(api_key=self.config.anthropic_api_key)


@dataclass
class OllamaProvider:
    config: LLMProviderConfig
    client_factory: Any | None = None
    name: str = "ollama"

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        client = self._client()
        response = client.chat.completions.create(
            model=self.config.ollama_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(self.config)
        from openai import OpenAI

        # Ollama exposes an OpenAI-compatible endpoint; no real secret is sent.
        return OpenAI(base_url=self.config.ollama_base_url, api_key="ollama-local")


def build_provider(
    config: LLMProviderConfig | None = None,
    *,
    client_factory: Any | None = None,
) -> LLMProvider:
    config = config or LLMProviderConfig.from_env()
    if config.provider == "openai":
        if not config.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is missing. Set it or pick another EXALTED_LLM_PROVIDER.")
        return OpenAIProvider(config=config, client_factory=client_factory)
    if config.provider == "anthropic":
        if not config.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is missing. Set it or pick another EXALTED_LLM_PROVIDER.")
        return AnthropicProvider(config=config, client_factory=client_factory)
    if config.provider == "ollama":
        return OllamaProvider(config=config, client_factory=client_factory)
    raise LLMProviderError(
        f"Unsupported EXALTED_LLM_PROVIDER '{config.provider}'. Supported: {SUPPORTED_PROVIDERS}."
    )


def generate_structured(
    provider: LLMProvider,
    kind: str,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """Run a provider and return validated structured output."""

    raw = provider.complete_json(system_prompt, user_prompt)
    return parse_structured_output(raw, kind)
