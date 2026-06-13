from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.agents.hermes_strategy_sandbox import (
    PAPER_ELIGIBLE_STOCK_LONG,
    REJECTED,
    SIMULATION_ONLY_MARGIN,
    SIMULATION_ONLY_OPTION,
    SIMULATION_ONLY_SHORT,
    HermesSandboxResult,
    load_hermes_sandbox_file,
)
from src.agents.hermes_team_registry import HermesTeamRegistry, load_hermes_team_registry_file


SCORE_FORMULA = (
    "score = paper_eligible_count * 2 + simulation_only_count * 1 - rejected_count * 1"
)
TOURNAMENT_DISCLAIMER = "routing score only, not profitability; no trading or execution approval"


class HermesTournamentProposalRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_path: str
    team_id: str
    agent_id: str
    strategy_id: str
    total_proposals: int = Field(ge=0)
    paper_eligible_stock_long_count: int = Field(ge=0)
    simulation_only_short_count: int = Field(ge=0)
    simulation_only_option_count: int = Field(ge=0)
    simulation_only_margin_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    score: int
    errors: list[str] = Field(default_factory=list)

    @property
    def simulation_only_count(self) -> int:
        return (
            self.simulation_only_short_count
            + self.simulation_only_option_count
            + self.simulation_only_margin_count
        )


class HermesTournamentRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    team_id: str
    score: int
    total_proposals: int = Field(ge=0)
    paper_eligible_stock_long_count: int = Field(ge=0)
    simulation_only_short_count: int = Field(ge=0)
    simulation_only_option_count: int = Field(ge=0)
    simulation_only_margin_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)


class HermesTournamentRoundResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    registry_path: str
    proposal_paths: list[str]
    rows: list[HermesTournamentProposalRow]
    rankings: list[HermesTournamentRanking]
    score_formula: str = SCORE_FORMULA
    disclaimer: str = TOURNAMENT_DISCLAIMER
    errors: list[str] = Field(default_factory=list)

    @property
    def winner(self) -> HermesTournamentRanking | None:
        return self.rankings[0] if self.rankings else None


class HermesTournamentRoundArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    json_path: Path
    markdown_path: Path


def run_hermes_tournament_round(
    registry_path: Path | str,
    proposal_paths: list[Path | str],
    *,
    generated_at: datetime | None = None,
) -> HermesTournamentRoundResult:
    generated_at = generated_at or datetime.now(timezone.utc)
    registry = load_hermes_team_registry_file(registry_path)
    rows = [
        _proposal_row_from_file(
            registry=registry,
            proposal_path=proposal_path,
        )
        for proposal_path in proposal_paths
    ]
    return HermesTournamentRoundResult(
        generated_at=generated_at,
        registry_path=str(registry_path),
        proposal_paths=[str(path) for path in proposal_paths],
        rows=rows,
        rankings=_rank_teams(rows),
        errors=[error for row in rows for error in row.errors],
    )


def save_hermes_tournament_round_artifacts(
    result: HermesTournamentRoundResult,
    output_dir: Path | str = Path("data/experiments"),
) -> HermesTournamentRoundArtifacts:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    timestamp = result.generated_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = output_path / f"hermes_tournament_round_{timestamp}.json"
    markdown_path = output_path / f"hermes_tournament_round_{timestamp}.md"
    json_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(format_hermes_tournament_round_markdown(result), encoding="utf-8")
    return HermesTournamentRoundArtifacts(json_path=json_path, markdown_path=markdown_path)


def format_hermes_tournament_round(result: HermesTournamentRoundResult) -> str:
    lines = [
        "Hermes Tournament Round",
        f"Score formula: {result.score_formula}",
        f"Disclaimer: {result.disclaimer}",
    ]
    winner = result.winner
    if winner is None:
        lines.append("Winner: none")
    else:
        lines.append(f"Winner: {winner.team_id} (score {winner.score})")
    lines.extend(
        [
            "",
            "Proposal rows",
            "team ID | agent ID | strategy ID | total | paper eligible | sim short | sim option | sim margin | rejected | score",
            "------- | -------- | ----------- | ----- | -------------- | --------- | ---------- | ---------- | -------- | -----",
        ]
    )
    for row in result.rows:
        lines.append(
            f"{row.team_id} | {row.agent_id} | {row.strategy_id} | {row.total_proposals} | "
            f"{row.paper_eligible_stock_long_count} | {row.simulation_only_short_count} | "
            f"{row.simulation_only_option_count} | {row.simulation_only_margin_count} | "
            f"{row.rejected_count} | {row.score}"
        )
    lines.extend(
        [
            "",
            "Team rankings",
            "rank | team ID | score | total | rejected",
            "---- | ------- | ----- | ----- | --------",
        ]
    )
    for ranking in result.rankings:
        lines.append(
            f"{ranking.rank} | {ranking.team_id} | {ranking.score} | "
            f"{ranking.total_proposals} | {ranking.rejected_count}"
        )
    if result.errors:
        lines.append("")
        lines.append("Warnings")
        lines.extend(f"- {error}" for error in result.errors)
    return "\n".join(lines)


def format_hermes_tournament_round_markdown(result: HermesTournamentRoundResult) -> str:
    return "\n".join(
        [
            "# Hermes Tournament Round",
            "",
            f"Generated: {result.generated_at.isoformat()}",
            f"Registry: `{result.registry_path}`",
            f"Proposal files: {', '.join(f'`{path}`' for path in result.proposal_paths)}",
            f"Score formula: `{result.score_formula}`",
            f"Disclaimer: {result.disclaimer}.",
            "",
            format_hermes_tournament_round(result),
            "",
        ]
    )


def _proposal_row_from_file(
    registry: HermesTeamRegistry,
    proposal_path: Path | str,
) -> HermesTournamentProposalRow:
    result = load_hermes_sandbox_file(proposal_path)
    if result.request is None:
        errors = result.errors or ["Proposal file could not be parsed."]
        return _error_row(str(proposal_path), errors)

    team_ids = {team.team_id for team in registry.teams}
    row = _proposal_row_from_sandbox_result(str(proposal_path), result)
    if result.request.team_id not in team_ids:
        errors = [*row.errors, f"Unknown team_id '{result.request.team_id}' in proposal file '{proposal_path}'."]
        return row.model_copy(
            update={
                "rejected_count": row.rejected_count + 1,
                "score": _score(
                    paper_eligible_count=row.paper_eligible_stock_long_count,
                    simulation_only_count=row.simulation_only_count,
                    rejected_count=row.rejected_count + 1,
                ),
                "errors": errors,
            }
        )
    return row


def _proposal_row_from_sandbox_result(
    proposal_path: str,
    result: HermesSandboxResult,
) -> HermesTournamentProposalRow:
    if result.request is None:
        return _error_row(proposal_path, result.errors)

    counts = result.route_counts()
    paper_eligible_count = counts[PAPER_ELIGIBLE_STOCK_LONG]
    simulation_only_count = (
        counts[SIMULATION_ONLY_SHORT]
        + counts[SIMULATION_ONLY_OPTION]
        + counts[SIMULATION_ONLY_MARGIN]
    )
    rejected_count = counts[REJECTED]
    return HermesTournamentProposalRow(
        proposal_path=proposal_path,
        team_id=result.request.team_id,
        agent_id=result.request.agent_id,
        strategy_id=result.request.strategy_id,
        total_proposals=len(result.routed_proposals),
        paper_eligible_stock_long_count=paper_eligible_count,
        simulation_only_short_count=counts[SIMULATION_ONLY_SHORT],
        simulation_only_option_count=counts[SIMULATION_ONLY_OPTION],
        simulation_only_margin_count=counts[SIMULATION_ONLY_MARGIN],
        rejected_count=rejected_count,
        score=_score(
            paper_eligible_count=paper_eligible_count,
            simulation_only_count=simulation_only_count,
            rejected_count=rejected_count,
        ),
        errors=[error for proposal in result.routed_proposals for error in proposal.errors],
    )


def _error_row(proposal_path: str, errors: list[str]) -> HermesTournamentProposalRow:
    return HermesTournamentProposalRow(
        proposal_path=proposal_path,
        team_id="invalid",
        agent_id="invalid",
        strategy_id="invalid",
        total_proposals=0,
        paper_eligible_stock_long_count=0,
        simulation_only_short_count=0,
        simulation_only_option_count=0,
        simulation_only_margin_count=0,
        rejected_count=1,
        score=-1,
        errors=errors,
    )


def _rank_teams(rows: list[HermesTournamentProposalRow]) -> list[HermesTournamentRanking]:
    aggregates: dict[str, dict[str, int]] = {}
    for row in rows:
        aggregate = aggregates.setdefault(
            row.team_id,
            {
                "total_proposals": 0,
                "paper_eligible_stock_long_count": 0,
                "simulation_only_short_count": 0,
                "simulation_only_option_count": 0,
                "simulation_only_margin_count": 0,
                "rejected_count": 0,
                "score": 0,
            },
        )
        aggregate["total_proposals"] += row.total_proposals
        aggregate["paper_eligible_stock_long_count"] += row.paper_eligible_stock_long_count
        aggregate["simulation_only_short_count"] += row.simulation_only_short_count
        aggregate["simulation_only_option_count"] += row.simulation_only_option_count
        aggregate["simulation_only_margin_count"] += row.simulation_only_margin_count
        aggregate["rejected_count"] += row.rejected_count
        aggregate["score"] += row.score

    sorted_items = sorted(
        aggregates.items(),
        key=lambda item: (-item[1]["score"], item[1]["rejected_count"], item[0]),
    )
    return [
        HermesTournamentRanking(
            rank=index,
            team_id=team_id,
            **values,
        )
        for index, (team_id, values) in enumerate(sorted_items, start=1)
    ]


def _score(
    *,
    paper_eligible_count: int,
    simulation_only_count: int,
    rejected_count: int,
) -> int:
    return paper_eligible_count * 2 + simulation_only_count - rejected_count
