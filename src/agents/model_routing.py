"""Task-specific LLM model routing (Phase 7O).

Routes each LLM task to a model so expensive models are used only for high-value
decisions (strategy / proposal generation) and cheaper models handle review,
critique, summary, research synthesis, and gate/review support.

Resolution order per task:

    LLM_MODEL_<TASK>  ->  LLM_MODEL  ->  OPENAI_MODEL  ->  built-in default

This module is deterministic and never performs network/LLM calls. It only reads
environment variables (model NAMES, never secrets) and builds a provider whose
model field is set to the routed model. No secrets are read, printed, or logged.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Mapping

from src.agents.llm_provider import LLMProvider, LLMProviderConfig, build_provider

TASKS = (
    "strategy",
    "portfolio_manager",
    "review",
    "critique",
    "summary",
    "research_synthesis",
    "default",
)

# Task -> task-specific env var. ``default`` has no task-specific var (it is the
# LLM_MODEL / OPENAI_MODEL fallback chain itself).
TASK_ENV_VAR = {
    "strategy": "LLM_MODEL_STRATEGY",
    "portfolio_manager": "LLM_MODEL_PORTFOLIO_MANAGER",
    "review": "LLM_MODEL_REVIEW",
    "critique": "LLM_MODEL_CRITIQUE",
    "summary": "LLM_MODEL_SUMMARY",
    "research_synthesis": "LLM_MODEL_RESEARCH_SYNTHESIS",
}

_DEFAULT_MODEL = "gpt-4o-mini"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def resolve_model(task: str, env: Mapping[str, str] | None = None) -> str:
    """Resolve the model name for a task via the documented fallback chain."""

    if env is None:
        env = os.environ
    candidates: list[str | None] = []
    task_var = TASK_ENV_VAR.get(task)
    if task_var is not None:
        candidates.append(_clean(env.get(task_var)))
    candidates.append(_clean(env.get("LLM_MODEL")))
    candidates.append(_clean(env.get("OPENAI_MODEL")))
    for candidate in candidates:
        if candidate:
            return candidate
    return _DEFAULT_MODEL


def resolve_all_models(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the resolved model name for every known task."""

    return {task: resolve_model(task, env) for task in TASKS}


def _config_with_model(config: LLMProviderConfig, model: str) -> LLMProviderConfig:
    """Return a copy of ``config`` with the active provider's model set to ``model``."""

    if config.provider == "anthropic":
        return replace(config, anthropic_model=model)
    if config.provider == "ollama":
        return replace(config, ollama_model=model)
    return replace(config, openai_model=model)


def build_routed_provider(
    task: str,
    *,
    config: LLMProviderConfig | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: Any | None = None,
) -> LLMProvider:
    """Build a provider whose model is the one routed for ``task``.

    Construction still fails safely (LLMProviderError) when the selected hosted
    provider's API key is missing — identical to ``build_provider``.
    """

    config = config or LLMProviderConfig.from_env(env)
    model = resolve_model(task, env)
    return build_provider(_config_with_model(config, model), client_factory=client_factory)


def routing_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Observable routing summary. Reports model NAMES + key-configured bool only.

    Never includes API key contents — only ``True``/``False`` for configuration.
    """

    config = LLMProviderConfig.from_env(env)
    key_configured = {
        "openai": bool(config.openai_api_key),
        "anthropic": bool(config.anthropic_api_key),
        "ollama": True,  # local OpenAI-compatible endpoint; no hosted key required
    }.get(config.provider, False)
    models = resolve_all_models(env)
    return {
        "provider": config.provider,
        "default_model": models["default"],
        "strategy_model": models["strategy"],
        "portfolio_manager_model": models["portfolio_manager"],
        "review_model": models["review"],
        "critique_model": models["critique"],
        "summary_model": models["summary"],
        "research_synthesis_model": models["research_synthesis"],
        "api_key_configured": key_configured,
    }
