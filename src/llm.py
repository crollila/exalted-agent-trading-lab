"""LLM provider boundary: one class, strict JSON in/out, fail LOUD.

Supports OpenAI, Anthropic, and Ollama (OpenAI-compatible local endpoint).
Agents receive prompts and return parsed JSON dicts — they never see API keys
and never get broker access. A provider failure raises :class:`LLMError`
immediately with a clear message; the old system's silent "provider_failure"
scorecard rows are exactly what this design forbids.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from src.config import Settings


class LLMError(RuntimeError):
    """Provider unavailable, call failed, or output was not valid JSON."""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_object(raw: str | None) -> dict[str, Any]:
    """Parse a model reply into a JSON object; tolerate markdown fences."""

    if not raw or not raw.strip():
        raise LLMError("Model returned an empty reply.")
    text = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model reply was not valid JSON: {exc}. Reply began: {text[:200]!r}") from exc
    if not isinstance(data, dict):
        raise LLMError(f"Model reply must be a JSON object, got {type(data).__name__}.")
    return data


class LLM:
    """Provider-agnostic JSON completion with per-role model selection."""

    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.provider = settings.llm_provider
        self._client = client  # test seam; real client built lazily

        if self.provider == "openai" and not settings.openai_api_key:
            raise LLMError("LLM_PROVIDER=openai but OPENAI_API_KEY is missing/blank in .env.")
        if self.provider == "anthropic" and not settings.anthropic_api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is missing/blank in .env.")
        if self.provider not in ("openai", "anthropic", "ollama"):
            raise LLMError(f"Unsupported LLM_PROVIDER {self.provider!r}. Use openai, anthropic, or ollama.")

    # --- clients ------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self.provider in ("openai", "ollama"):
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMError(
                    "The 'openai' package is not installed in this environment. "
                    "Run: pip install -r requirements.txt"
                ) from exc
            if self.provider == "openai":
                self._client = OpenAI(api_key=self.settings.openai_api_key)
            else:
                self._client = OpenAI(base_url=self.settings.ollama_base_url, api_key="ollama-local")
        else:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise LLMError(
                    "The 'anthropic' package is not installed. Run: pip install anthropic"
                ) from exc
            self._client = Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    # --- completion ----------------------------------------------------------

    def complete_json(self, role: str, system: str, user: str) -> dict[str, Any]:
        """One JSON completion for an agent role. Retries once on transient failure."""

        model = self.settings.model_for(role)
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                raw = self._complete_raw(model, system, user)
                return parse_json_object(raw)
            except LLMError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - network/provider errors retried once
                last_error = exc
            if attempt == 1:
                time.sleep(2)
        raise LLMError(f"LLM call failed for role={role} model={model}: {last_error}")

    def _complete_raw(self, model: str, system: str, user: str) -> str:
        client = self._get_client()
        if self.provider in ("openai", "ollama"):
            response = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system + "\nRespond with a single valid JSON object and nothing else.",
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text
