"""Per-team dynamic watchlist — a universe each team grows for itself.

The researcher may add or remove symbols any cycle. Additions must pass the
broker's asset-existence check (kills hallucinated tickers); the core symbols
from settings can never be removed; total size is capped so prompts stay
bounded. The file is the team's evolving market universe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_WATCHLIST = 30


@dataclass
class TeamWatchlist:
    team_id: str
    path: Path
    core: tuple[str, ...]                  # protected; from settings
    extra: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, team_id: str, data_dir: Path, core: tuple[str, ...]) -> "TeamWatchlist":
        path = Path(data_dir) / "watchlist" / f"{team_id}.json"
        wl = cls(team_id=team_id, path=path, core=tuple(s.upper() for s in core))
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                wl.extra = [
                    str(s).upper() for s in data.get("extra", [])
                    if str(s).strip() and str(s).upper() not in wl.core
                ][: MAX_WATCHLIST - len(wl.core)]
            except (json.JSONDecodeError, OSError):
                wl.extra = []
        return wl

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"team_id": self.team_id, "extra": self.extra}, indent=2),
            encoding="utf-8",
        )

    @property
    def symbols(self) -> list[str]:
        return list(self.core) + self.extra

    def apply_changes(
        self,
        additions: list[str] | None,
        removals: list[str] | None,
        asset_of: Callable[[str], object | None],
    ) -> dict[str, list[str]]:
        """Apply researcher watchlist edits. Returns what actually happened."""

        added: list[str] = []
        rejected: list[str] = []
        removed: list[str] = []

        for raw in removals or []:
            symbol = str(raw).strip().upper()
            if symbol in self.extra:
                self.extra.remove(symbol)
                removed.append(symbol)
            elif symbol in self.core:
                rejected.append(f"{symbol} (core symbol, cannot remove)")

        for raw in additions or []:
            symbol = str(raw).strip().upper()
            if not symbol or not symbol.isalnum() or len(symbol) > 6:
                rejected.append(f"{symbol or '?'} (invalid symbol)")
                continue
            if symbol in self.symbols:
                continue
            if len(self.symbols) >= MAX_WATCHLIST:
                rejected.append(f"{symbol} (watchlist full at {MAX_WATCHLIST})")
                continue
            asset = asset_of(symbol)
            if asset is None or not getattr(asset, "tradable", False):
                rejected.append(f"{symbol} (not found/tradable at broker)")
                continue
            self.extra.append(symbol)
            added.append(symbol)

        if added or removed:
            self.save()
        return {"added": added, "removed": removed, "rejected": rejected}
