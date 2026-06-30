"""Durable per-iteration audit log for the cheap competition loop (Phase 7U).

Every loop iteration appends one JSON record per team to an ignored JSONL file
under ``data/runtime`` and refreshes a readable ``*_latest.json`` status summary.
This makes the previously-invisible "healthy no-trade vs. silent failure"
distinction observable after the fact, and gives the diagnostic a heartbeat to
detect a dead loop.

Hard properties:

* Secrets are never written. Every string value is passed through
  ``redact_secret_like_text`` and only an allowlisted set of fields is recorded.
* Writing the audit must never crash the loop — all I/O is best-effort and any
  failure is swallowed *after* being surfaced to the console.
* This module never trades, never calls an LLM, and never touches a broker.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ui.dashboard_state import redact_secret_like_text

DEFAULT_AUDIT_DIR = Path("data/runtime/loop_audit")
AUDIT_JSONL_NAME = "iterations.jsonl"
AUDIT_DIR_ENV = "LOOP_AUDIT_DIR"


def resolve_audit_dir(audit_dir: Path | str | None = None) -> Path:
    """Resolve the audit directory: explicit arg > ``LOOP_AUDIT_DIR`` env > default.

    The env override keeps the test suite hermetic (it never writes to the real
    ``data/runtime`` path) without threading a directory through every caller.
    """

    if audit_dir is not None:
        return Path(audit_dir)
    env_dir = os.getenv(AUDIT_DIR_ENV)
    return Path(env_dir) if env_dir else DEFAULT_AUDIT_DIR


@dataclass
class IterationAuditRecord:
    """One team's outcome for one loop iteration. No secrets."""

    iteration_id: str
    iteration: int
    team_id: str
    started_at: str
    finished_at: str | None = None
    market_state: str = "unknown"  # open | closed | unknown
    cycle_action: str = ""  # full_cycle | review_only | cheap_skip | market_closed | quiet | error
    # Gate outcomes.
    kill_switch_engaged: bool = False
    gate_should_run_full_cycle: bool | None = None
    gate_recommend_review_only: bool | None = None
    gate_reason: str = ""
    # Proposal / routing counts (when a cycle ran).
    proposals_count: int | None = None
    approved_count: int | None = None
    simulation_only_count: int | None = None
    rejected_count: int | None = None
    orders_submitted: int | None = None
    broker_rejected_count: int | None = None
    portfolio_decision_type: str | None = None
    portfolio_no_trade: bool | None = None
    no_trade_reason: str | None = None
    # Phase 7Z: candidate-generation auditability (exact, machine-readable).
    no_trade_reason_class: str | None = None
    # Set only when execution-eligible proposals existed but no order submitted
    # (dry-run / kill switch / review-only / team autonomy off / no broker client).
    execution_block_reason: str | None = None
    candidate_generation_allowed: bool | None = None
    reached_candidate_generation: bool | None = None
    provider_outcome: str | None = None
    routed_provider: str | None = None
    routed_model: str | None = None
    provider_failure_category: str | None = None
    # Phase 7Z: fresh broker-state grounding + reconciliation.
    account_read_ok: bool | None = None
    account_snapshot_source: str | None = None
    account_snapshot_time: str | None = None
    reconciliation_status: str | None = None
    reconciliation_conflicts: list[str] | None = None
    # Phase 7Z: same-period benchmark anchor availability.
    benchmark_timeframe: str | None = None
    benchmark_anchors_available: bool | None = None
    # Account summary + usage/caps.
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    orders_today: int | None = None
    max_daily_orders_per_team: int | None = None
    # Phase 7X: bounded prompt-memory metadata (never raw prompt text/secrets).
    memory_daily_summaries_included: list[str] | None = None
    memory_lesson_ids_included: list[str] | None = None
    memory_scorecard_included: bool | None = None
    memory_bounded_context_chars: int | None = None
    memory_malformed_sources: list[str] | None = None
    # Phase 7X: portfolio review / sell-to-close execution state.
    portfolio_action_recommended: str | None = None      # e.g. "trim:NVDA,exit:LOSS"
    portfolio_action_eligible: bool | None = None
    portfolio_action_submitted: int | None = None
    portfolio_action_rejected_reason: str | None = None
    new_buys_blocked_reason: str | None = None
    # Phase 7AA: effective execution configuration this iteration (no secrets).
    # ``execution_mode`` is "dry_run" iff Settings.dry_run is truly true OR the
    # loop was launched with --dry-run-loop; otherwise "paper_execution_enabled".
    settings_dry_run: bool | None = None
    loop_dry_run_flag: bool | None = None
    execution_mode: str | None = None
    working_directory: str | None = None
    spawned_by: str | None = None              # e.g. "watchdog" (how the loop was launched)
    watchdog_spawned: bool | None = None
    # Phase 7AA: provenance of the scorecard-derived fields above so a stale prior
    # scorecard can never masquerade as this iteration's result.
    #   current     - a fresh scorecard was written by THIS iteration's cycle
    #   legacy       - reading a prior scorecard for a non-cycle action (cheap skip)
    #   unavailable - the cycle errored / wrote no scorecard (fields left null)
    source_freshness: str | None = None
    # Phase 7AA: structured, sanitized error metadata for an errored iteration.
    error_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    stages_completed_before_error: list[str] | None = None
    # Failure visibility.
    exception_text: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_safe_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return _redact_record(raw)


def _redact_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Redact every string value so a stray token can never land in the log."""

    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            cleaned[key] = redact_secret_like_text(value)
        elif isinstance(value, list):
            cleaned[key] = [
                redact_secret_like_text(v) if isinstance(v, str) else v for v in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def append_iteration_record(
    record: IterationAuditRecord,
    *,
    audit_dir: Path | str | None = None,
) -> Path | None:
    """Append one JSONL record and refresh the team's latest-status summary.

    Best-effort: returns the JSONL path on success, or ``None`` after printing a
    notice if anything goes wrong (the loop must never die on an audit failure).
    """

    try:
        directory = resolve_audit_dir(audit_dir)
        directory.mkdir(parents=True, exist_ok=True)
        payload = record.as_safe_dict()

        jsonl_path = directory / AUDIT_JSONL_NAME
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

        latest_path = directory / f"{record.team_id}_latest.json"
        latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return jsonl_path
    except Exception as exc:  # noqa: BLE001 - audit must never crash the loop
        print(f"(iteration audit write failed for {record.team_id}: {exc}; continuing loop)")
        return None


def load_latest_status(
    team_id: str,
    *,
    audit_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Read a team's latest-status summary, or None when absent/unreadable."""

    path = resolve_audit_dir(audit_dir) / f"{team_id}_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt status file is treated as absent
        return None


def latest_status_age_seconds(
    team_id: str,
    *,
    audit_dir: Path | str | None = None,
    now: datetime | None = None,
) -> tuple[str | None, float | None]:
    """Return (latest finished/started ISO, age in seconds) for the team, or (None, None)."""

    status = load_latest_status(team_id, audit_dir=audit_dir)
    if not status:
        return None, None
    stamp = status.get("finished_at") or status.get("started_at")
    if not stamp:
        return None, None
    try:
        ts = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return stamp, None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return stamp, (now - ts).total_seconds()


def new_iteration_id(iteration: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S')}-{iteration:04d}"


__all__ = [
    "DEFAULT_AUDIT_DIR",
    "AUDIT_JSONL_NAME",
    "IterationAuditRecord",
    "append_iteration_record",
    "load_latest_status",
    "latest_status_age_seconds",
    "new_iteration_id",
]
