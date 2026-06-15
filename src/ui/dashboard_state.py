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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from src.agents.hermes_strategy_sandbox import load_hermes_sandbox_file
from src.config.settings import Settings
from src.discord_bot.bot import (
    DEFAULT_ASK_TEAM_OUTPUT_DIR,
    DEFAULT_TEAM_CYCLE_DIR,
    REVIEW_APPROVAL_TOKEN,
    RISK_APPROVAL_TOKEN,
    DiscordBotConfig,
    _approval_file_is_true,
    _latest_paper_order_status,
    _proposal_routing_split,
    build_team_paper_cycle_summary,
    latest_agent_run_path_for_team,
)

KNOWN_TEAM_IDS: tuple[str, ...] = ("team_alpha", "team_beta")

DEFAULT_RUN_CYCLE_PROMPT = (
    "Build a conservative 1-stock paper-trading plan to beat SPY this week. "
    "Prefer exactly one stock_long idea. Do not use options, margin, or shorting."
)

_SECRET_KEY_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD")
_SECRET_MASK = "********"
_NOT_SET = "(not set)"

_ENV_LINE_PATTERN = re.compile(r'^(\s*["\']?)([A-Za-z0-9_.\-]+)(["\']?\s*[:=]\s*)(.+)$')


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
    """Redact values of secret-like ``KEY=VALUE`` / ``"key": "value"`` lines.

    Used as a defensive pass before showing any file contents, so an accidental token or
    key in a runtime file can never be rendered in the dashboard.
    """

    redacted_lines: list[str] = []
    for line in text.splitlines():
        match = _ENV_LINE_PATTERN.match(line)
        if match is not None and is_secret_key(match.group(2)):
            redacted_lines.append(f"{match.group(1)}{match.group(2)}{match.group(3)}{_SECRET_MASK}")
        else:
            redacted_lines.append(line)
    return "\n".join(redacted_lines)


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
