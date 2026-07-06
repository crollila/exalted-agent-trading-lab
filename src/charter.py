"""Team charter: each team's SELF-CHOSEN trading parameters.

The teams decide how aggressive to be — position sizing, gross exposure (margin),
cycle speed, which instruments to use, and a free-text style statement — and the
strategist may change any of it on any cycle by returning ``charter_updates``.

Every value is clamped to the immutable platform caps in ``RiskLimits`` when
loaded and when updated, so a team can turn itself up or down but can never
climb out of the sandbox. Changes are journaled (with the team's stated reason)
and announced on Discord by the caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import RiskLimits
from src.market_time import now_utc

INSTRUMENTS = ("stocks", "shorts", "options", "margin")
MAX_HISTORY = 30

# Starting personalities. From here, each team evolves its own charter.
DEFAULT_CHARTERS = {
    "team_alpha": {
        "style": (
            "Aggressive momentum and catalysts: press winners, rotate fast, use "
            "shorts and long options to express high-conviction moves."
        ),
        "max_position_pct": 0.15,
        "max_gross_exposure": 1.20,
        "cycle_minutes": 20,
        "instruments": ["stocks", "shorts", "options", "margin"],
    },
    "team_beta": {
        "style": (
            "Contrarian, risk-adjusted, low churn: quality entries, mean reversion, "
            "small defined-risk options, and capital preservation first."
        ),
        "max_position_pct": 0.10,
        "max_gross_exposure": 0.80,
        "cycle_minutes": 45,
        "instruments": ["stocks", "shorts", "options"],
    },
}

# Fields the strategist is allowed to change, with (min, platform-cap attr).
_NUMERIC_FIELDS = {
    "max_position_pct": (0.01, "max_position_pct", float),
    "max_gross_exposure": (0.10, "max_gross_exposure", float),
    "cycle_minutes": (None, None, int),  # clamped to limits.min/max_cycle_minutes
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class TeamCharter:
    team_id: str
    path: Path
    style: str = ""
    max_position_pct: float = 0.10
    max_gross_exposure: float = 1.0
    cycle_minutes: int = 30
    instruments: list[str] = field(default_factory=lambda: ["stocks"])
    updated_at: str = ""
    history: list[dict] = field(default_factory=list)

    # --- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, team_id: str, data_dir: Path, limits: RiskLimits) -> "TeamCharter":
        path = Path(data_dir) / "charter" / f"{team_id}.json"
        defaults = DEFAULT_CHARTERS.get(team_id, DEFAULT_CHARTERS["team_beta"])
        charter = cls(team_id=team_id, path=path, **{k: (list(v) if isinstance(v, list) else v) for k, v in defaults.items()})
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                charter.style = str(data.get("style", charter.style)).strip() or charter.style
                charter.max_position_pct = float(data.get("max_position_pct", charter.max_position_pct))
                charter.max_gross_exposure = float(data.get("max_gross_exposure", charter.max_gross_exposure))
                charter.cycle_minutes = int(data.get("cycle_minutes", charter.cycle_minutes))
                raw_instruments = data.get("instruments", charter.instruments)
                if isinstance(raw_instruments, list):
                    charter.instruments = [str(i) for i in raw_instruments]
                charter.updated_at = str(data.get("updated_at", ""))
                if isinstance(data.get("history"), list):
                    charter.history = data["history"][-MAX_HISTORY:]
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass  # corrupt file -> defaults; the clamp below still applies
        charter._enforce(limits)
        return charter

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "team_id": self.team_id,
                    "style": self.style,
                    "max_position_pct": self.max_position_pct,
                    "max_gross_exposure": self.max_gross_exposure,
                    "cycle_minutes": self.cycle_minutes,
                    "instruments": self.instruments,
                    "updated_at": self.updated_at,
                    "history": self.history[-MAX_HISTORY:],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # --- rules ---------------------------------------------------------------

    def _enforce(self, limits: RiskLimits) -> None:
        """Clamp everything to platform caps; drop platform-disabled instruments."""

        self.max_position_pct = _clamp(self.max_position_pct, 0.01, limits.max_position_pct)
        self.max_gross_exposure = _clamp(self.max_gross_exposure, 0.10, limits.max_gross_exposure)
        self.cycle_minutes = int(_clamp(self.cycle_minutes, limits.min_cycle_minutes, limits.max_cycle_minutes))
        allowed = {"stocks"}
        if limits.allow_shorts:
            allowed.add("shorts")
        if limits.allow_options:
            allowed.add("options")
        if limits.allow_margin:
            allowed.add("margin")
        cleaned = [i for i in self.instruments if i in INSTRUMENTS and i in allowed]
        if "stocks" not in cleaned:
            cleaned.insert(0, "stocks")
        self.instruments = cleaned

    def allows(self, instrument: str) -> bool:
        return instrument in self.instruments

    def apply_updates(self, updates: Any, limits: RiskLimits, reason: str) -> dict[str, Any]:
        """Apply a strategist's ``charter_updates`` dict. Returns {field: (old, new)}
        for what actually changed (post-clamping). Invalid input changes nothing."""

        if not isinstance(updates, dict):
            return {}
        before = {
            "style": self.style,
            "max_position_pct": self.max_position_pct,
            "max_gross_exposure": self.max_gross_exposure,
            "cycle_minutes": self.cycle_minutes,
            "instruments": list(self.instruments),
        }

        if isinstance(updates.get("style"), str) and updates["style"].strip():
            self.style = updates["style"].strip()[:400]
        for name, (_low, _cap, cast) in _NUMERIC_FIELDS.items():
            if name in updates:
                try:
                    setattr(self, name, cast(updates[name]))
                except (TypeError, ValueError):
                    pass
        if isinstance(updates.get("instruments"), list):
            self.instruments = [str(i).strip().lower() for i in updates["instruments"]]

        self._enforce(limits)

        changed: dict[str, Any] = {}
        for name, old in before.items():
            new = getattr(self, name)
            if new != old:
                changed[name] = (old, new)
        if changed:
            self.updated_at = now_utc().isoformat()
            self.history.append(
                {
                    "at": self.updated_at,
                    "reason": str(reason).strip()[:300],
                    "changes": {k: [v[0], v[1]] for k, v in changed.items()},
                }
            )
            self.history = self.history[-MAX_HISTORY:]
        return changed

    # --- prompt rendering -----------------------------------------------------

    def render(self, limits: RiskLimits) -> str:
        recent = self.history[-3:]
        lines = [
            f"Style (self-chosen, you may change it): {self.style}",
            f"Your current limits (self-chosen): max position {self.max_position_pct:.0%} of equity, "
            f"max gross exposure {self.max_gross_exposure:.0%}, cycle every {self.cycle_minutes} min.",
            f"Instruments you have enabled: {', '.join(self.instruments)}.",
            f"Platform hard caps (you can NEVER exceed these): position {limits.max_position_pct:.0%}, "
            f"gross {limits.max_gross_exposure:.0%}, {limits.max_orders_per_day} orders/day, "
            f"option premium {limits.max_option_premium_pct:.0%}/trade, cycle {limits.min_cycle_minutes}-"
            f"{limits.max_cycle_minutes} min. Long options only — selling/writing options is never allowed.",
        ]
        if recent:
            lines.append("Your recent charter changes:")
            for entry in recent:
                changes = ", ".join(f"{k}: {v[0]} -> {v[1]}" for k, v in entry.get("changes", {}).items())
                lines.append(f"- [{entry.get('at', '')[:10]}] {changes} ({entry.get('reason', '')})")
        return "\n".join(lines)
