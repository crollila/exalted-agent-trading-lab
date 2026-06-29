"""Memory inventory + safe, idempotent retention maintenance (Phase 7W).

``inventory`` powers ``memory-status`` (read-only). ``run_maintenance`` powers
``memory-maintenance`` (dry-run by default; ``--apply`` to act). Maintenance
archives eligible old raw artifacts into compressed weekly archives, then deletes
them, and is safe to re-run after an interruption.

Hard safety rules:

* Default is dry-run; nothing is touched unless ``apply=True``.
* Never deletes today's data, the current/latest daily summary, current
  portfolio-review (position-thesis) records, or durable playbook lessons.
* Only ever touches the configured runtime memory directories — never ``.env``,
  source code, DB migrations, Git files, or user notes outside runtime.
* Archive-then-delete with a manifest, so re-running is idempotent and an
  interrupted run resumes safely.
* Reports (JSON + Markdown) record every archive/delete/skip with a reason. No
  secrets are read or written.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.market_time import ny_trading_date
from src.competition.memory_config import MemoryConfig, memory_dirs
from src.competition.playbook import TeamPlaybook

# Categories pruned by retention (durable layers like the playbook are excluded).
PRUNABLE = ("daily_summary", "daily_learning", "raw_audit", "agent_response", "proposal")
RETENTION_KEY = {
    "daily_summary": "daily_summary",
    "daily_learning": "daily_summary",
    "raw_audit": "raw_audit",
    "agent_response": "agent_response",
    "proposal": "proposal",
}


# --- inventory ----------------------------------------------------------------


@dataclass
class CategoryInventory:
    category: str
    path: str
    exists: bool
    file_count: int
    total_bytes: int
    oldest: str | None
    newest: str | None
    malformed: list[str] = field(default_factory=list)
    retention_days: int | None = None
    eligible_for_cleanup: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryInventory:
    team_id: str
    generated_at: str
    categories: list[CategoryInventory]
    playbook_total: int
    playbook_active: int
    playbook_retired: int
    scorecard_available: bool
    next_cleanup_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "generated_at": self.generated_at,
            "categories": [c.as_dict() for c in self.categories],
            "playbook_total": self.playbook_total,
            "playbook_active": self.playbook_active,
            "playbook_retired": self.playbook_retired,
            "scorecard_available": self.scorecard_available,
            "next_cleanup_note": self.next_cleanup_note,
        }


def _iter_team_files(directory: Path, team_id: str) -> list[Path]:
    if not directory.exists():
        return []
    out: list[Path] = []
    for p in directory.iterdir():
        if not p.is_file():
            continue
        # Team-scoped files start with the team id; shared files (e.g. the raw-audit
        # JSONL) are included for all teams.
        if p.name.startswith(team_id) or not (p.name.startswith("team_alpha") or p.name.startswith("team_beta")):
            out.append(p)
    return out


def _file_dt(path: Path, now: datetime) -> datetime:
    """Best-effort artifact date: parse a YYYY-MM-DD in the name, else mtime."""

    import re

    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if m:
        try:
            return datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    m2 = re.search(r"(\d{8})T", path.name)
    if m2:
        try:
            return datetime.strptime(m2.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return now


def inventory(team_id: str, config: MemoryConfig, *, root: Path | str | None = None,
             now: datetime | None = None) -> MemoryInventory:
    now = now or datetime.now(timezone.utc)
    dirs = memory_dirs() if root is None else memory_dirs(root)
    cats: list[CategoryInventory] = []

    for category in PRUNABLE:
        directory = dirs[category]
        files = _iter_team_files(directory, team_id)
        total = sum((f.stat().st_size for f in files), 0)
        malformed = []
        for f in files:
            if f.suffix == ".json":
                try:
                    json.loads(f.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - flag, never crash
                    malformed.append(str(f))
        dts = [(f, _file_dt(f, now)) for f in files]
        retention = config.retention_days_for(RETENTION_KEY[category])
        cutoff = retention if retention is not None else None
        eligible = 0
        if cutoff is not None:
            eligible = sum(1 for _f, dt in dts if (now - dt).days > cutoff)
        cats.append(CategoryInventory(
            category=category,
            path=str(directory),
            exists=directory.exists(),
            file_count=len(files),
            total_bytes=total,
            oldest=(min(dts, key=lambda x: x[1])[1].date().isoformat() if dts else None),
            newest=(max(dts, key=lambda x: x[1])[1].date().isoformat() if dts else None),
            malformed=malformed,
            retention_days=retention,
            eligible_for_cleanup=eligible,
        ))

    playbook = TeamPlaybook.load(team_id)
    active = len(playbook.active_lessons())
    total_lessons = len(playbook.lessons)
    scorecard_dir = Path("data/scorecards")
    scorecard_available = scorecard_dir.exists() and any(
        scorecard_dir.glob(f"{team_id}_*.json")
    )
    total_eligible = sum(c.eligible_for_cleanup for c in cats)
    note = (
        f"{total_eligible} file(s) beyond retention; run memory-maintenance --apply to archive+delete."
        if total_eligible else "No files beyond retention; nothing to clean up."
    )
    return MemoryInventory(
        team_id=team_id, generated_at=now.isoformat(), categories=cats,
        playbook_total=total_lessons, playbook_active=active,
        playbook_retired=total_lessons - active,
        scorecard_available=bool(scorecard_available), next_cleanup_note=note,
    )


# --- maintenance --------------------------------------------------------------


@dataclass
class MaintenanceAction:
    category: str
    path: str
    action: str  # archive | delete | skip
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso_week_tag(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _manifest_path(archive_dir: Path) -> Path:
    return archive_dir / "manifest.json"


def _load_manifest(archive_dir: Path) -> dict[str, Any]:
    path = _manifest_path(archive_dir)
    if not path.exists():
        return {"archived": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"archived": {}}
    except Exception:  # noqa: BLE001
        return {"archived": {}}


def _save_manifest(archive_dir: Path, manifest: dict[str, Any]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(archive_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _is_current(path: Path, team_id: str) -> bool:
    """Latest/current artifacts that must never be pruned."""

    name = path.name
    return name.endswith("_latest.json") or name.endswith("_latest.md") or "_latest" in name


def plan_maintenance(
    team_id: str, config: MemoryConfig, *, root: Path | str | None = None,
    now: datetime | None = None,
) -> list[MaintenanceAction]:
    """Compute the maintenance plan (no side effects)."""

    now = now or datetime.now(timezone.utc)
    today = ny_trading_date(now).isoformat()
    dirs = memory_dirs() if root is None else memory_dirs(root)
    actions: list[MaintenanceAction] = []

    for category in PRUNABLE:
        if category == "raw_audit":
            continue  # handled separately (record-level JSONL retention)
        directory = dirs[category]
        retention = config.retention_days_for(RETENTION_KEY[category])
        files = _iter_team_files(directory, team_id)
        if retention is None or not files:
            continue
        # For daily-summary layers, the newest dated file is the "current summary"
        # and is always kept. Other raw categories (agent_response/proposal) have no
        # such current-record to protect and are pruned purely by age.
        protect_newest = category in ("daily_summary", "daily_learning")
        dated = sorted(((f, _file_dt(f, now)) for f in files), key=lambda x: x[1], reverse=True)
        newest_path = dated[0][0] if (dated and protect_newest) else None
        for f, dt in dated:
            age_days = (now - dt).days
            if _is_current(f, team_id) or (newest_path is not None and f == newest_path):
                continue  # never the current/latest summary
            if dt.date().isoformat() == today:
                continue  # never today's data
            if age_days <= retention:
                continue
            act = "archive" if config.keep_weekly_archives else "delete"
            actions.append(MaintenanceAction(
                category, str(f), act,
                f"age {age_days}d > retention {retention}d",
            ))
    # raw-audit JSONL: plan a record-level rotation if old records exist.
    actions.extend(_plan_raw_audit(team_id, config, dirs, now))
    return actions


def _plan_raw_audit(team_id, config, dirs, now) -> list[MaintenanceAction]:
    jsonl = dirs["raw_audit"] / "iterations.jsonl"
    if not jsonl.exists():
        return []
    retention = config.raw_audit_retention_days
    cutoff = now.timestamp() - retention * 86400
    old = 0
    try:
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts = rec.get("finished_at") or rec.get("started_at")
                dt = datetime.fromisoformat(ts) if ts else None
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt and dt.timestamp() < cutoff:
                    old += 1
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return []
    if old <= 0:
        return []
    act = "archive" if config.keep_weekly_archives else "delete"
    return [MaintenanceAction("raw_audit", str(jsonl), act,
                              f"{old} record(s) older than {retention}d to rotate out")]


def _archive_file(src: Path, archive_dir: Path, category: str, now: datetime,
                  manifest: dict[str, Any]) -> tuple[bool, str]:
    """Gzip a file into the weekly archive. Idempotent via the manifest."""

    key = str(src)
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return False, "source missing"
    prior = manifest["archived"].get(key)
    if prior and prior.get("mtime") == mtime:
        return True, "already archived"
    week_dir = archive_dir / category / _iso_week_tag(now)
    week_dir.mkdir(parents=True, exist_ok=True)
    dest = week_dir / (src.name + ".gz")
    with src.open("rb") as fin, gzip.open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()[:16]
    manifest["archived"][key] = {
        "archived_path": str(dest), "mtime": mtime, "sha256": digest,
        "archived_at": now.isoformat(),
    }
    return True, str(dest)


@dataclass
class MaintenanceReport:
    team_id: str
    mode: str
    generated_at: str
    retention: dict[str, Any]
    actions: list[MaintenanceAction]
    archived: int = 0
    deleted: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id, "mode": self.mode, "generated_at": self.generated_at,
            "retention": self.retention,
            "totals": {"archived": self.archived, "deleted": self.deleted, "skipped": self.skipped},
            "actions": [a.as_dict() for a in self.actions],
        }

    def to_markdown(self) -> str:
        out = [f"# Memory maintenance ({self.mode}) - {self.team_id}", "",
               f"_Generated: {self.generated_at}_", "",
               f"- Archived: {self.archived} | Deleted: {self.deleted} | Skipped: {self.skipped}", "",
               "| Category | Action | Path | Reason |", "|---|---|---|---|"]
        for a in self.actions:
            out.append(f"| {a.category} | {a.action} | {a.path} | {a.reason} |")
        out.append("")
        out.append("_Paper-only memory maintenance. No .env/source/DB/Git changes; durable playbook never auto-deleted._")
        return "\n".join(out)


def run_maintenance(
    team_id: str, config: MemoryConfig, *, apply: bool = False,
    root: Path | str | None = None, now: datetime | None = None,
) -> MaintenanceReport:
    """Plan, then (only when ``apply``) archive+delete idempotently."""

    now = now or datetime.now(timezone.utc)
    dirs = memory_dirs() if root is None else memory_dirs(root)
    archive_dir = dirs["archive"]
    plan = plan_maintenance(team_id, config, root=root, now=now)
    mode = "apply" if apply else "dry-run"
    report = MaintenanceReport(
        team_id=team_id, mode=mode, generated_at=now.isoformat(),
        retention=config.summary(), actions=[],
    )

    if not apply:
        for a in plan:
            report.actions.append(MaintenanceAction(a.category, a.path, f"would_{a.action}", a.reason))
            report.skipped += 1
        _save_report(report, archive_dir)
        return report

    manifest = _load_manifest(archive_dir)
    for a in plan:
        path = Path(a.path)
        if a.category == "raw_audit":
            ok, detail = _rotate_raw_audit(path, archive_dir, config, now, manifest)
            report.actions.append(MaintenanceAction(a.category, a.path,
                                                    "archive_rotate" if ok else "skip", detail))
            if ok:
                report.archived += 1
            else:
                report.skipped += 1
            continue
        if not path.exists():
            report.actions.append(MaintenanceAction(a.category, a.path, "skip", "already removed (idempotent)"))
            report.skipped += 1
            continue
        if a.action == "archive":
            ok, detail = _archive_file(path, archive_dir, a.category, now, manifest)
            if not ok:
                report.actions.append(MaintenanceAction(a.category, a.path, "skip", detail))
                report.skipped += 1
                continue
            report.archived += 1
            path.unlink(missing_ok=True)
            report.actions.append(MaintenanceAction(a.category, a.path, "archive+delete", detail))
            report.deleted += 1
        else:  # delete-only (archives disabled)
            path.unlink(missing_ok=True)
            report.actions.append(MaintenanceAction(a.category, a.path, "delete", a.reason))
            report.deleted += 1

    _save_manifest(archive_dir, manifest)
    _save_report(report, archive_dir)
    return report


def _rotate_raw_audit(jsonl: Path, archive_dir: Path, config: MemoryConfig,
                      now: datetime, manifest: dict[str, Any]) -> tuple[bool, str]:
    """Archive old JSONL records and rewrite with only the within-retention tail."""

    if not jsonl.exists():
        return False, "no raw-audit file"
    cutoff = now.timestamp() - config.raw_audit_retention_days * 86400
    keep: list[str] = []
    drop: list[str] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            ts = rec.get("finished_at") or rec.get("started_at")
            dt = datetime.fromisoformat(ts) if ts else None
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            (drop if (dt and dt.timestamp() < cutoff) else keep).append(line)
        except Exception:  # noqa: BLE001 - unparseable line is kept (never silently lost)
            keep.append(line)
    if not drop:
        return False, "no records beyond retention (idempotent)"
    if config.keep_weekly_archives:
        week_dir = archive_dir / "raw_audit" / _iso_week_tag(now)
        week_dir.mkdir(parents=True, exist_ok=True)
        dest = week_dir / f"iterations_{now.date().isoformat()}.jsonl.gz"
        existing = b""
        if dest.exists():
            with gzip.open(dest, "rb") as fin:
                existing = fin.read()
        with gzip.open(dest, "wb") as fout:
            fout.write(existing + ("\n".join(drop) + "\n").encode("utf-8"))
    jsonl.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return True, f"rotated {len(drop)} old record(s), kept {len(keep)}"


def _save_report(report: MaintenanceReport, archive_dir: Path) -> dict[str, Path]:
    reports_dir = archive_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "").replace("-", "")[:15]
    base = reports_dir / f"{report.team_id}_{report.mode}_{stamp}"
    js = base.with_suffix(".json")
    md = base.with_suffix(".md")
    js.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    md.write_text(report.to_markdown(), encoding="utf-8")
    return {"json": js, "markdown": md}


__all__ = [
    "PRUNABLE", "CategoryInventory", "MemoryInventory", "inventory",
    "MaintenanceAction", "MaintenanceReport", "plan_maintenance", "run_maintenance",
]
