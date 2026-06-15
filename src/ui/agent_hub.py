"""Conversational Agent Hub backend (grounded chat, not proposals).

Natural-language chat with a team or single agent, separate from proposal generation. It
reuses the existing Hermes/Ollama conversational adapter (``ask_hermes_agent``) with an
evidence-grounded prompt, never routes through the proposal sandbox, never generates trade
JSON, and never touches order submission or ``build_team_paper_cycle_summary``.

Honesty: claims about what an agent/team is "working on" are grounded in actual saved runtime
files (latest proposal, risk/review notes, approvals, order status). If the model is
unavailable, status questions get a deterministic answer built from that evidence instead of
a guess. If there is no evidence, that is stated plainly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from src.agents.hermes_runtime import (
    HermesAgentChatRequest,
    HermesRuntimeConfig,
    ask_hermes_agent,
)
from src.ui.dashboard_state import (
    DEFAULT_AGENT_HUB_DIR,
    TeamStatus,
    redact_secret_like_text,
)

_CONVERSATION_RULES = (
    "Answer conversationally, in plain English.",
    "Use ONLY the runtime evidence provided below when saying what the team/agent is working on or has done.",
    "If the evidence shows no proposals/notes, say plainly that there is nothing recorded yet.",
    "Do not invent research topics, symbols, market views, indicators, or current tasks that are not in the evidence.",
    "Do not output trade JSON or a proposal schema.",
    "Do not claim to place orders and do not say a trade was submitted.",
    "If you discuss trade ideas, label them as research only.",
    "This is a paper-only lab; there is no live trading.",
)

_STATUS_QUESTION_MARKERS = (
    "working on",
    "what did you",
    "what have you",
    "latest proposal",
    "what are you thinking",
    "what happened",
    "last cycle",
    "what's the latest",
    "whats the latest",
    "what is the latest",
    "status",
    "what's new",
    "whats new",
)


def agent_role_from_id(agent_id: str) -> str:
    """Infer an agent's role from its id (research/risk/review)."""

    lowered = agent_id.lower()
    if "research" in lowered:
        return "research_agent"
    if "risk" in lowered:
        return "risk_agent"
    if "review" in lowered:
        return "review_agent"
    return "agent"


def is_status_question(message: str) -> bool:
    """True if the message is a 'what are you working on / latest / status' type question."""

    lowered = message.lower()
    return any(marker in lowered for marker in _STATUS_QUESTION_MARKERS)


# ---------------------------------------------------------------------------
# Evidence context
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentHubEvidence:
    team_id: str
    agent_id: str | None
    latest_proposal_path: Path | None
    latest_proposal_mtime: datetime | None
    execution_eligible_count: int
    simulation_only_count: int
    rejected_count: int
    latest_risk_note_path: Path | None
    risk_approved: bool
    latest_review_note_path: Path | None
    review_approved: bool
    stock_long_eligible: bool
    paper_order_status: str
    positions_count: int | None
    latest_report_path: Path | None
    recent_proposal_paths: tuple[Path, ...] = field(default_factory=tuple)
    recent_note_paths: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def has_evidence(self) -> bool:
        return (
            self.latest_proposal_path is not None
            or self.latest_risk_note_path is not None
            or self.latest_review_note_path is not None
        )


def _path_mtime(path: Path | None) -> datetime | None:
    if path is None:
        return None
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def build_agent_hub_evidence_context(
    team_id: str,
    *,
    status: TeamStatus | None,
    agent_id: str | None = None,
    positions_count: int | None = None,
    recent_proposal_paths: Sequence[Path] = (),
    recent_note_paths: Sequence[Path] = (),
    latest_report_path: Path | None = None,
) -> AgentHubEvidence:
    """Build a concise evidence record from saved runtime state (no filesystem scan here).

    All cycle facts come from the passed ``TeamStatus``; recent-file lists and the latest
    report path are supplied by the caller (the UI), keeping this helper pure and hermetic.
    """

    if status is None:
        return AgentHubEvidence(
            team_id=team_id,
            agent_id=agent_id,
            latest_proposal_path=None,
            latest_proposal_mtime=None,
            execution_eligible_count=0,
            simulation_only_count=0,
            rejected_count=0,
            latest_risk_note_path=None,
            risk_approved=False,
            latest_review_note_path=None,
            review_approved=False,
            stock_long_eligible=False,
            paper_order_status="unknown",
            positions_count=positions_count,
            latest_report_path=latest_report_path,
            recent_proposal_paths=tuple(recent_proposal_paths),
            recent_note_paths=tuple(recent_note_paths),
        )
    return AgentHubEvidence(
        team_id=team_id,
        agent_id=agent_id,
        latest_proposal_path=status.latest_proposal_path,
        latest_proposal_mtime=_path_mtime(status.latest_proposal_path),
        execution_eligible_count=status.execution_eligible_count,
        simulation_only_count=status.simulation_only_count,
        rejected_count=status.rejected_count,
        latest_risk_note_path=status.latest_risk_note_path,
        risk_approved=status.risk_approved,
        latest_review_note_path=status.latest_review_note_path,
        review_approved=status.review_approved,
        stock_long_eligible=status.stock_long_eligible,
        paper_order_status=status.paper_order_status,
        positions_count=positions_count,
        latest_report_path=latest_report_path,
        recent_proposal_paths=tuple(recent_proposal_paths),
        recent_note_paths=tuple(recent_note_paths),
    )


def render_evidence_context(evidence: AgentHubEvidence) -> str:
    """Render evidence as concise, secret-redacted text for prompts and the UI panel."""

    lines = [
        f"team_id: {evidence.team_id}",
    ]
    if evidence.agent_id:
        lines.append(f"agent_id: {evidence.agent_id}")
    if not evidence.has_evidence:
        lines.append("No saved proposals or notes yet for this team.")
    lines.extend(
        [
            f"latest proposal: {evidence.latest_proposal_path or 'none'}",
            f"latest proposal time: {evidence.latest_proposal_mtime or 'n/a'}",
            (
                f"latest split: exec-eligible {evidence.execution_eligible_count}, "
                f"sim-only {evidence.simulation_only_count}, rejected {evidence.rejected_count}"
            ),
            f"latest risk note: {evidence.latest_risk_note_path or 'none'}",
            f"parsed risk approval: {'yes' if evidence.risk_approved else 'no'}",
            f"latest review note: {evidence.latest_review_note_path or 'none'}",
            f"parsed review approval: {'yes' if evidence.review_approved else 'no'}",
            f"stock_long subset eligible: {'yes' if evidence.stock_long_eligible else 'no'}",
            f"paper order status: {evidence.paper_order_status}",
            f"positions count: {evidence.positions_count if evidence.positions_count is not None else 'not checked'}",
            f"latest report: {evidence.latest_report_path or 'none'}",
        ]
    )
    if evidence.recent_proposal_paths:
        lines.append("recent proposals: " + ", ".join(str(p) for p in evidence.recent_proposal_paths[:5]))
    if evidence.recent_note_paths:
        lines.append("recent notes: " + ", ".join(str(p) for p in evidence.recent_note_paths[:5]))
    lines.append("safety: paper-only, no live trading")
    return redact_secret_like_text("\n".join(lines))


def deterministic_status_answer(message: str, evidence: AgentHubEvidence) -> str | None:
    """Honest, evidence-only answer to status questions; None if not such a question.

    Never invents topics — only restates what the saved runtime files show.
    """

    if not is_status_question(message):
        return None
    if not evidence.has_evidence:
        return (
            "I don't have any saved proposals or notes yet, so there's nothing I can honestly "
            "say I'm working on. Run a cycle or ask for a proposal first. Nothing has been traded."
        )
    parts = [
        f"Here's what the saved runtime files actually show for {evidence.team_id}:",
        f"- Latest proposal: {evidence.latest_proposal_path or 'none'}"
        + (f" (saved {evidence.latest_proposal_mtime})" if evidence.latest_proposal_mtime else ""),
        (
            f"- Routing split: {evidence.execution_eligible_count} execution-eligible, "
            f"{evidence.simulation_only_count} simulation-only, {evidence.rejected_count} rejected"
        ),
        f"- Risk note: {evidence.latest_risk_note_path or 'none'} (approval: {'yes' if evidence.risk_approved else 'no'})",
        f"- Review note: {evidence.latest_review_note_path or 'none'} (approval: {'yes' if evidence.review_approved else 'no'})",
        f"- stock_long subset eligible for deterministic risk review: {'yes' if evidence.stock_long_eligible else 'no'}",
        f"- Paper order status: {evidence.paper_order_status}",
    ]
    if evidence.latest_report_path:
        parts.append(f"- Latest report: {evidence.latest_report_path}")
    parts.append("That's everything I can ground in evidence — I won't guess beyond it. Nothing has been traded.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def _rules_block() -> str:
    return "\n".join(f"- {rule}" for rule in _CONVERSATION_RULES)


def build_team_chat_prompt(
    team_id: str,
    message: str,
    evidence_text: str,
    *,
    memory_context: str = "",
    data_rules: str = "",
) -> str:
    return f"""
You are speaking as the {team_id} agent team (research, risk, and review agents) in a casual
team chat. Reply in a friendly, readable team voice.

Conversation rules:
{_rules_block()}

Runtime evidence (the ONLY source of truth for what you have done / are working on):
{evidence_text}

Runtime memory (operator-maintained notes only; not model training):
{memory_context or 'No runtime memory provided.'}

Data/tool rules and context:
{data_rules or 'No market data context provided. Do not invent current market facts.'}

When practical, end with a short "Evidence used:" line citing the latest proposal and
risk/review note paths and approvals you relied on.

User message: {message}
""".strip()


def build_agent_chat_prompt(
    team_id: str,
    agent_id: str,
    role: str,
    message: str,
    evidence_text: str,
    *,
    memory_context: str = "",
    data_rules: str = "",
) -> str:
    return f"""
You are {agent_id}, the {role} on {team_id}, chatting casually with your operator.
Answer in your own voice and persona for that role.

Conversation rules:
{_rules_block()}

Runtime evidence (the ONLY source of truth for what you have done / are working on):
{evidence_text}

Runtime memory (operator-maintained notes only; not model training):
{memory_context or 'No runtime memory provided.'}

Data/tool rules and context:
{data_rules or 'No market data context provided. Do not invent current market facts.'}

When practical, end with a short "Evidence used:" line citing the latest proposal and
risk/review note paths and approvals you relied on.

User message: {message}
""".strip()


# ---------------------------------------------------------------------------
# Conversational replies
# ---------------------------------------------------------------------------
def _conversation_log_path(output_dir: Path | str, team_id: str, mode: str, agent_id: str | None) -> Path:
    suffix = f"_{agent_id}" if agent_id else ""
    return Path(output_dir) / f"{team_id}_{mode}{suffix}_chat.md"


def _fallback(who: str, message: str, evidence: AgentHubEvidence) -> str:
    deterministic = deterministic_status_answer(message, evidence)
    if deterministic is not None:
        return deterministic
    return (
        f"{who} can't chat live right now — no model runtime is configured or the model call "
        "failed. Nothing was traded. Enable conversation by setting HERMES_ENABLED / "
        "HERMES_BASE_URL / HERMES_MODEL on the Setup / Secrets page. (Paper-only; no live trading.)"
    )


def team_chat_reply(
    team_id: str,
    message: str,
    *,
    config: HermesRuntimeConfig | None = None,
    status: TeamStatus | None = None,
    evidence: AgentHubEvidence | None = None,
    memory_context: str = "",
    data_rules: str = "",
    chat_fn: Callable[..., object] = ask_hermes_agent,
    output_dir: Path | str = DEFAULT_AGENT_HUB_DIR,
) -> str:
    """Hold a grounded natural conversation with the whole team (no proposals, no trades)."""

    config = config or HermesRuntimeConfig.from_env()
    evidence = evidence or build_agent_hub_evidence_context(team_id, status=status)
    prompt = build_team_chat_prompt(
        team_id,
        message,
        render_evidence_context(evidence),
        memory_context=memory_context,
        data_rules=data_rules,
    )
    request = HermesAgentChatRequest(
        team_id=team_id,
        agent_id=f"{team_id}_team",
        agent_role="team",
        prompt_text=prompt,
    )
    log_path = _conversation_log_path(output_dir, team_id, "team_chat", None)
    try:
        result = chat_fn(config, request, log_path)
        return result.response_text
    except Exception:
        return _fallback(f"The {team_id} team", message, evidence)


def agent_chat_reply(
    team_id: str,
    agent_id: str,
    message: str,
    *,
    role: str | None = None,
    config: HermesRuntimeConfig | None = None,
    status: TeamStatus | None = None,
    evidence: AgentHubEvidence | None = None,
    memory_context: str = "",
    data_rules: str = "",
    chat_fn: Callable[..., object] = ask_hermes_agent,
    output_dir: Path | str = DEFAULT_AGENT_HUB_DIR,
) -> str:
    """Hold a grounded natural conversation with a single agent (no proposals, no trades)."""

    config = config or HermesRuntimeConfig.from_env()
    role = role or agent_role_from_id(agent_id)
    evidence = evidence or build_agent_hub_evidence_context(team_id, status=status, agent_id=agent_id)
    prompt = build_agent_chat_prompt(
        team_id,
        agent_id,
        role,
        message,
        render_evidence_context(evidence),
        memory_context=memory_context,
        data_rules=data_rules,
    )
    request = HermesAgentChatRequest(
        team_id=team_id,
        agent_id=agent_id,
        agent_role=role,
        prompt_text=prompt,
    )
    log_path = _conversation_log_path(output_dir, team_id, "agent_chat", agent_id)
    try:
        result = chat_fn(config, request, log_path)
        return result.response_text
    except Exception:
        return _fallback(agent_id, message, evidence)
