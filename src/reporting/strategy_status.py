from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_STRATEGY_STATUSES = ("active", "promoted", "retest", "modified", "retired")
DEFAULT_STRATEGY_STATUS_PATH = Path("data/notes/strategy_status.md")


@dataclass(frozen=True)
class StrategyStatusResult:
    saved: bool
    registry_path: Path
    message: str


@dataclass(frozen=True)
class StrategyStatusReadResult:
    registry_path: Path
    message: str


@dataclass(frozen=True)
class StrategyStatusEntry:
    timestamp: str
    strategy_id: str
    status: str
    reason: str
    source_note: str | None
    next_action: str | None


def set_strategy_status(
    strategy_id: str,
    status: str,
    reason: str,
    registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
    source_note: Path | str | None = None,
    next_action: str | None = None,
    status_timestamp: datetime | None = None,
) -> StrategyStatusResult:
    if not strategy_id.strip():
        raise ValueError("strategy ID is required")
    if status not in ALLOWED_STRATEGY_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(ALLOWED_STRATEGY_STATUSES)}")
    if not reason.strip():
        raise ValueError("reason is required")

    active_registry_path = Path(registry_path)
    active_registry_path.parent.mkdir(parents=True, exist_ok=True)
    entry = format_strategy_status_entry(
        strategy_id=strategy_id,
        status=status,
        reason=reason,
        source_note=source_note,
        next_action=next_action,
        status_timestamp=status_timestamp,
    )

    if active_registry_path.exists():
        existing_text = active_registry_path.read_text(encoding="utf-8")
        separator = "" if existing_text.endswith("\n") else "\n"
        active_registry_path.write_text(f"{existing_text}{separator}\n{entry}", encoding="utf-8")
    else:
        active_registry_path.write_text("# Strategy Status Registry\n\n" + entry, encoding="utf-8")

    return StrategyStatusResult(
        saved=True,
        registry_path=active_registry_path,
        message=f"Saved strategy status registry: {active_registry_path}",
    )


def read_strategy_status_registry(
    registry_path: Path | str = DEFAULT_STRATEGY_STATUS_PATH,
) -> StrategyStatusReadResult:
    active_registry_path = Path(registry_path)
    if not active_registry_path.exists():
        return StrategyStatusReadResult(
            registry_path=active_registry_path,
            message=f"No strategy status registry found at {active_registry_path}.",
        )

    markdown = active_registry_path.read_text(encoding="utf-8")
    entries = parse_strategy_status_entries(markdown)
    if not entries:
        return StrategyStatusReadResult(
            registry_path=active_registry_path,
            message=f"No strategy statuses found in {active_registry_path}.",
        )

    return StrategyStatusReadResult(
        registry_path=active_registry_path,
        message=format_strategy_status_registry(entries=entries, registry_path=active_registry_path),
    )


def format_strategy_status_entry(
    strategy_id: str,
    status: str,
    reason: str,
    source_note: Path | str | None = None,
    next_action: str | None = None,
    status_timestamp: datetime | None = None,
) -> str:
    timestamp = (status_timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    lines = [
        f"## Status - {timestamp}",
        "",
        f"- Status timestamp: {timestamp}",
        f"- Strategy ID: `{strategy_id}`",
        f"- Status: `{status}`",
        f"- Reason: {reason}",
    ]
    if source_note is not None:
        lines.append(f"- Source note path: `{Path(source_note)}`")
    if next_action:
        lines.append(f"- Next action: {next_action}")

    lines.extend(
        [
            "- Safety reminder:",
            "  - Research status only.",
            "  - Not live trading approval.",
            "  - No broker/order behavior changed.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_strategy_status_entries(markdown: str) -> list[StrategyStatusEntry]:
    entries: list[StrategyStatusEntry] = []
    blocks = markdown.split("## Status - ")
    for block in blocks[1:]:
        parsed = _entry_from_block(block)
        if parsed is not None:
            entries.append(parsed)
    return entries


def format_strategy_status_registry(entries: list[StrategyStatusEntry], registry_path: Path) -> str:
    latest_entries = _latest_entries(entries)
    lines = [
        "Strategy Status Registry",
        f"Registry path: {registry_path}",
        "",
        "Current statuses",
        _text_table(
            headers=("strategy ID", "latest status", "reason", "timestamp", "next action", "source note"),
            rows=[
                (
                    entry.strategy_id,
                    entry.status,
                    entry.reason,
                    entry.timestamp,
                    entry.next_action or "",
                    entry.source_note or "",
                )
                for entry in latest_entries
            ],
        ),
        "",
        f"History entries: {len(entries)}",
        "Safety reminder: research status only; not live trading approval; no broker/order behavior changed.",
        "",
        "Status history",
        _text_table(
            headers=("timestamp", "strategy ID", "status", "reason"),
            rows=[
                (entry.timestamp, entry.strategy_id, entry.status, entry.reason)
                for entry in entries
            ],
        ),
    ]
    return "\n".join(lines)


def _latest_entries(entries: list[StrategyStatusEntry]) -> list[StrategyStatusEntry]:
    latest_by_strategy: dict[str, StrategyStatusEntry] = {}
    for entry in entries:
        latest_by_strategy[entry.strategy_id] = entry
    return sorted(latest_by_strategy.values(), key=lambda entry: entry.strategy_id)


def _entry_from_block(block: str) -> StrategyStatusEntry | None:
    fields: dict[str, str] = {}
    lines = block.splitlines()
    if not lines:
        return None
    fields["Status timestamp"] = lines[0].strip()
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        key, separator, value = stripped[2:].partition(":")
        if not separator:
            continue
        fields[key] = _unquote_markdown_code(value.strip())

    required = ("Status timestamp", "Strategy ID", "Status", "Reason")
    if any(not fields.get(field) for field in required):
        return None

    return StrategyStatusEntry(
        timestamp=fields["Status timestamp"],
        strategy_id=fields["Strategy ID"],
        status=fields["Status"],
        reason=fields["Reason"],
        source_note=fields.get("Source note path"),
        next_action=fields.get("Next action"),
    )


def _unquote_markdown_code(value: str) -> str:
    if value.startswith("`") and value.endswith("`") and len(value) >= 2:
        return value[1:-1]
    return value


def _text_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(str(value)) for value in column) for column in zip(headers, *rows)]
    lines = [
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))
