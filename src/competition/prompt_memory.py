"""Bounded prompt-memory assembly + metadata recording (Phase 7X).

Wires the deterministic bounded-memory retrieval layer into the live LLM proposal
prompt and records *metadata only* (never raw prompt contents or secrets) so the
iteration audit can show what memory the prompt actually used.

Bounded prompt memory contains only: refreshed working memory, current positions +
active theses, account/risk/cap constraints, the most recent configured daily
summaries, the top configured active playbook lessons, and the latest scorecard
snapshot. It excludes raw audit JSONL, unbounded agent-response history, and old
chat history by construction.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.competition.memory_config import MemoryConfig
from src.competition.memory_retrieval import build_bounded_context, load_recent_daily_summaries
from src.competition.playbook import TeamPlaybook, lesson_id_for
from src.competition.position_review import build_team_portfolio_review
from src.config.permissions import TradingPermissions
from src.config.portfolio_limits import PortfolioLimits

DEFAULT_PROMPT_META_DIR = Path("data/runtime/prompt_memory")


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def build_bounded_prompt_memory(
    team_id: str,
    *,
    account: dict[str, Any] | None,
    raw_positions: list[Any],
    attribution_entries: list[Any],
    market_session: Any,
    scorecard_snapshot: dict[str, Any] | None,
    config: MemoryConfig | None = None,
    playbook: TeamPlaybook | None = None,
    recent_daily: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (bounded_memory_block, metadata). Pure + deterministic.

    ``account`` is the ``{equity,cash,buying_power}`` snapshot (strings ok).
    ``raw_positions`` are refreshed broker positions. Everything is bounded by
    :class:`MemoryConfig`. Metadata never includes raw prompt text or secrets.
    """

    now = now or datetime.now(timezone.utc)
    config = config or MemoryConfig.from_env()
    playbook = playbook if playbook is not None else TeamPlaybook.load(team_id)
    malformed: list[str] = []

    equity = _f((account or {}).get("equity"))
    cash = _f((account or {}).get("cash"))
    bp = _f((account or {}).get("buying_power"))

    # Current positions + active theses (deterministic review; long-only mgmt).
    try:
        review = build_team_portfolio_review(
            team_id, equity=equity, cash=cash, buying_power=bp,
            raw_positions=raw_positions, attribution_entries=attribution_entries,
            limits=PortfolioLimits.from_env(), now=now,
        )
        positions_theses = [
            {
                "symbol": p.symbol, "side": p.side, "qty": p.quantity,
                "unrealized_pl_pct": p.unrealized_pl_pct, "weight": p.portfolio_weight,
                "thesis_status": p.thesis_status, "recommended_action": p.recommended_action,
                "thesis": (p.original_thesis or "")[:200],
            }
            for p in review.positions
        ]
        health = {
            "block_new_buys": review.health.block_new_buys,
            "block_new_buys_reason": review.health.block_new_buys_reason,
            "negative_cash": review.health.negative_cash,
            "zero_buying_power": review.health.zero_buying_power,
            "concentration_alerts": review.health.concentration_alerts,
        }
    except Exception as exc:  # noqa: BLE001 - never let memory assembly crash the cycle
        malformed.append(f"position_review:{type(exc).__name__}")
        positions_theses, health = [], {}

    perms = TradingPermissions.from_env()
    constraints = {
        "permissions": perms.summary().get("caps", {}),
        "portfolio_limits": PortfolioLimits.from_env().summary(),
        "stocks_enabled": perms.stocks_enabled(),
        "sell_to_close_enabled": PortfolioLimits.from_env().enable_paper_sell_to_close,
    }

    working_memory = {
        "account": {"equity": equity, "cash": cash, "buying_power": bp},
        "market_session": market_session,
        "positions_and_theses": positions_theses,
        "portfolio_health": health,
    }

    if recent_daily is None:
        try:
            recent_daily = load_recent_daily_summaries(
                team_id, max_n=config.max_daily_summaries_in_prompt
            )
        except Exception as exc:  # noqa: BLE001
            malformed.append(f"daily_summaries:{type(exc).__name__}")
            recent_daily = []

    relevance_symbols = [p["symbol"] for p in positions_theses] or None
    block = build_bounded_context(
        team_id, working_memory=working_memory, playbook=playbook,
        recent_daily=recent_daily, scorecard_snapshot=scorecard_snapshot,
        constraints=constraints, config=config,
        relevance_symbols=relevance_symbols, now=now,
    )

    metadata = {
        "daily_summaries_included": [d.get("trading_date") for d in block["recent_daily_summaries"]],
        "lesson_ids_included": [
            lesson_id_for(l["category"], l["text"]) for l in block["playbook_lessons"]
        ],
        "scorecard_included": scorecard_snapshot is not None,
        "bounded_context_chars": len(json.dumps(block, default=str)),
        "malformed_or_unavailable": malformed,
    }
    return block, metadata


def record_prompt_memory_metadata(
    team_id: str, metadata: dict[str, Any], *, meta_dir: Path | str = DEFAULT_PROMPT_META_DIR,
    now: datetime | None = None,
) -> Path | None:
    """Persist the latest prompt-memory metadata for the iteration audit. Best-effort."""

    now = now or datetime.now(timezone.utc)
    try:
        directory = Path(meta_dir)
        directory.mkdir(parents=True, exist_ok=True)
        payload = dict(metadata)
        payload["recorded_at"] = now.isoformat()
        path = directory / f"{team_id}_prompt_meta.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001 - metadata recording must never crash the cycle
        print(f"(prompt-memory metadata record failed for {team_id}: {exc})")
        return None


def load_prompt_memory_metadata(
    team_id: str, *, meta_dir: Path | str = DEFAULT_PROMPT_META_DIR,
) -> dict[str, Any] | None:
    path = Path(meta_dir) / f"{team_id}_prompt_meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "DEFAULT_PROMPT_META_DIR",
    "build_bounded_prompt_memory",
    "record_prompt_memory_metadata",
    "load_prompt_memory_metadata",
]
