"""Pure, testable helpers for the local operator dashboard.

This module deliberately contains no Streamlit imports so its behavior can be unit-tested
without launching a UI server, Discord, Ollama, Alpaca, the internet, or any secrets. It
reuses the existing gated functions in :mod:`src.discord_bot.bot` rather than duplicating
any trading, routing, or autonomy logic.

Safety notes:
- Nothing here submits Alpaca orders. Running a cycle is delegated to the same
  ``build_team_paper_cycle_summary`` path that Discord uses, which keeps every existing gate
  (autonomy, risk approval, review approval, deterministic Python risk, daily caps, Alpaca
  paper-only wrapper).
- Secret-like values are masked before they can be displayed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, MutableMapping, Sequence

import json
from datetime import datetime, timezone

from src.agents.hermes_strategy_sandbox import load_hermes_sandbox_file
from src.config.settings import Settings
from src.discord_bot.bot import (
    DEFAULT_ASK_TEAM_OUTPUT_DIR,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_TEAM_CYCLE_DIR,
    REVIEW_APPROVAL_TOKEN,
    RISK_APPROVAL_TOKEN,
    DiscordBotConfig,
    TeamAutonomyConfig,
    _approval_file_is_true,
    _latest_paper_order_status,
    _proposal_routing_split,
    build_ask_agent_summary,
    build_ask_team_summary,
    build_team_paper_cycle_summary,
    latest_agent_run_path_for_team,
    save_runtime_team_autonomy_config,
)
from src.agents.hermes_team_registry import load_hermes_team_registry_file

KNOWN_TEAM_IDS: tuple[str, ...] = ("team_alpha", "team_beta")

DEFAULT_RUN_CYCLE_PROMPT = (
    "Build a conservative 1-stock paper-trading plan to beat SPY this week. "
    "Prefer exactly one stock_long idea. Do not use options, margin, or shorting."
)

QUICK_PROMPTS: dict[str, str] = {
    "Conservative 1-stock plan": DEFAULT_RUN_CYCLE_PROMPT,
    "Beat SPY this week": (
        "Propose 1-2 stock_long ideas with the best chance of beating SPY this week. "
        "Paper-only. No options, margin, or shorting."
    ),
    "Risk-off defensive plan": (
        "Build a defensive, risk-off paper plan that prioritizes capital preservation while "
        "still aiming to beat SPY. Prefer one low-volatility stock_long idea. "
        "No options, margin, or shorting."
    ),
    "Compare Alpha/Beta ideas": (
        "Propose a single high-conviction stock_long idea and explain how it differs from a "
        "typical SPY-beating play. Paper-only. No options, margin, or shorting."
    ),
}

# Safe first-test defaults used by the "reset team to safe defaults" control.
SAFE_DEFAULT_MAX_ORDERS_PER_DAY = 1
SAFE_DEFAULT_MAX_DAILY_NOTIONAL: dict[str, float] = {
    "team_alpha": 250000.0,
    "team_beta": 0.0,
}

_SECRET_KEY_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD")
_SECRET_MASK = "********"
_NOT_SET = "(not set)"

_ENV_LINE_PATTERN = re.compile(r'^(\s*["\']?)([A-Za-z0-9_.\-]+)(["\']?\s*[:=]\s*)(.+)$')
# Matches a secret-named assignment anywhere in a line (key contains KEY/SECRET/TOKEN/...).
_INLINE_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r'([A-Za-z0-9_.\-]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Za-z0-9_.\-]*)'
    r'(["\']?\s*[:=]\s*["\']?)'
    r'([^\s"\']+)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------
def is_secret_key(name: str) -> bool:
    """Return True when an env/config key name looks like it holds a secret value."""

    upper = name.upper()
    return any(hint in upper for hint in _SECRET_KEY_HINTS)


def mask_secret(value: object) -> str:
    """Mask a single secret-like value so its contents are never displayed.

    Returns a fixed mask for any non-empty value (never any original characters) and a
    clear ``(not set)`` marker for missing/blank values.
    """

    if value is None:
        return _NOT_SET
    text = str(value).strip()
    if not text:
        return _NOT_SET
    return _SECRET_MASK


def is_configured(value: object) -> bool:
    """Return True when a value is present and non-blank (without revealing it)."""

    return value is not None and str(value).strip() != ""


def redact_secret_like_text(text: str) -> str:
    """Redact secret-like values before any file/log contents are displayed.

    Two passes: (1) line-leading ``KEY=VALUE`` / ``"key": "value"`` assignments whose key
    looks like a secret, and (2) any secret-named assignment appearing mid-line (e.g. a token
    embedded in prose). This is a defensive guard so an accidental token or key in a runtime
    file or log can never be rendered in the dashboard.
    """

    redacted_lines: list[str] = []
    for line in text.splitlines():
        match = _ENV_LINE_PATTERN.match(line)
        if match is not None and is_secret_key(match.group(2)):
            redacted_lines.append(f"{match.group(1)}{match.group(2)}{match.group(3)}{_SECRET_MASK}")
        else:
            redacted_lines.append(line)
    joined = "\n".join(redacted_lines)
    return _INLINE_SECRET_ASSIGNMENT_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_SECRET_MASK}", joined
    )


# ---------------------------------------------------------------------------
# Safe file reading / latest-file lookup
# ---------------------------------------------------------------------------
def read_safe_text(
    path: Path | str | None,
    *,
    max_chars: int = 20000,
    redact: bool = True,
) -> str | None:
    """Read a UTF-8 text file safely; return None when missing/unreadable.

    Secret-like lines are redacted by default and output is truncated to ``max_chars``.
    """

    if path is None:
        return None
    file_path = Path(path)
    if not file_path.is_file():
        return None
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if redact:
        text = redact_secret_like_text(text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


def find_latest_proposal_path(
    team_id: str,
    proposal_output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
) -> Path | None:
    """Return the latest saved proposal JSON path for a team, or None if absent."""

    try:
        return latest_agent_run_path_for_team(team_id, output_dir=proposal_output_dir)
    except FileNotFoundError:
        return None


def find_latest_note_path(
    notes_output_dir: Path | str,
    team_id: str,
    kind: str,
) -> Path | None:
    """Return the latest ``*<kind>*.md`` note path under the team's notes folder.

    ``kind`` is typically ``"risk"`` or ``"review"``. Returns None when none exist.
    """

    safe_team_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", team_id.strip()) or "team"
    team_dir = Path(notes_output_dir) / safe_team_id
    if not team_dir.is_dir():
        return None
    paths = [path for path in team_dir.glob(f"*{kind}*.md") if path.is_file()]
    if not paths:
        return None
    return max(paths, key=lambda path: path.stat().st_mtime)


def list_recent_runtime_files(
    base_dirs: Iterable[Path | str],
    *,
    limit: int = 25,
    suffixes: Sequence[str] = (".json", ".md"),
) -> list[Path]:
    """List the most recently modified runtime files under the given directories.

    Read-only discovery for the viewers; nothing here writes or commits these files.
    """

    files: list[Path] = []
    for base_dir in base_dirs:
        directory = Path(base_dir)
        if not directory.is_dir():
            continue
        for candidate in directory.rglob("*"):
            if candidate.is_file() and candidate.suffix in suffixes:
                files.append(candidate)
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit]


# ---------------------------------------------------------------------------
# Team status collection
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TeamStatus:
    team_id: str
    autonomy_enabled: bool
    mode: str
    max_paper_orders_per_day: int
    max_daily_notional: float
    natural_chat_channel_id: int | None
    latest_proposal_path: Path | None
    latest_risk_note_path: Path | None
    latest_review_note_path: Path | None
    execution_eligible_count: int
    simulation_only_count: int
    rejected_count: int
    risk_approved: bool
    review_approved: bool
    stock_long_eligible: bool
    paper_order_status: str


def collect_team_status(
    team_id: str,
    config: DiscordBotConfig,
    *,
    settings: Settings | None = None,
    proposal_output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
    notes_output_dir: Path | str = DEFAULT_TEAM_CYCLE_DIR,
) -> TeamStatus:
    """Summarize a team's autonomy + latest-cycle state from runtime files.

    Pure read-only aggregation: no broker calls, no order submission. Missing runtime files
    degrade gracefully to zero counts / not-approved. ``paper_order_status`` is only read
    from the local database when ``settings`` is provided.
    """

    autonomy = config.autonomy_for(team_id)

    proposal_path = find_latest_proposal_path(team_id, proposal_output_dir)
    execution_eligible_count = simulation_only_count = rejected_count = 0
    if proposal_path is not None:
        try:
            split = _proposal_routing_split(load_hermes_sandbox_file(proposal_path))
            execution_eligible_count = len(split.execution_eligible_proposals)
            simulation_only_count = len(split.simulation_only_proposals)
            rejected_count = len(split.rejected_proposals)
        except OSError:
            pass

    risk_note_path = find_latest_note_path(notes_output_dir, team_id, "risk")
    review_note_path = find_latest_note_path(notes_output_dir, team_id, "review")
    risk_approved = _approval_file_is_true(risk_note_path, RISK_APPROVAL_TOKEN)
    review_approved = _approval_file_is_true(review_note_path, REVIEW_APPROVAL_TOKEN)
    stock_long_eligible = risk_approved and review_approved and execution_eligible_count >= 1

    if settings is not None:
        paper_order_status = _latest_paper_order_status(team_id, settings=settings)
    else:
        paper_order_status = "not checked"

    return TeamStatus(
        team_id=team_id,
        autonomy_enabled=autonomy.enabled,
        mode=autonomy.mode,
        max_paper_orders_per_day=autonomy.max_paper_orders_per_day,
        max_daily_notional=autonomy.max_daily_notional,
        natural_chat_channel_id=config.team_channel_ids.get(team_id),
        latest_proposal_path=proposal_path,
        latest_risk_note_path=risk_note_path,
        latest_review_note_path=review_note_path,
        execution_eligible_count=execution_eligible_count,
        simulation_only_count=simulation_only_count,
        rejected_count=rejected_count,
        risk_approved=risk_approved,
        review_approved=review_approved,
        stock_long_eligible=stock_long_eligible,
        paper_order_status=paper_order_status,
    )


def team_status_table_rows(statuses: Sequence[TeamStatus]) -> list[dict[str, object]]:
    """Flatten team statuses into simple rows for a status table."""

    rows: list[dict[str, object]] = []
    for status in statuses:
        rows.append(
            {
                "team": status.team_id,
                "autonomy": "enabled" if status.autonomy_enabled else "disabled",
                "mode": status.mode,
                "max_orders/day": status.max_paper_orders_per_day,
                "max_notional": status.max_daily_notional,
                "exec_eligible": status.execution_eligible_count,
                "sim_only": status.simulation_only_count,
                "rejected": status.rejected_count,
                "risk_approved": "yes" if status.risk_approved else "no",
                "review_approved": "yes" if status.review_approved else "no",
                "stock_long_eligible": "yes" if status.stock_long_eligible else "no",
                "paper_orders": status.paper_order_status,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Run-cycle confirmation gate (UI-side guard; does NOT replace any safety gate)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DashboardRunResult:
    ran: bool
    message: str


def run_cycle_block_reason(*, autonomy_enabled: bool, confirmation_checked: bool) -> str | None:
    """Return a blocking reason when an autonomy-enabled cycle lacks UI confirmation.

    This is an extra UI-side speed bump only. It never weakens the real gates: even when
    this returns None, ``build_team_paper_cycle_summary`` still enforces autonomy, risk and
    review approvals, deterministic Python risk, daily caps, and the Alpaca paper-only
    wrapper before any paper order can be submitted.
    """

    if autonomy_enabled and not confirmation_checked:
        return (
            "Autonomy is ENABLED for this team. Running a cycle may attempt Alpaca paper "
            "orders if all existing gates pass. Tick the confirmation checkbox to proceed. "
            "(Deterministic risk, agent approvals, daily caps, and the paper-only wrapper "
            "still apply.)"
        )
    return None


def run_team_cycle_via_dashboard(
    team_id: str,
    prompt_text: str,
    *,
    config: DiscordBotConfig,
    autonomy_enabled: bool,
    confirmation_checked: bool,
    runner: Callable[..., str] = build_team_paper_cycle_summary,
    **runner_kwargs: object,
) -> DashboardRunResult:
    """Run a team paper cycle through the same gated path Discord uses.

    Blocks (without invoking ``runner``) when an autonomy-enabled run is not explicitly
    confirmed in the UI. The dashboard never calls Alpaca order submission directly; order
    placement, if any, happens only inside ``build_team_paper_cycle_summary`` after every
    existing gate passes.
    """

    reason = run_cycle_block_reason(
        autonomy_enabled=autonomy_enabled,
        confirmation_checked=confirmation_checked,
    )
    if reason is not None:
        return DashboardRunResult(ran=False, message=reason)

    output = runner(team_id, prompt_text, config=config, **runner_kwargs)
    return DashboardRunResult(ran=True, message=output)


# ---------------------------------------------------------------------------
# Team runtime config updates (delegates to existing persistence)
# ---------------------------------------------------------------------------
def update_team_runtime_config(
    team_id: str,
    config: DiscordBotConfig,
    *,
    enabled: bool | None = None,
    max_paper_orders_per_day: int | None = None,
    max_daily_notional: float | None = None,
    require_risk_agent_approval: bool | None = None,
    require_review_agent_approval: bool | None = None,
    mode: str | None = None,
) -> Path:
    """Update a team's runtime autonomy config, persisting via the existing helper.

    Only fields passed (non-None) are changed; the rest carry over from the current config.
    Returns the path to the saved runtime config file.
    """

    current = config.autonomy_for(team_id)
    updated = TeamAutonomyConfig(
        team_id=team_id,
        enabled=current.enabled if enabled is None else enabled,
        mode=current.mode if mode is None else mode,
        max_paper_orders_per_day=(
            current.max_paper_orders_per_day
            if max_paper_orders_per_day is None
            else max_paper_orders_per_day
        ),
        max_daily_notional=(
            current.max_daily_notional if max_daily_notional is None else max_daily_notional
        ),
        require_risk_agent_approval=(
            current.require_risk_agent_approval
            if require_risk_agent_approval is None
            else require_risk_agent_approval
        ),
        require_review_agent_approval=(
            current.require_review_agent_approval
            if require_review_agent_approval is None
            else require_review_agent_approval
        ),
    )
    return save_runtime_team_autonomy_config(updated, config.autonomy_config_path)


def reset_team_to_safe_defaults(team_id: str, config: DiscordBotConfig) -> Path:
    """Reset a team to conservative first-test defaults (autonomy off, tight caps)."""

    return update_team_runtime_config(
        team_id,
        config,
        enabled=False,
        max_paper_orders_per_day=SAFE_DEFAULT_MAX_ORDERS_PER_DAY,
        max_daily_notional=SAFE_DEFAULT_MAX_DAILY_NOTIONAL.get(team_id, 0.0),
        require_risk_agent_approval=True,
        require_review_agent_approval=True,
    )


def disable_all_autonomy(config: DiscordBotConfig) -> list[Path]:
    """Kill switch: disable autonomy for every known team. Returns saved config paths."""

    return [update_team_runtime_config(team_id, config, enabled=False) for team_id in KNOWN_TEAM_IDS]


# ---------------------------------------------------------------------------
# Persistent UI notifications (pure; operates on a session-state-like mapping)
# ---------------------------------------------------------------------------
NOTIFICATIONS_STATE_KEY = "command_center_notifications"
NOTIFICATION_TTL_SECONDS = 8.0
NOTIFICATION_LEVELS = ("success", "info", "warning", "error")


def push_notification(
    state: MutableMapping,
    message: str,
    *,
    level: str = "success",
    now: float | None = None,
    ttl_seconds: float = NOTIFICATION_TTL_SECONDS,
) -> list[dict]:
    """Append a persistent notification to a session-state-like mapping.

    Notifications survive Streamlit reruns (so success notices don't flash for one frame)
    until they expire after ``ttl_seconds`` or are dismissed.
    """

    if level not in NOTIFICATION_LEVELS:
        level = "info"
    current = time.time() if now is None else now
    items = list(state.get(NOTIFICATIONS_STATE_KEY, []))
    items.append({"message": str(message), "level": level, "expires_at": current + ttl_seconds})
    state[NOTIFICATIONS_STATE_KEY] = items
    return items


def active_notifications(state: MutableMapping, *, now: float | None = None) -> list[dict]:
    """Return non-expired notifications, pruning expired ones from the mapping."""

    current = time.time() if now is None else now
    items = list(state.get(NOTIFICATIONS_STATE_KEY, []))
    active = [item for item in items if float(item.get("expires_at", 0)) > current]
    state[NOTIFICATIONS_STATE_KEY] = active
    return active


def dismiss_notifications(state: MutableMapping) -> None:
    """Clear all notifications."""

    state[NOTIFICATIONS_STATE_KEY] = []


# ---------------------------------------------------------------------------
# Agent Hub (proposal-only / analysis-only; never trades)
# ---------------------------------------------------------------------------
AGENT_HUB_AGENT_IDS: dict[str, tuple[str, ...]] = {
    "team_alpha": ("alpha_research_01", "alpha_risk_01", "alpha_review_01"),
    "team_beta": ("beta_research_01", "beta_risk_01", "beta_review_01"),
}

# Conversational modes (chat) and structured proposal modes (sandbox-routed).
TEAM_CHAT_MODE = "team_chat"
AGENT_CHAT_MODE = "agent_chat"
ASK_TEAM_MODE = "ask_team"
ASK_AGENT_MODE = "ask_agent"
AGENT_SCOPED_MODES = (AGENT_CHAT_MODE, ASK_AGENT_MODE)
DEFAULT_AGENT_HUB_DIR = Path("data/notes/agent_hub")


def agent_hub_history_key(team_id: str, mode: str, agent_id: str | None = None) -> str:
    """Build a session-state key for a conversation, scoped by team/mode/agent.

    Agent-scoped modes (Agent Chat, Ask Agent for Proposal) include the agent id so each
    agent and each mode keeps its own separate transcript.
    """

    normalized_mode = str(mode).strip().lower().replace(" ", "_")
    base = f"agent_hub::{team_id}::{normalized_mode}"
    if normalized_mode in AGENT_SCOPED_MODES and agent_id:
        base += f"::{agent_id}"
    return base


def get_chat_history(state: MutableMapping, key: str) -> list[dict]:
    """Return the chat history list for a conversation key (empty if none)."""

    return list(state.get(key, []))


def append_chat_message(state: MutableMapping, key: str, role: str, content: str) -> list[dict]:
    """Append a chat message (role/content) to a conversation's history."""

    history = list(state.get(key, []))
    history.append({"role": str(role), "content": str(content)})
    state[key] = history
    return history


def clear_chat_history(state: MutableMapping, key: str) -> None:
    """Clear a conversation's chat history."""

    state[key] = []


def agent_hub_transcript_path(
    key: str,
    *,
    output_dir: Path | str = DEFAULT_AGENT_HUB_DIR,
    now: "datetime | None" = None,
) -> Path:
    """Build a timestamped transcript path under the ignored agent-hub runtime dir."""

    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
    return Path(output_dir) / f"{safe_key}_{timestamp}.md"


def save_agent_hub_transcript(history: Sequence[dict], path: Path | str) -> Path:
    """Write a chat transcript to a runtime markdown file (secret-redacted)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Agent Hub transcript",
        "",
        "_Proposal-only / analysis-only. No trades placed._",
        "",
    ]
    for message in history:
        role = str(message.get("role", "?"))
        content = redact_secret_like_text(str(message.get("content", "")))
        lines.append(f"## {role}")
        lines.append(content)
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def default_research_agent(team_id: str, registry_path: Path | str = DEFAULT_REGISTRY_PATH):
    """Return the active research agent for a team, or None."""

    registry = load_hermes_team_registry_file(registry_path)
    team = next((team for team in registry.teams if team.team_id == team_id), None)
    if team is None:
        return None
    return next(
        (agent for agent in team.agents if agent.active and agent.role.value == "research_agent"),
        None,
    )


def validate_proposal_prompt(text: str) -> str:
    """Return a trimmed proposal prompt, or raise if blank.

    Used to block empty proposal requests in the UI before calling the proposal helper, so a
    blank input can never become an empty ``learning_goal`` downstream.
    """

    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Enter a prompt for the proposal request — it cannot be blank.")
    return cleaned


def agent_hub_ask_agent(
    team_id: str,
    agent_id: str,
    prompt_text: str,
    *,
    runner: Callable[..., str] = build_ask_agent_summary,
    **kwargs: object,
) -> str:
    """Ask a single agent through the existing non-trading ask-agent path.

    Proposal/analysis only. Delegates to ``build_ask_agent_summary``; never submits orders.
    """

    return runner(team_id, agent_id, prompt_text, **kwargs)


def agent_hub_ask_team(
    team_id: str,
    agent_id: str,
    agent_role: str,
    strategy_id: str,
    prompt_text: str,
    *,
    runner: Callable[..., str] = build_ask_team_summary,
    **kwargs: object,
) -> str:
    """Ask a team for proposal JSON through the existing non-trading ask-team path.

    Proposal/analysis only. Delegates to ``build_ask_team_summary``; never submits orders.
    """

    return runner(team_id, agent_id, agent_role, strategy_id, prompt_text, **kwargs)


# ---------------------------------------------------------------------------
# Agent stats derived from runtime files (best-effort estimates)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentStat:
    agent_id: str
    team_id: str
    role: str
    latest_run_path: Path | None
    latest_note_path: Path | None
    latest_action_time: datetime | None
    proposal_files_generated: int
    latest_proposal_count: int
    execution_eligible_count: int
    simulation_only_count: int
    rejected_count: int
    cycles_participated: int
    risk_approved: bool | None
    review_approved: bool | None
    is_estimate: bool = True


def _agent_proposal_files(agent_id: str, team_id: str, proposal_output_dir: Path | str) -> list[Path]:
    directory = Path(proposal_output_dir)
    if not directory.is_dir():
        return []
    matches: list[Path] = []
    for candidate in directory.glob("*.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("team_id") == team_id and payload.get("agent_id") == agent_id:
            matches.append(candidate)
    return sorted(matches, key=lambda path: path.stat().st_mtime, reverse=True)


def _agent_note_files(agent_id: str, team_id: str, notes_output_dir: Path | str) -> list[Path]:
    safe_team_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", team_id.strip()) or "team"
    team_dir = Path(notes_output_dir) / safe_team_id
    if not team_dir.is_dir():
        return []
    matches = [path for path in team_dir.glob(f"{agent_id}_*.md") if path.is_file()]
    return sorted(matches, key=lambda path: path.stat().st_mtime, reverse=True)


def _mtime_datetime(path: Path | None) -> datetime | None:
    if path is None:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def collect_agent_stats(
    team_id: str,
    *,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    proposal_output_dir: Path | str = DEFAULT_ASK_TEAM_OUTPUT_DIR,
    notes_output_dir: Path | str = DEFAULT_TEAM_CYCLE_DIR,
) -> list[AgentStat]:
    """Derive best-effort per-agent stats from runtime files.

    These are runtime-derived estimates, not authoritative history. Agents never have direct
    trade permissions; this only reads saved proposal/note files.
    """

    registry = load_hermes_team_registry_file(registry_path)
    team = next((team for team in registry.teams if team.team_id == team_id), None)
    if team is None:
        return []

    stats: list[AgentStat] = []
    for agent in team.agents:
        if not agent.active:
            continue
        proposal_files = _agent_proposal_files(agent.agent_id, team_id, proposal_output_dir)
        latest_run = proposal_files[0] if proposal_files else None
        latest_proposal_count = 0
        execution_eligible = simulation_only = rejected = 0
        if latest_run is not None:
            try:
                split = _proposal_routing_split(load_hermes_sandbox_file(latest_run))
                execution_eligible = len(split.execution_eligible_proposals)
                simulation_only = len(split.simulation_only_proposals)
                rejected = len(split.rejected_proposals)
                latest_proposal_count = execution_eligible + simulation_only + rejected
            except OSError:
                pass

        note_files = _agent_note_files(agent.agent_id, team_id, notes_output_dir)
        latest_note = note_files[0] if note_files else None
        role = agent.role.value
        risk_approved = _approval_file_is_true(latest_note, RISK_APPROVAL_TOKEN) if role == "risk_agent" else None
        review_approved = (
            _approval_file_is_true(latest_note, REVIEW_APPROVAL_TOKEN) if role == "review_agent" else None
        )

        action_times = [time for time in (_mtime_datetime(latest_run), _mtime_datetime(latest_note)) if time]
        latest_action_time = max(action_times) if action_times else None

        stats.append(
            AgentStat(
                agent_id=agent.agent_id,
                team_id=team_id,
                role=role,
                latest_run_path=latest_run,
                latest_note_path=latest_note,
                latest_action_time=latest_action_time,
                proposal_files_generated=len(proposal_files),
                latest_proposal_count=latest_proposal_count,
                execution_eligible_count=execution_eligible,
                simulation_only_count=simulation_only,
                rejected_count=rejected,
                cycles_participated=len(note_files),
                risk_approved=risk_approved,
                review_approved=review_approved,
            )
        )
    return stats
