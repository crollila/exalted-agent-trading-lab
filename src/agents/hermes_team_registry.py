from __future__ import annotations

import json
from enum import Enum
from json import JSONDecodeError
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class HermesAgentRole(str, Enum):
    RESEARCH_AGENT = "research_agent"
    RISK_AGENT = "risk_agent"
    EXECUTION_AGENT = "execution_agent"
    REVIEW_AGENT = "review_agent"
    STRATEGY_MUTATOR = "strategy_mutator"
    PORTFOLIO_MANAGER = "portfolio_manager"


class HermesAgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    agent_id: str
    team_id: str
    agent_name: str
    role: HermesAgentRole
    description: str
    active: bool
    model_hint: str | None = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    latest_strategy_id: str | None = None
    learning_notes: str | None = None

    @field_validator("agent_id", "team_id", "agent_name", "description")
    @classmethod
    def required_text_must_not_be_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("model_hint", "latest_strategy_id", "learning_notes")
    @classmethod
    def optional_text_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("must not be empty when provided")
        return text

    @field_validator("strengths", "weaknesses")
    @classmethod
    def text_lists_must_not_contain_empty_values(cls, value: list[str]) -> list[str]:
        items = [item.strip() for item in value]
        if any(not item for item in items):
            raise ValueError("must not contain empty values")
        return items


class HermesTeamProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    description: str
    agents: list[HermesAgentProfile]
    active: bool
    strategy_family: str | None = None
    learning_notes: str | None = None

    @field_validator("team_id", "team_name", "description")
    @classmethod
    def required_text_must_not_be_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("strategy_family", "learning_notes")
    @classmethod
    def optional_text_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("must not be empty when provided")
        return text

    @field_validator("agents")
    @classmethod
    def agents_must_not_be_empty(cls, value: list[HermesAgentProfile]) -> list[HermesAgentProfile]:
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def agent_team_ids_must_match_parent(self) -> "HermesTeamProfile":
        for agent in self.agents:
            if agent.team_id != self.team_id:
                raise ValueError(
                    f"agent '{agent.agent_id}' team_id '{agent.team_id}' must match parent team_id '{self.team_id}'"
                )
        return self


class HermesTeamRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teams: list[HermesTeamProfile]
    registry_notes: str | None = None

    @field_validator("teams")
    @classmethod
    def teams_must_not_be_empty(cls, value: list[HermesTeamProfile]) -> list[HermesTeamProfile]:
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("registry_notes")
    @classmethod
    def registry_notes_must_not_be_empty(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("must not be empty when provided")
        return text

    @model_validator(mode="after")
    def registry_ids_must_be_unique(self) -> "HermesTeamRegistry":
        team_ids: set[str] = set()
        agent_ids: set[str] = set()
        for team in self.teams:
            if team.team_id in team_ids:
                raise ValueError(f"duplicate team_id '{team.team_id}'")
            team_ids.add(team.team_id)
            for agent in team.agents:
                if agent.agent_id in agent_ids:
                    raise ValueError(f"duplicate agent_id '{agent.agent_id}'")
                agent_ids.add(agent.agent_id)
        return self


def parse_hermes_team_registry_json(raw_json: str) -> HermesTeamRegistry:
    try:
        payload = json.loads(raw_json)
    except JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.msg}.") from exc

    try:
        return HermesTeamRegistry.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("; ".join(_validation_errors(exc))) from exc


def load_hermes_team_registry_file(path: Path | str) -> HermesTeamRegistry:
    return parse_hermes_team_registry_json(Path(path).read_text(encoding="utf-8"))


def format_hermes_team_registry(registry: HermesTeamRegistry) -> str:
    lines = [
        "Hermes Team Registry",
        "registry only; no trading or LLM calls",
        "Teams:",
    ]
    for team in registry.teams:
        team_status = "active" if team.active else "inactive"
        lines.append(f"- {team.team_id} ({team.team_name}) [{team_status}]")
        if team.strategy_family is not None:
            lines.append(f"  strategy family: {team.strategy_family}")
        for agent in team.agents:
            agent_status = "active" if agent.active else "inactive"
            lines.append(f"  - {agent.agent_id} ({agent.agent_name}) [{agent_status}] role={agent.role.value}")
            if agent.latest_strategy_id is not None:
                lines.append(f"    latest strategy: {agent.latest_strategy_id}")
    return "\n".join(lines)


def _validation_errors(exc: ValidationError) -> list[str]:
    errors = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        errors.append(f"Invalid Hermes team registry at {location}: {error['msg']}.")
    return errors or ["Invalid Hermes team registry."]
