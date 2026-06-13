from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.agents.hermes_strategy_sandbox import (
    HermesSandboxResult,
    format_hermes_sandbox_result,
    load_hermes_sandbox_file,
)


DEFAULT_HERMES_TIMEOUT_SECONDS = 30.0


class HermesRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = Field(default=DEFAULT_HERMES_TIMEOUT_SECONDS, gt=0.0)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HermesRuntimeConfig":
        env = env or os.environ
        return cls(
            enabled=env.get("HERMES_ENABLED", "").strip().lower() == "true",
            base_url=env.get("HERMES_BASE_URL", ""),
            model=env.get("HERMES_MODEL", ""),
            api_key=env.get("HERMES_API_KEY") or None,
            timeout_seconds=float(env.get("HERMES_TIMEOUT_SECONDS", DEFAULT_HERMES_TIMEOUT_SECONDS)),
        )

    @field_validator("base_url", "model")
    @classmethod
    def text_fields_are_trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def optional_text_is_trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    def validate_ready(self) -> None:
        if not self.enabled:
            raise RuntimeError("Hermes runtime is disabled. Set HERMES_ENABLED=true to opt in.")
        if not self.base_url:
            raise RuntimeError("Hermes runtime is misconfigured: HERMES_BASE_URL is required.")
        if not self.model:
            raise RuntimeError("Hermes runtime is misconfigured: HERMES_MODEL is required.")


class HermesGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    agent_id: str
    agent_role: str
    strategy_id: str
    learning_goal: str | None = None
    strategy_notes: str | None = None

    @field_validator("team_id", "agent_id", "agent_role", "strategy_id")
    @classmethod
    def required_text_must_not_be_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("learning_goal", "strategy_notes")
    @classmethod
    def optional_text_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("must not be empty when provided")
        return text


class HermesGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_file: Path
    raw_json: str
    sandbox_result: HermesSandboxResult

    @property
    def ok(self) -> bool:
        return self.sandbox_result.ok


def build_hermes_generation_prompt(request: HermesGenerationRequest) -> str:
    learning_goal = request.learning_goal or "Generate a small, reviewable proposal set for local sandbox routing."
    strategy_notes = request.strategy_notes or "Use conservative local-research assumptions and include clear theses."
    return f"""
You are Hermes, a proposal generator for ExaltedFable Agent Trading Lab.

Output ONLY strict JSON. Do not use Markdown. Do not include prose outside JSON.

The JSON must match this exact top-level sandbox schema:
{{
  "agent_id": "{request.agent_id}",
  "team_id": "{request.team_id}",
  "strategy_id": "{request.strategy_id}",
  "agent_role": "{request.agent_role}",
  "strategy_notes": "string",
  "learning_goal": "string",
  "proposals": [{{}}]
}}

Allowed proposal_type values are:
- "stock_long"
- "short_stock"
- "option_long"
- "margin"

For stock_long proposals include: proposal_type, symbol, target_weight or quantity, estimated_price, thesis, confidence.
For short_stock proposals include: proposal_type, symbol, target_short_weight or notional_exposure, estimated_price, thesis, confidence, borrow_available_assumption, and optional borrow_fee_assumption, max_loss_exit_price, forced_cover_threshold.
For option_long proposals include: proposal_type, contract, action, contracts, premium, estimated_total_premium, thesis, confidence, liquidity_open_interest_assumption, assignment_exercise_risk_note. The contract must include underlying_symbol, option_type, expiration, and strike.
For margin proposals include: proposal_type, requested_gross_exposure, thesis, confidence, and optional symbols.

Safety rules:
- No secrets.
- No API keys.
- No broker credentials.
- No execution claims.
- No order placement language.
- Proposals are for paper/simulation routing only.
- No live trading.
- No markdown/prose outside JSON.

Learning goal: {learning_goal}
Strategy notes: {strategy_notes}
""".strip()


def generate_hermes_proposals(
    config: HermesRuntimeConfig,
    request: HermesGenerationRequest,
    output_file: Path | str,
    *,
    http_post=None,
) -> HermesGenerationResult:
    config.validate_ready()
    http_post = http_post or requests.post
    output_path = Path(output_file)
    prompt = build_hermes_generation_prompt(request)
    try:
        response = http_post(
            _chat_completions_url(config.base_url),
            headers=_headers(config),
            json={
                "model": config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You output strict JSON only for local proposal review. "
                            "You cannot place orders or access brokers."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        response_payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Hermes runtime request failed: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Hermes runtime response was not valid JSON.") from exc

    raw_json = _extract_message_content(response_payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(raw_json, encoding="utf-8")
    sandbox_result = load_hermes_sandbox_file(output_path)
    return HermesGenerationResult(
        output_file=output_path,
        raw_json=raw_json,
        sandbox_result=sandbox_result,
    )


def format_hermes_generation_result(result: HermesGenerationResult) -> str:
    return "\n".join(
        [
            "Hermes proposal generation complete.",
            f"Saved raw Hermes JSON: {result.output_file}",
            "Hermes output is proposal JSON only; not execution approval.",
            format_hermes_sandbox_result(result.sandbox_result),
        ]
    )


def _chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _headers(config: HermesRuntimeConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _extract_message_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Hermes response did not match OpenAI-compatible chat completions format.") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Hermes response content was empty or non-text.")
    return content.strip()
