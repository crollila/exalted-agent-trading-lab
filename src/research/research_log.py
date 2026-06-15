"""Research run logging (Task 6).

Persists research runs under the ignored runtime path ``data/research/``:
* ``research_log.jsonl`` — append-only log of every research run.
* ``latest_<team>_research.json`` — latest snapshot per team.

No secrets are written — only queries, results, provenance, and status.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.research.research import ResearchRunResult

DEFAULT_RESEARCH_DIR = Path("data/research")


def log_research(
    run_result: ResearchRunResult,
    *,
    cycle_id: str,
    proposal_source: str,
    proposal_ids: list[str] | None = None,
    research_dir: Path | str = DEFAULT_RESEARCH_DIR,
) -> Path:
    directory = Path(research_dir)
    directory.mkdir(parents=True, exist_ok=True)

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "team_id": run_result.team_id,
        "cycle_id": cycle_id,
        "proposal_source": proposal_source,
        "provider": run_result.provider,
        "available": run_result.available,
        "queries": [q.as_dict() for q in run_result.queries],
        "results": [r.as_dict() for r in run_result.results],
        "errors": run_result.errors,
        "proposal_ids": proposal_ids or [],
        "status_message": run_result.status_message,
    }

    log_path = directory / "research_log.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")

    latest_path = directory / f"latest_{run_result.team_id}_research.json"
    latest_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return latest_path


def read_latest_research(
    team_id: str,
    research_dir: Path | str = DEFAULT_RESEARCH_DIR,
) -> dict[str, Any] | None:
    path = Path(research_dir) / f"latest_{team_id}_research.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def research_log_count(research_dir: Path | str = DEFAULT_RESEARCH_DIR) -> int:
    path = Path(research_dir) / "research_log.jsonl"
    if not path.exists():
        return 0
    return sum(1 for _ in path.open("r", encoding="utf-8"))
