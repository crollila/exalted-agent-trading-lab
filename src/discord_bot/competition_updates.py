"""Phase 7S — Discord team-thought updates per cheap-loop iteration.

Every cheap-competition-loop iteration can post a concise, readable "team room
briefing" to each team's Discord channel showing what Alpha and Beta did this
iteration, *why* the cheap gate decided what it decided, the current Portfolio
Manager stance, the latest thesis/learning, SPY-relative performance, broker
order outcomes, and a paper-only safety badge.

Safety properties (do not weaken):

* This module only *reads* local artifacts (scorecards, attribution, daily
  reviews, strategy memory, learning ledgers, kill switch) and the read-only
  routing model NAMES. It never submits orders, never bypasses risk, and never
  influences whether an order is placed.
* Discord failures (missing token/channel, rate limit, network down) NEVER crash
  the trading loop — every send fails closed with a concise warning.
* Secrets are never printed or posted. ``redact_secrets`` scrubs known env secret
  values and token-like strings before anything leaves the process.
* Token handling is reused from :mod:`src.discord_bot.bot` (same ``DISCORD_BOT_TOKEN``
  and channel env vars); this module never re-implements credential parsing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from src.discord_bot.bot import (
    SPECIAL_CHANNEL_ENVS,
    TEAM_CHANNEL_ENVS,
    TOKEN_ENV,
    _clean_optional,
    parse_optional_int,
)

DISCORD_API_BASE = "https://discord.com/api/v10"
DEFAULT_STATE_PATH = Path("data/discord/iteration_update_state.json")

# Sender signature: (channel_id, message, token) -> None. Raises on failure.
Sender = Callable[[int, str, str], None]

# --- env names (Phase 7S) ---------------------------------------------------
ENABLE_ENV = "ENABLE_DISCORD_ITERATION_UPDATES"
STYLE_ENV = "DISCORD_ITERATION_UPDATE_STYLE"
MAX_CHARS_ENV = "DISCORD_ITERATION_UPDATE_MAX_CHARS"
POST_WHEN_CLOSED_ENV = "DISCORD_POST_WHEN_MARKET_CLOSED"
POST_REVIEW_ONLY_ENV = "DISCORD_POST_REVIEW_ONLY"
POST_FULL_CYCLE_ENV = "DISCORD_POST_FULL_CYCLE"
POST_CHEAP_SKIP_ENV = "DISCORD_POST_CHEAP_SKIP"
POST_BROKER_EVENTS_ENV = "DISCORD_POST_BROKER_EVENTS"
POST_SCOREBOARD_SUMMARY_ENV = "DISCORD_POST_SCOREBOARD_SUMMARY"
MIN_INTERVAL_ENV = "DISCORD_UPDATE_MIN_INTERVAL_SECONDS"
POST_COMPETITION_SUMMARY_ENV = "DISCORD_POST_COMPETITION_SUMMARY"
COMPETITION_SUMMARY_CHANNEL_ENV = "DISCORD_COMPETITION_SUMMARY_CHANNEL"

# Cycle action identifiers produced by the loop.
ACTION_FULL_CYCLE = "full_cycle"
ACTION_REVIEW_ONLY = "review_only"
ACTION_CHEAP_SKIP = "cheap_skip"
ACTION_MARKET_CLOSED = "market_closed"

_ACTION_LABELS = {
    ACTION_FULL_CYCLE: "Full strategy cycle",
    ACTION_REVIEW_ONLY: "Review-only",
    ACTION_CHEAP_SKIP: "Cheap skip",
    ACTION_MARKET_CLOSED: "Market closed (cheap only)",
}

_TEAM_DISPLAY = {"team_alpha": "Team Alpha", "team_beta": "Team Beta"}


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _clean_optional(env.get(name))
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _clean_optional(env.get(name))
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class DiscordIterationUpdateConfig:
    enabled: bool = False
    style: str = "brief"
    max_chars: int = 1800
    post_when_market_closed: bool = False
    post_review_only: bool = True
    post_full_cycle: bool = True
    post_cheap_skip: bool = False
    post_broker_events: bool = True
    post_scoreboard_summary: bool = True
    min_interval_seconds: int = 300
    post_competition_summary: bool = True
    competition_summary_channel: str = "tournament_results"
    token: str | None = None
    team_channel_ids: Mapping[str, int] = None  # type: ignore[assignment]
    special_channel_ids: Mapping[str, int] = None  # type: ignore[assignment]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DiscordIterationUpdateConfig":
        env = env if env is not None else os.environ
        team_channel_ids: dict[str, int] = {}
        for team_id, env_name in TEAM_CHANNEL_ENVS.items():
            channel_id = parse_optional_int(env.get(env_name), env_name)
            if channel_id is not None:
                team_channel_ids[team_id] = channel_id
        special_channel_ids: dict[str, int] = {}
        for name, env_name in SPECIAL_CHANNEL_ENVS.items():
            channel_id = parse_optional_int(env.get(env_name), env_name)
            if channel_id is not None:
                special_channel_ids[name] = channel_id
        summary_channel = (_clean_optional(env.get(COMPETITION_SUMMARY_CHANNEL_ENV)) or "tournament_results").lower()
        if summary_channel not in SPECIAL_CHANNEL_ENVS:
            summary_channel = "tournament_results"
        return cls(
            enabled=_bool_env(env, ENABLE_ENV, False),
            style=(_clean_optional(env.get(STYLE_ENV)) or "brief").lower(),
            max_chars=_int_env(env, MAX_CHARS_ENV, 1800) or 1800,
            post_when_market_closed=_bool_env(env, POST_WHEN_CLOSED_ENV, False),
            post_review_only=_bool_env(env, POST_REVIEW_ONLY_ENV, True),
            post_full_cycle=_bool_env(env, POST_FULL_CYCLE_ENV, True),
            post_cheap_skip=_bool_env(env, POST_CHEAP_SKIP_ENV, False),
            post_broker_events=_bool_env(env, POST_BROKER_EVENTS_ENV, True),
            post_scoreboard_summary=_bool_env(env, POST_SCOREBOARD_SUMMARY_ENV, True),
            min_interval_seconds=_int_env(env, MIN_INTERVAL_ENV, 300),
            post_competition_summary=_bool_env(env, POST_COMPETITION_SUMMARY_ENV, True),
            competition_summary_channel=summary_channel,
            token=_clean_optional(env.get(TOKEN_ENV)),
            team_channel_ids=team_channel_ids,
            special_channel_ids=special_channel_ids,
        )

    def channel_for_team(self, team_id: str) -> int | None:
        return (self.team_channel_ids or {}).get(team_id)

    def summary_channel_id(self) -> int | None:
        return (self.special_channel_ids or {}).get(self.competition_summary_channel)


# --- secret redaction -------------------------------------------------------

# Discord bot tokens look like ``<id>.<ts>.<hmac>``; OpenAI keys start ``sk-``.
_DISCORD_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}\b")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_BEARER_RE = re.compile(r"\b(?:Bot|Bearer)\s+[A-Za-z0-9_.\-]{12,}", flags=re.IGNORECASE)
_REDACTED = "[REDACTED]"


def redact_secrets(text: str, *, env: Mapping[str, str] | None = None) -> str:
    """Scrub known secret env values and token-like strings from ``text``.

    Conservative on purpose: redacts exact secret env values (DISCORD_BOT_TOKEN
    and any *_API_KEY / *_SECRET / *_TOKEN env value of reasonable length) plus
    Discord/OpenAI/bearer token shapes. Never raises.
    """

    if not text:
        return text
    env = env if env is not None else os.environ
    redacted = text
    try:
        for name, value in env.items():
            if not value or len(value) < 8:
                continue
            upper = name.upper()
            if upper == TOKEN_ENV or upper.endswith(("_API_KEY", "_SECRET", "_TOKEN", "API_KEY", "SECRET_KEY")):
                redacted = redacted.replace(value, _REDACTED)
    except Exception:  # noqa: BLE001 - redaction must never crash a status update
        pass
    redacted = _BEARER_RE.sub(_REDACTED, redacted)
    redacted = _DISCORD_TOKEN_RE.sub(_REDACTED, redacted)
    redacted = _OPENAI_KEY_RE.sub(_REDACTED, redacted)
    return redacted


def truncate_discord_message(message: str, limit: int = 1800) -> str:
    """Keep a message safely under the Discord 2000-char limit."""

    limit = max(1, int(limit))
    if len(message) <= limit:
        return message
    marker = "\n...(truncated)"
    keep = max(0, limit - len(marker))
    return f"{message[:keep].rstrip()}{marker}"


# --- iteration-update state (min-interval + UI last-update/last-error) -------


def _state_path(state_path: Path | str | None = None) -> Path:
    return Path(state_path) if state_path is not None else DEFAULT_STATE_PATH


def load_update_state(state_path: Path | str | None = None) -> dict[str, Any]:
    path = _state_path(state_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict[str, Any], state_path: Path | str | None = None) -> None:
    path = _state_path(state_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # UI/state persistence is best-effort; never crash the loop


def _record_event(
    key: str,
    *,
    posted: bool,
    error: str | None,
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    state = load_update_state(state_path)
    entry = state.get(key, {}) if isinstance(state.get(key), dict) else {}
    if posted:
        entry["last_post_at"] = now.isoformat()
        entry["last_error"] = None
    if error is not None:
        entry["last_error"] = redact_secrets(error)
        entry["last_error_at"] = now.isoformat()
    state[key] = entry
    _write_state(state, state_path)


def min_interval_ok(
    key: str,
    config: DiscordIterationUpdateConfig,
    *,
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> bool:
    """True when enough time has elapsed since the last successful post for ``key``."""

    if config.min_interval_seconds <= 0:
        return True
    state = load_update_state(state_path)
    entry = state.get(key)
    if not isinstance(entry, dict):
        return True
    last = entry.get("last_post_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - last_dt).total_seconds() >= config.min_interval_seconds


# --- decide whether to post -------------------------------------------------


def should_post_for_action(
    config: DiscordIterationUpdateConfig,
    *,
    cycle_action: str,
    market_state: str,
) -> tuple[bool, str]:
    """Apply enable + market-closed + per-action posting rules. (No interval here.)"""

    if not config.enabled:
        return False, "iteration updates disabled"
    if market_state == "closed" and not config.post_when_market_closed:
        return False, "market closed; posting disabled (DISCORD_POST_WHEN_MARKET_CLOSED=false)"
    if cycle_action == ACTION_FULL_CYCLE and not config.post_full_cycle:
        return False, "full-cycle posting disabled"
    if cycle_action == ACTION_REVIEW_ONLY and not config.post_review_only:
        return False, "review-only posting disabled"
    if cycle_action in (ACTION_CHEAP_SKIP, ACTION_MARKET_CLOSED) and not config.post_cheap_skip:
        return False, "cheap-skip posting disabled"
    return True, "ok"


# --- gather local context (read-only; degrades to n/a) ----------------------

_NA = "n/a"


def _join(items: Any, limit: int = 4) -> str:
    if not items:
        return _NA
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return _NA
    head = cleaned[:limit]
    suffix = f" (+{len(cleaned) - limit} more)" if len(cleaned) > limit else ""
    return ", ".join(head) + suffix


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+.4f}"
    except (TypeError, ValueError):
        return _NA


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return _NA


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - missing/old artifacts must never crash a status update
        return default


def gather_team_iteration_context(
    team_id: str,
    *,
    iteration: int | None = None,
    cycle_action: str = ACTION_CHEAP_SKIP,
    gate_decision: Any = None,
    market_state: str = "unknown",
    kill_switch_engaged: bool = False,
    llm_model_used: str | None = None,
) -> dict[str, Any]:
    """Read local artifacts into a compact, secret-free context dict for the brief."""

    from src.competition.attribution import performance_feedback
    from src.competition.daily_review import load_daily_spy_attribution, load_latest_daily_team_review
    from src.competition.scorecard import load_latest_scorecard
    from src.learning.strategy_memory import StrategyMemory
    from src.learning.team_memory import TeamLearningLedger

    scorecard = _safe(lambda: load_latest_scorecard(team_id))
    feedback = _safe(lambda: performance_feedback(team_id), default={}) or {}
    outcome = feedback.get("outcome_feedback", {}) if isinstance(feedback, dict) else {}
    review = _safe(lambda: load_latest_daily_team_review(team_id))
    memory = _safe(lambda: StrategyMemory.load(team_id))
    ledger = _safe(lambda: TeamLearningLedger.load(team_id))
    attribution = _safe(lambda: load_daily_spy_attribution(team_id))

    ctx: dict[str, Any] = {
        "team_id": team_id,
        "iteration": iteration,
        "cycle_action": cycle_action,
        "market_state": market_state,
        "kill_switch_engaged": bool(kill_switch_engaged),
        "llm_model_used": llm_model_used,
    }

    # Gate (why this decision was made).
    if gate_decision is not None:
        ctx["gate_reason"] = getattr(gate_decision, "reason", None)
        ctx["gate_recommend_review_only"] = getattr(gate_decision, "recommend_review_only", None)
        ctx["mode"] = next(
            (flag.split(":", 1)[1] for flag in getattr(gate_decision, "trigger_flags", []) if str(flag).startswith("mode:")),
            None,
        )

    # Portfolio Manager (from the persisted scorecard).
    if scorecard is not None:
        ctx["pm_decision"] = getattr(scorecard, "portfolio_decision_type", None)
        ctx["pm_no_trade"] = getattr(scorecard, "portfolio_no_trade", None)
        ctx["pm_max_new"] = getattr(scorecard, "max_new_proposals", None)
        ctx["team_return"] = getattr(scorecard, "team_return", None)
        ctx["spy_return"] = getattr(scorecard, "spy_benchmark_return", None)
        ctx["excess_return"] = getattr(scorecard, "excess_return_vs_spy", None)
        ctx["equity"] = getattr(scorecard, "current_equity", None)
        ctx["proposals_count"] = getattr(scorecard, "proposals_count", None)
        ctx["approved_count"] = getattr(scorecard, "approved_count", None)
        ctx["rejected_count"] = getattr(scorecard, "rejected_count", None)
        ctx["simulation_only_count"] = getattr(scorecard, "simulation_only_count", None)
        ctx["orders_submitted"] = getattr(scorecard, "orders_submitted", None)
        ctx["broker_rejected_count"] = getattr(scorecard, "broker_rejected_count", None)
        if ctx.get("mode") is None:
            # PM mode is not on the scorecard; fall back to the ledger below.
            pass

    # Ledger (hypothesis / watchlist / avoid).
    if ledger is not None:
        ctx.setdefault("mode", None)
        if not ctx.get("mode"):
            ctx["mode"] = getattr(ledger, "mode", None) or None
        ctx["hypothesis"] = getattr(ledger, "current_hypothesis", None) or None
        ctx["watchlist"] = list(getattr(ledger, "watchlist", []) or [])
        ctx["avoid_next_cycle"] = list(getattr(ledger, "avoid_next_cycle", []) or [])

    # Daily review (the team's own thinking).
    if review is not None:
        ctx["spy_relative_result"] = getattr(review, "spy_relative_result", None) or None
        ctx["why_vs_spy"] = getattr(review, "why_vs_spy", None) or None
        ctx["keep_doing"] = list(getattr(review, "keep_doing", []) or [])
        ctx["stop_doing"] = list(getattr(review, "stop_doing", []) or [])
        ctx["test_next"] = list(getattr(review, "test_next", []) or [])
        ctx["prior_thesis_outcome"] = getattr(review, "prior_thesis_outcome", None) or None
        ctx["helped"] = list(getattr(review, "helped", []) or [])
        ctx["hurt"] = list(getattr(review, "hurt", []) or [])

    # Strategy memory (what changed / recurring patterns).
    if memory is not None:
        ctx["recurring_winners"] = list(getattr(memory, "recurring_winning_patterns", []) or [])
        ctx["recurring_losers"] = list(getattr(memory, "recurring_losing_patterns", []) or [])
        ctx["compact_summary"] = getattr(memory, "compact_summary", None) or None
        if not ctx.get("mode"):
            ctx["mode"] = getattr(memory, "recommended_mode", None) or None

    # Attribution outcomes (worked / failed / mixed).
    if isinstance(outcome, dict) and outcome:
        ctx["worked_count"] = outcome.get("worked_count")
        ctx["failed_count"] = outcome.get("failed_count")
        ctx["mixed_count"] = outcome.get("mixed_count")
        ctx["avg_excess_return_vs_spy"] = outcome.get("avg_excess_return_vs_spy")
        ctx["recent_broker_rejections"] = outcome.get("recent_broker_rejections", []) or []

    # Strongest / weakest holdings from daily attribution.
    if attribution is not None:
        winners = getattr(attribution, "top_winners", []) or []
        losers = getattr(attribution, "top_losers", []) or []
        ctx["strongest_symbol"] = winners[0]["symbol"] if winners else None
        ctx["weakest_symbol"] = losers[0]["symbol"] if losers else None
        if ctx.get("orders_submitted") is None:
            ctx["orders_submitted"] = getattr(attribution, "submitted_orders", None)

    return ctx


# --- message builders -------------------------------------------------------


def _team_display(team_id: str) -> str:
    return _TEAM_DISPLAY.get(team_id, team_id)


def build_team_iteration_update(team_id: str, iteration_context: Mapping[str, Any]) -> str:
    """Build a compact "team room briefing" from a gathered context dict."""

    ctx = dict(iteration_context or {})
    iteration = ctx.get("iteration")
    header = f"{_team_display(team_id)} - Iteration Brief"
    if iteration is not None:
        header += f" (iteration {iteration})"

    action = ctx.get("cycle_action", ACTION_CHEAP_SKIP)
    market = ctx.get("market_state", "unknown")
    ks = "ENGAGED (orders blocked)" if ctx.get("kill_switch_engaged") else "off"

    strongest = ctx.get("hypothesis") or (
        f"top winner {ctx['strongest_symbol']}" if ctx.get("strongest_symbol") else None
    )

    lines = [
        header,
        f"Mode: {ctx.get('mode') or _NA}",
        f"Cycle decision: {_ACTION_LABELS.get(action, action)}",
        f"Why: {ctx.get('gate_reason') or _NA}",
        f"Portfolio Manager: {ctx.get('pm_decision') or _NA} "
        f"(no_trade={ctx.get('pm_no_trade')}, max_new={ctx.get('pm_max_new')})",
        f"Market: {market} | Kill switch: {ks}",
        "",
        "Current thinking:",
        f"- vs SPY: {ctx.get('spy_relative_result') or _pct(ctx.get('excess_return'))} "
        f"(team {_pct(ctx.get('team_return'))} vs SPY {_pct(ctx.get('spy_return'))})",
        f"- Why vs SPY: {ctx.get('why_vs_spy') or _NA}",
        f"- Strongest thesis: {strongest or _NA}",
        f"- Weakest holding: {ctx.get('weakest_symbol') or _NA}",
        f"- What changed: {ctx.get('compact_summary') or _join(ctx.get('keep_doing'))}",
        f"- Watching next: {_join((ctx.get('test_next') or []) + (ctx.get('watchlist') or []))}",
        "",
        "Actions:",
        f"- Proposals: {ctx.get('proposals_count') if ctx.get('proposals_count') is not None else _NA} "
        f"(approved {ctx.get('approved_count') if ctx.get('approved_count') is not None else _NA}, "
        f"rejected {ctx.get('rejected_count') if ctx.get('rejected_count') is not None else _NA})",
        f"- Simulation-only: {ctx.get('simulation_only_count') if ctx.get('simulation_only_count') is not None else _NA}",
        f"- Submitted paper orders: {ctx.get('orders_submitted') if ctx.get('orders_submitted') is not None else _NA}",
        f"- Broker rejected: {ctx.get('broker_rejected_count') if ctx.get('broker_rejected_count') is not None else _NA}",
        f"- Blocked by kill switch: {'yes' if ctx.get('kill_switch_engaged') else 'no'}",
        "",
        "Learning:",
        f"- Worked: {ctx.get('worked_count') if ctx.get('worked_count') is not None else _NA}"
        + (f" ({_join(ctx.get('keep_doing'))})" if ctx.get("keep_doing") else ""),
        f"- Failed: {ctx.get('failed_count') if ctx.get('failed_count') is not None else _NA}"
        + (f" ({_join(ctx.get('stop_doing'))})" if ctx.get("stop_doing") else ""),
        f"- Mixed: {ctx.get('mixed_count') if ctx.get('mixed_count') is not None else _NA}",
        f"- Avoid next cycle: {_join(ctx.get('avoid_next_cycle'))}",
        "",
        f"Model: {ctx.get('llm_model_used') or _NA}",
        "Safety: Paper-only. LLMs do not execute trades. Orders require deterministic gates.",
    ]
    return "\n".join(lines)


def build_competition_iteration_summary(
    *,
    teams: tuple[str, ...] = ("team_alpha", "team_beta"),
    kill_switch_engaged: bool = False,
    next_wake_seconds: int | None = None,
) -> str:
    """Build a compact Alpha-vs-Beta scoreboard summary from local scorecards."""

    from src.competition.scorecard import load_latest_scorecard

    cards = {team_id: _safe(lambda team_id=team_id: load_latest_scorecard(team_id)) for team_id in teams}

    def _excess(team_id: str) -> float | None:
        card = cards.get(team_id)
        return getattr(card, "excess_return_vs_spy", None) if card is not None else None

    ranked = [t for t in teams if cards.get(t) is not None]
    ranked.sort(key=lambda t: (_excess(t) if _excess(t) is not None else float("-inf")), reverse=True)
    leader = ranked[0] if ranked else None

    lines = ["Alpha vs Beta - Scoreboard (paper-only)"]
    if leader is None:
        lines.append("Leader: n/a (no scorecards yet)")
    else:
        lines.append(f"Leader: {_team_display(leader)} (excessVsSPY {_pct(_excess(leader))})")
    spy_return = None
    for team_id in teams:
        card = cards.get(team_id)
        if card is None:
            lines.append(f"{_team_display(team_id)}: n/a")
            continue
        spy_return = getattr(card, "spy_benchmark_return", None)
        lines.append(
            f"{_team_display(team_id)}: equity {_money(getattr(card, 'current_equity', None))}, "
            f"return {_pct(getattr(card, 'team_return', None))}, "
            f"excessVsSPY {_pct(getattr(card, 'excess_return_vs_spy', None))}"
        )
    lines.append(f"SPY return: {_pct(spy_return)}")
    lines.append(f"Kill switch: {'ENGAGED' if kill_switch_engaged else 'off'} | Paper-only")
    if next_wake_seconds is not None:
        lines.append(f"Next scheduled wake: ~{int(next_wake_seconds)}s")
    return "\n".join(lines)


# --- sending (REST via bot token; never crashes the loop) -------------------


def _http_send(channel_id: int, message: str, token: str) -> None:
    """Default sender: POST to the Discord REST API using the bot token."""

    import requests

    response = requests.post(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json={"content": message},
        timeout=10,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Discord API returned HTTP {response.status_code}")


def _send(
    *,
    key: str,
    channel_id: int | None,
    message: str,
    config: DiscordIterationUpdateConfig,
    sender: Sender | None,
    state_path: Path | str | None,
    now: datetime | None,
) -> dict[str, Any]:
    """Redact + truncate + send. Never raises; records state for the UI."""

    safe_message = truncate_discord_message(redact_secrets(message), config.max_chars)
    if not config.token:
        warning = f"Discord update skipped for {key}: {TOKEN_ENV} not configured."
        print(warning)
        return {"sent": False, "reason": "no_token", "message": safe_message}
    if channel_id is None:
        warning = f"Discord update skipped for {key}: no channel configured."
        print(warning)
        return {"sent": False, "reason": "no_channel", "message": safe_message}

    send_fn = sender or _http_send
    try:
        send_fn(channel_id, safe_message, config.token)
    except Exception as exc:  # noqa: BLE001 - Discord must never crash the trading loop
        warning = redact_secrets(f"Discord update failed for {key}: {exc}")
        print(f"(Discord warning) {warning}; continuing loop.")
        _record_event(key, posted=False, error=str(exc), state_path=state_path, now=now)
        return {"sent": False, "reason": "send_failed", "error": warning, "message": safe_message}

    _record_event(key, posted=True, error=None, state_path=state_path, now=now)
    return {"sent": True, "reason": "ok", "message": safe_message}


def send_team_iteration_update(
    team_id: str,
    message: str,
    *,
    config: DiscordIterationUpdateConfig | None = None,
    sender: Sender | None = None,
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send a prebuilt team brief to that team's channel. Never raises."""

    config = config or DiscordIterationUpdateConfig.from_env()
    return _send(
        key=team_id,
        channel_id=config.channel_for_team(team_id),
        message=message,
        config=config,
        sender=sender,
        state_path=state_path,
        now=now,
    )


def send_competition_iteration_summary(
    message: str,
    *,
    config: DiscordIterationUpdateConfig | None = None,
    sender: Sender | None = None,
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send a prebuilt scoreboard summary to the configured summary channel."""

    config = config or DiscordIterationUpdateConfig.from_env()
    return _send(
        key=f"summary:{config.competition_summary_channel}",
        channel_id=config.summary_channel_id(),
        message=message,
        config=config,
        sender=sender,
        state_path=state_path,
        now=now,
    )


# --- orchestrators used by the loop + CLI -----------------------------------


def post_team_iteration_update(
    team_id: str,
    *,
    iteration: int | None = None,
    cycle_action: str = ACTION_CHEAP_SKIP,
    gate_decision: Any = None,
    market_state: str = "unknown",
    kill_switch_engaged: bool = False,
    llm_model_used: str | None = None,
    config: DiscordIterationUpdateConfig | None = None,
    sender: Sender | None = None,
    dry_run: bool = False,
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide, build, and (unless dry-run) send a team's iteration brief.

    Returns a small result dict. Never raises — Discord problems only warn.
    """

    config = config or DiscordIterationUpdateConfig.from_env()
    should, reason = should_post_for_action(config, cycle_action=cycle_action, market_state=market_state)

    # Dry-run is always a preview (even when posting rules would skip the send),
    # so operators can inspect the brief without a token/channel/enable flag.
    if dry_run:
        ctx = gather_team_iteration_context(
            team_id,
            iteration=iteration,
            cycle_action=cycle_action,
            gate_decision=gate_decision,
            market_state=market_state,
            kill_switch_engaged=kill_switch_engaged,
            llm_model_used=llm_model_used,
        )
        message = build_team_iteration_update(team_id, ctx)
        would = "would post" if should else f"would NOT post ({reason})"
        print(f"[dry-run] {would} Discord iteration update to {team_id}:")
        print(redact_secrets(truncate_discord_message(message, config.max_chars)))
        return {"sent": False, "reason": "dry_run", "would_post": should, "message": message}

    if not should:
        return {"sent": False, "reason": reason, "message": None}

    ctx = gather_team_iteration_context(
        team_id,
        iteration=iteration,
        cycle_action=cycle_action,
        gate_decision=gate_decision,
        market_state=market_state,
        kill_switch_engaged=kill_switch_engaged,
        llm_model_used=llm_model_used,
    )
    message = build_team_iteration_update(team_id, ctx)

    if not min_interval_ok(team_id, config, state_path=state_path, now=now):
        return {
            "sent": False,
            "reason": f"min interval not elapsed ({config.min_interval_seconds}s)",
            "message": message,
        }

    return send_team_iteration_update(
        team_id, message, config=config, sender=sender, state_path=state_path, now=now
    )


def post_competition_iteration_summary(
    *,
    config: DiscordIterationUpdateConfig | None = None,
    sender: Sender | None = None,
    dry_run: bool = False,
    kill_switch_engaged: bool = False,
    next_wake_seconds: int | None = None,
    teams: tuple[str, ...] = ("team_alpha", "team_beta"),
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build + (unless dry-run) post the Alpha-vs-Beta scoreboard summary."""

    config = config or DiscordIterationUpdateConfig.from_env()
    enabled = config.enabled and config.post_competition_summary
    message = build_competition_iteration_summary(
        teams=teams, kill_switch_engaged=kill_switch_engaged, next_wake_seconds=next_wake_seconds
    )
    if dry_run:
        would = "would post" if enabled else "would NOT post (competition summary disabled)"
        print(f"[dry-run] {would} Discord competition summary:")
        print(redact_secrets(truncate_discord_message(message, config.max_chars)))
        return {"sent": False, "reason": "dry_run", "would_post": enabled, "message": message}

    if not enabled:
        return {"sent": False, "reason": "competition summary disabled", "message": None}

    return send_competition_iteration_summary(
        message, config=config, sender=sender, state_path=state_path, now=now
    )


# --- UI / status ------------------------------------------------------------


def iteration_updates_status(
    config: DiscordIterationUpdateConfig | None = None,
    *,
    state_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compact, secret-free status for the operator UI. Channel IDs are not exposed."""

    config = config or DiscordIterationUpdateConfig.from_env()
    state = load_update_state(state_path)
    teams: dict[str, Any] = {}
    for team_id in TEAM_CHANNEL_ENVS:
        entry = state.get(team_id, {}) if isinstance(state.get(team_id), dict) else {}
        teams[team_id] = {
            "channel_configured": config.channel_for_team(team_id) is not None,
            "last_update_at": entry.get("last_post_at"),
            "last_error": redact_secrets(entry.get("last_error")) if entry.get("last_error") else None,
        }
    return {
        "enabled": config.enabled,
        "token_configured": config.token is not None,
        "style": config.style,
        "max_chars": config.max_chars,
        "post_when_market_closed": config.post_when_market_closed,
        "post_full_cycle": config.post_full_cycle,
        "post_review_only": config.post_review_only,
        "post_cheap_skip": config.post_cheap_skip,
        "post_competition_summary": config.post_competition_summary,
        "min_interval_seconds": config.min_interval_seconds,
        "summary_channel": config.competition_summary_channel,
        "summary_channel_configured": config.summary_channel_id() is not None,
        "teams": teams,
    }


__all__ = [
    "ACTION_CHEAP_SKIP",
    "ACTION_FULL_CYCLE",
    "ACTION_MARKET_CLOSED",
    "ACTION_REVIEW_ONLY",
    "DiscordIterationUpdateConfig",
    "build_competition_iteration_summary",
    "build_team_iteration_update",
    "gather_team_iteration_context",
    "iteration_updates_status",
    "min_interval_ok",
    "post_competition_iteration_summary",
    "post_team_iteration_update",
    "redact_secrets",
    "send_competition_iteration_summary",
    "send_team_iteration_update",
    "should_post_for_action",
    "truncate_discord_message",
]
