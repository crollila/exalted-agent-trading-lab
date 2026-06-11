from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_RESEARCH_DECISIONS = ("promote", "modify", "retest", "retire", "no_decision")
DEFAULT_DECISION_LEDGER_PATH = Path("data/notes/research_decisions.md")


@dataclass(frozen=True)
class ResearchDecisionResult:
    saved: bool
    ledger_path: Path
    message: str


@dataclass(frozen=True)
class ResearchDecisionLedgerResult:
    ledger_path: Path
    message: str


def record_research_decision(
    strategy_id: str,
    decision: str,
    reason: str,
    ledger_path: Path | str = DEFAULT_DECISION_LEDGER_PATH,
    source_note: Path | str | None = None,
    next_action: str | None = None,
    decision_timestamp: datetime | None = None,
) -> ResearchDecisionResult:
    if not strategy_id.strip():
        raise ValueError("strategy ID is required")
    if decision not in ALLOWED_RESEARCH_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(ALLOWED_RESEARCH_DECISIONS)}")
    if not reason.strip():
        raise ValueError("reason is required")

    active_ledger_path = Path(ledger_path)
    active_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    entry = format_research_decision_entry(
        strategy_id=strategy_id,
        decision=decision,
        reason=reason,
        source_note=source_note,
        next_action=next_action,
        decision_timestamp=decision_timestamp,
    )

    if active_ledger_path.exists():
        existing_text = active_ledger_path.read_text(encoding="utf-8")
        separator = "" if existing_text.endswith("\n") else "\n"
        active_ledger_path.write_text(f"{existing_text}{separator}\n{entry}", encoding="utf-8")
    else:
        active_ledger_path.write_text("# Research Decision Ledger\n\n" + entry, encoding="utf-8")

    return ResearchDecisionResult(
        saved=True,
        ledger_path=active_ledger_path,
        message=f"Saved research decision ledger: {active_ledger_path}",
    )


def read_research_decision_ledger(
    ledger_path: Path | str = DEFAULT_DECISION_LEDGER_PATH,
) -> ResearchDecisionLedgerResult:
    active_ledger_path = Path(ledger_path)
    if not active_ledger_path.exists():
        return ResearchDecisionLedgerResult(
            ledger_path=active_ledger_path,
            message=f"No research decision ledger found at {active_ledger_path}.",
        )
    return ResearchDecisionLedgerResult(
        ledger_path=active_ledger_path,
        message=active_ledger_path.read_text(encoding="utf-8"),
    )


def format_research_decision_entry(
    strategy_id: str,
    decision: str,
    reason: str,
    source_note: Path | str | None = None,
    next_action: str | None = None,
    decision_timestamp: datetime | None = None,
) -> str:
    timestamp = (decision_timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    lines = [
        f"## Decision - {timestamp}",
        "",
        f"- Decision timestamp: {timestamp}",
        f"- Strategy ID: `{strategy_id}`",
        f"- Decision: `{decision}`",
        f"- Reason: {reason}",
    ]
    if source_note is not None:
        lines.append(f"- Source note path: `{Path(source_note)}`")
    if next_action:
        lines.append(f"- Next action: {next_action}")

    lines.extend(
        [
            "- Safety reminder:",
            "  - Research decision only.",
            "  - Not live trading approval.",
            "  - No broker/order behavior changed.",
            "",
        ]
    )
    return "\n".join(lines)
