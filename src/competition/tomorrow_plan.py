"""Tomorrow Plan artifact (Phase 7T).

One clean, deterministic artifact built *after* the daily team review that
answers the operator's standing questions in a single place:

* what worked / what failed today
* what to stop / keep doing
* what to test tomorrow
* a watchlist and an avoid list
* the recommended team mode (conservation / exploration / risk_reduction /
  hold_observe)
* explicit, plain-English tomorrow rules ("do not add shorts", "free buying
  power before new buys")
* risk / buying-power constraints and the PortfolioManager stance
* consistency + mixed-signal warnings when the inputs disagree

The builder is deterministic and never invents claims: missing inputs degrade
to ``"n/a"`` / ``"no update available"`` rather than raising. It summarizes only
— it submits no orders, and the deterministic risk engine, team credentials,
and the kill switch remain authoritative. LLMs do not execute orders.

Artifacts are persisted under the ignored runtime path ``data/reviews/``:

* ``{team}_tomorrow_plan_latest.json``
* ``{team}_tomorrow_plan_latest.md``
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.competition.attribution import DEFAULT_ATTRIBUTION_DIR, performance_feedback
from src.competition.daily_review import (
    DEFAULT_REVIEWS_DIR,
    DailySpyAttribution,
    bucket_for,
    export_daily_team_review,
    load_daily_spy_attribution,
    load_latest_daily_team_review,
)
from src.competition.scorecard import DEFAULT_SCORECARD_DIR, load_latest_scorecard
from src.learning.team_memory import DEFAULT_LEARNING_DIR, TeamLearningLedger

NA = "n/a"
NO_UPDATE = "no update available"

# Recommended modes.
MODE_CONSERVATION = "conservation"
MODE_EXPLORATION = "exploration"
MODE_RISK_REDUCTION = "risk_reduction"
MODE_HOLD_OBSERVE = "hold_observe"

_SAFER_MODES = {MODE_CONSERVATION, MODE_RISK_REDUCTION, MODE_HOLD_OBSERVE, "observe", "hold"}

SAFETY_REMINDER = (
    "Paper-only. LLMs summarize/propose only and do NOT execute orders. "
    "Deterministic risk gates and the kill switch remain authoritative."
)

CONSISTENCY_WARNING = (
    "Plan consistency warning: daily review and learning ledger disagree; "
    "default to safer conservation/risk-reduction stance."
)

_TEAM_DISPLAY = {"team_alpha": "Team Alpha", "team_beta": "Team Beta"}

# Tokens that look like a ticker: an already-uppercase run of 2-5 letters. We
# deliberately do NOT upper-case the text first, so ordinary words in free-text
# avoid entries (and sentence-leading capitals) are not mistaken for tickers.
_SYMBOL_RE = re.compile(r"\b[A-Z]{2,5}\b")


def _team_display(team_id: str) -> str:
    return _TEAM_DISPLAY.get(team_id, team_id)


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first present attribute/key from an object or mapping."""

    if obj is None:
        return default
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return default


def _clean_list(values: Any) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _norm_mode(value: Any) -> str:
    return str(value or "").strip().lower()


def _symbols_in(values: Any) -> set[str]:
    """Best-effort ticker tokens found in a list of strings (or one string)."""

    if not values:
        return set()
    if isinstance(values, str):
        items = [values]
    else:
        items = [str(v) for v in values]
    found: set[str] = set()
    for item in items:
        for token in _SYMBOL_RE.findall(item):
            found.add(token)
    return found


@dataclass
class TomorrowPlan:
    team_id: str
    generated_at: str
    source_date: str
    equity: str = NA
    equity_source: str = NA
    rank: str = NA
    recommended_mode: str = MODE_HOLD_OBSERVE
    executive_summary: str = NO_UPDATE
    what_worked_today: list[str] = field(default_factory=list)
    what_failed_today: list[str] = field(default_factory=list)
    stop_doing: list[str] = field(default_factory=list)
    keep_doing: list[str] = field(default_factory=list)
    test_tomorrow: list[str] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    avoid_list: list[str] = field(default_factory=list)
    risk_constraints: str = NA
    portfolio_manager_stance: str = NO_UPDATE
    tomorrow_rules: list[str] = field(default_factory=list)
    consistency_warning: str = ""
    mixed_signal_warning: str = ""
    safety_reminder: str = SAFETY_REMINDER

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_equity_and_rank(
    team_id: str,
    competition_status: Any,
    equity_view: Any,
) -> tuple[str, str, str]:
    """Return (equity, equity_source, rank) as display strings (never invents)."""

    equity_text = NA
    equity_source = NA
    rank_text = NA

    # Prefer a live/cached equity snapshot when one was provided.
    snap = None
    if equity_view is not None and hasattr(equity_view, "get"):
        try:
            snap = equity_view.get(team_id)
        except Exception:  # noqa: BLE001 - never let a bad view crash the plan
            snap = None
    if snap is not None and _get(snap, "equity") is not None:
        equity_text = f"{float(_get(snap, 'equity')):.2f}"
        equity_source = "live" if _get(snap, "is_live", default=False) else "cached"

    # Pull rank (and a cached-equity fallback) from competition status teams.
    teams = _get(competition_status, "teams", default=None)
    if isinstance(teams, list):
        for card in teams:
            if _get(card, "team_id") != team_id:
                continue
            rank = _get(card, "current_rank")
            total = len([c for c in teams if _get(c, "current_rank") is not None]) or len(teams)
            if rank is not None:
                rank_text = f"#{rank} of {total}"
            if equity_text == NA:
                cur = _get(card, "current_equity")
                if cur is not None:
                    equity_text = f"{float(cur):.2f}"
                    equity_source = "cached"
            break
    return equity_text, equity_source, rank_text


def _resolve_mode(
    *,
    review_mode: str,
    ledger_mode: str,
    has_attribution: bool,
    risk_signals: bool,
) -> tuple[str, bool]:
    """Resolve the recommended mode + whether a consistency warning is needed."""

    # Contradiction: daily review wants exploration but the ledger says play it
    # safe. We always emit the consistency warning and pick the safer stance.
    contradiction = review_mode == MODE_EXPLORATION and ledger_mode in _SAFER_MODES

    if not has_attribution and not review_mode and not ledger_mode:
        return MODE_HOLD_OBSERVE, False
    if contradiction:
        return (MODE_RISK_REDUCTION if risk_signals else MODE_CONSERVATION), True
    if risk_signals:
        return MODE_RISK_REDUCTION, False
    if review_mode in {MODE_EXPLORATION, MODE_CONSERVATION, MODE_RISK_REDUCTION, MODE_HOLD_OBSERVE}:
        return review_mode, False
    if ledger_mode == MODE_EXPLORATION:
        return MODE_EXPLORATION, False
    if ledger_mode in _SAFER_MODES:
        return MODE_CONSERVATION, False
    return MODE_HOLD_OBSERVE, False


def _build_risk_constraints(
    *,
    attribution: DailySpyAttribution | None,
    pm_state: Any,
) -> str:
    parts: list[str] = []
    low_bp = _get(pm_state, "low_buying_power", default=None)
    if low_bp is True:
        parts.append("LOW buying power — free buying power before new buys")
    bp_impact = _get(pm_state, "buying_power_impact")
    if bp_impact:
        parts.append(str(bp_impact))
    max_new = _get(pm_state, "max_new_proposals_this_cycle", "max_new_proposals")
    if max_new is not None:
        parts.append(f"max new proposals next cycle: {max_new}")
    allowed = _get(pm_state, "allowed_to_generate_new_orders", default=None)
    if allowed is False:
        parts.append("portfolio manager blocked new orders")
    risk_notes = _get(pm_state, "risk_notes")
    if risk_notes:
        parts.append(str(risk_notes))
    if attribution is not None and attribution.broker_rejections:
        cats = ", ".join(attribution.broker_rejection_categories) or "unknown"
        parts.append(f"{attribution.broker_rejections} broker rejection(s) ({cats})")
    return "; ".join(dict.fromkeys(p for p in parts if p)) or NA


def _build_pm_stance(pm_state: Any) -> str:
    if pm_state is None:
        return NO_UPDATE
    # A PortfolioDecision exposes a compact summary() helper.
    summary = getattr(pm_state, "summary", None)
    if callable(summary):
        try:
            return str(summary())
        except Exception:  # noqa: BLE001 - degrade safely
            pass
    decision_type = _get(pm_state, "decision_type", "portfolio_decision_type")
    mode = _get(pm_state, "mode")
    no_trade = _get(pm_state, "portfolio_no_trade", default=None)
    if decision_type is None and mode is None and no_trade is None:
        return NO_UPDATE
    bits = []
    if decision_type:
        bits.append(str(decision_type))
    if no_trade is not None:
        bits.append(f"no_trade={bool(no_trade)}")
    if mode:
        bits.append(f"mode={mode}")
    return " ".join(bits) or NO_UPDATE


def _build_tomorrow_rules(
    *,
    mode: str,
    attribution: DailySpyAttribution | None,
    pm_state: Any,
    shorts_hurt: bool,
) -> list[str]:
    rules: list[str] = []
    drivers = list(attribution.drivers) if attribution is not None else []
    low_bp = _get(pm_state, "low_buying_power", default=None) is True
    broker_rejections = attribution.broker_rejections if attribution is not None else 0

    if shorts_hurt or "short_exposure" in drivers:
        rules.append("Do not add shorts; existing short exposure lagged SPY.")
    if broker_rejections > 0 or low_bp:
        rules.append("Free buying power before new buys (close/trim first).")
    if "too_much_cash_no_trade" in drivers:
        rules.append("Deploy idle cash only when an idea beats the weakest holding.")
    for driver in drivers:
        if driver.startswith("sector_exposure:"):
            rules.append(f"Avoid overconcentrating in {driver.split(':', 1)[1]}.")
    if mode == MODE_RISK_REDUCTION:
        rules.append("Reduce gross exposure; trim the weakest holding before adding risk.")
    elif mode == MODE_HOLD_OBSERVE:
        rules.append("Hold and observe; place no new orders unless an idea clearly clears the bar.")
    elif mode == MODE_CONSERVATION:
        rules.append("Stay selective; only act when an idea beats the weakest current holding.")

    rules.append(
        "Deterministic risk gates and the kill switch remain authoritative; LLMs do not execute orders."
    )
    return list(dict.fromkeys(rules))


def build_tomorrow_plan(
    team_id: str,
    daily_review: Any = None,
    learning_status: Any = None,
    attribution: DailySpyAttribution | None = None,
    competition_status: Any = None,
    portfolio_manager_state: Any = None,
    *,
    equity_view: Any = None,
    generated_at: str | None = None,
    source_date: str | None = None,
) -> TomorrowPlan:
    """Build a deterministic Tomorrow Plan from artifacts the system already keeps.

    All inputs are optional; missing data degrades to ``n/a`` / ``no update
    available`` rather than raising. Never invents claims.
    """

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    source_date = (
        source_date
        or _get(daily_review, "date")
        or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )

    equity_text, equity_source, rank_text = _resolve_equity_and_rank(
        team_id, competition_status, equity_view
    )

    # Pull the daily-review answers (already deterministic, no secrets).
    helped = _clean_list(_get(daily_review, "helped"))
    hurt = _clean_list(_get(daily_review, "hurt"))
    stop_doing = _clean_list(_get(daily_review, "stop_doing"))
    keep_doing = _clean_list(_get(daily_review, "keep_doing"))
    test_next = _clean_list(_get(daily_review, "test_next"))
    review_watch = _clean_list(_get(daily_review, "watch_symbols"))
    spy_relative = _get(daily_review, "spy_relative_result", default="")

    ledger_watch = _clean_list(_get(learning_status, "watchlist"))
    avoid = _clean_list(_get(learning_status, "avoid_next_cycle")) + _clean_list(
        _get(learning_status, "rejected_ideas")
    )
    avoid = _clean_list(avoid)

    watchlist = _clean_list(review_watch + ledger_watch)

    # Mode resolution + contradiction detection.
    review_mode = _norm_mode(_get(daily_review, "recommended_mode"))
    ledger_mode = _norm_mode(_get(learning_status, "mode"))
    shorts_hurt = bool(
        attribution is not None
        and attribution.short_contribution_est is not None
        and attribution.short_contribution_est < 0
    )
    broker_rejections = attribution.broker_rejections if attribution is not None else 0
    low_bp = _get(portfolio_manager_state, "low_buying_power", default=None) is True
    risk_signals = bool(broker_rejections > 0 or low_bp or shorts_hurt)
    has_attribution = bool(
        attribution is not None
        and (
            attribution.excess_return is not None
            or attribution.top_winners
            or attribution.top_losers
            or attribution.submitted_orders
        )
    )
    recommended_mode, needs_consistency_warning = _resolve_mode(
        review_mode=review_mode,
        ledger_mode=ledger_mode,
        has_attribution=has_attribution,
        risk_signals=risk_signals,
    )

    # Mixed-signal detection: a symbol/sector in BOTH favor and avoid lists.
    favor_symbols = _symbols_in(watchlist) | _symbols_in(keep_doing) | _symbols_in(helped)
    avoid_symbols = _symbols_in(avoid)
    overlap_symbols = sorted(favor_symbols & avoid_symbols)
    favor_buckets = {bucket_for(s) for s in favor_symbols} - {"unknown"}
    avoid_buckets = {bucket_for(s) for s in avoid_symbols} - {"unknown"}
    overlap_buckets = sorted(favor_buckets & avoid_buckets)
    mixed_signal_warning = ""
    if overlap_symbols or overlap_buckets:
        detail = ", ".join(overlap_symbols or overlap_buckets)
        mixed_signal_warning = (
            f"Mixed-signal warning: {detail} appears in both the favor and avoid lists; "
            "reconcile before acting."
        )

    risk_constraints = _build_risk_constraints(attribution=attribution, pm_state=portfolio_manager_state)
    pm_stance = _build_pm_stance(portfolio_manager_state)
    tomorrow_rules = _build_tomorrow_rules(
        mode=recommended_mode,
        attribution=attribution,
        pm_state=portfolio_manager_state,
        shorts_hurt=shorts_hurt,
    )

    # One-sentence executive summary (never invents — uses only known signals).
    spy_text = spy_relative or "SPY-relative result n/a"
    if not has_attribution and not helped and not hurt:
        executive_summary = (
            f"{_team_display(team_id)}: no fresh trading data — run in {recommended_mode} mode; "
            "hold and observe (paper-only)."
        )
    else:
        lead = helped[0] if helped else (hurt[0] if hurt else "")
        lead_text = f"; key mover {lead}" if lead else ""
        executive_summary = (
            f"{_team_display(team_id)} should run in {recommended_mode} mode tomorrow ({spy_text}{lead_text})."
        )

    plan = TomorrowPlan(
        team_id=team_id,
        generated_at=generated_at,
        source_date=source_date,
        equity=equity_text,
        equity_source=equity_source,
        rank=rank_text,
        recommended_mode=recommended_mode,
        executive_summary=executive_summary,
        what_worked_today=helped or [NO_UPDATE],
        what_failed_today=hurt or [NO_UPDATE],
        stop_doing=stop_doing or [NO_UPDATE],
        keep_doing=keep_doing or [NO_UPDATE],
        test_tomorrow=test_next or [NO_UPDATE],
        watchlist=watchlist or [NA],
        avoid_list=avoid or [NA],
        risk_constraints=risk_constraints,
        portfolio_manager_stance=pm_stance,
        tomorrow_rules=tomorrow_rules,
        consistency_warning=CONSISTENCY_WARNING if needs_consistency_warning else "",
        mixed_signal_warning=mixed_signal_warning,
        safety_reminder=SAFETY_REMINDER,
    )
    return plan


# --- persistence ------------------------------------------------------------


def _plan_paths(team_id: str, reviews_dir: Path | str) -> tuple[Path, Path]:
    directory = Path(reviews_dir)
    return (
        directory / f"{team_id}_tomorrow_plan_latest.json",
        directory / f"{team_id}_tomorrow_plan_latest.md",
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def format_tomorrow_plan_markdown(plan: TomorrowPlan) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- (none)"

    lines = [
        f"# {_team_display(plan.team_id)} — Tomorrow Plan",
        "",
        f"- Generated at: {plan.generated_at}",
        f"- Source date: {plan.source_date}",
        f"- Equity: {plan.equity} ({plan.equity_source})",
        f"- Rank: {plan.rank}",
        f"- Recommended mode: **{plan.recommended_mode}**",
        "",
        f"**Summary:** {plan.executive_summary}",
        "",
        "## What worked today",
        bullets(plan.what_worked_today),
        "",
        "## What failed today",
        bullets(plan.what_failed_today),
        "",
        "## Stop doing",
        bullets(plan.stop_doing),
        "",
        "## Keep doing",
        bullets(plan.keep_doing),
        "",
        "## Test tomorrow",
        bullets(plan.test_tomorrow),
        "",
        "## Watchlist",
        bullets(plan.watchlist),
        "",
        "## Avoid list",
        bullets(plan.avoid_list),
        "",
        f"## Risk constraints / buying power\n{plan.risk_constraints}",
        "",
        f"## PortfolioManager stance\n{plan.portfolio_manager_stance}",
        "",
        "## Tomorrow rules",
        bullets(plan.tomorrow_rules),
    ]
    if plan.consistency_warning:
        lines += ["", f"> ⚠️ {plan.consistency_warning}"]
    if plan.mixed_signal_warning:
        lines += ["", f"> ⚠️ {plan.mixed_signal_warning}"]
    lines += ["", f"_{plan.safety_reminder}_", ""]
    return "\n".join(lines)


def save_tomorrow_plan(
    plan: TomorrowPlan,
    *,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
) -> tuple[Path, Path]:
    json_path, md_path = _plan_paths(plan.team_id, reviews_dir)
    _atomic_write(json_path, json.dumps(plan.as_dict(), indent=2, default=str))
    _atomic_write(md_path, format_tomorrow_plan_markdown(plan))
    return json_path, md_path


def load_latest_tomorrow_plan(
    team_id: str,
    *,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
) -> TomorrowPlan | None:
    json_path, _ = _plan_paths(team_id, reviews_dir)
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in {f.name for f in fields(TomorrowPlan)}}
        return TomorrowPlan(**known)
    except (ValueError, TypeError, OSError):
        return None


def format_tomorrow_plan_terminal(plan: TomorrowPlan, *, saved_paths: tuple[Path, Path] | None = None) -> str:
    def joined(items: list[str], limit: int = 6) -> str:
        if not items:
            return "(none)"
        shown = items[:limit]
        suffix = f" (+{len(items) - limit} more)" if len(items) > limit else ""
        return ", ".join(shown) + suffix

    lines = [
        f"{_team_display(plan.team_id)} Tomorrow Plan",
        f"Mode: {plan.recommended_mode}",
        f"Summary: {plan.executive_summary}",
        f"Equity: {plan.equity} ({plan.equity_source}) | Rank: {plan.rank}",
        f"Worked: {joined(plan.what_worked_today)}",
        f"Failed: {joined(plan.what_failed_today)}",
        f"Stop: {joined(plan.stop_doing)}",
        f"Keep: {joined(plan.keep_doing)}",
        f"Test tomorrow: {joined(plan.test_tomorrow)}",
        f"Watchlist: {joined(plan.watchlist)}",
        f"Avoid: {joined(plan.avoid_list)}",
        f"Risk limits: {plan.risk_constraints}",
        f"PM stance: {plan.portfolio_manager_stance}",
        f"Tomorrow rules: {joined(plan.tomorrow_rules, limit=8)}",
    ]
    if plan.consistency_warning:
        lines.append(f"Consistency: {plan.consistency_warning}")
    if plan.mixed_signal_warning:
        lines.append(f"Mixed signal: {plan.mixed_signal_warning}")
    lines.append(f"Safety: {plan.safety_reminder}")
    if saved_paths is not None:
        json_path, md_path = saved_paths
        lines.append("Saved:")
        lines.append(f"  - {json_path}")
        lines.append(f"  - {md_path}")
    return "\n".join(lines)


# --- build + persist from local data ----------------------------------------


def export_tomorrow_plan(
    team_id: str,
    *,
    scorecard_dir: Path | str = DEFAULT_SCORECARD_DIR,
    attribution_dir: Path | str = DEFAULT_ATTRIBUTION_DIR,
    learning_dir: Path | str = DEFAULT_LEARNING_DIR,
    reviews_dir: Path | str = DEFAULT_REVIEWS_DIR,
    competition_status: Any = None,
    equity_view: Any = None,
) -> tuple[TomorrowPlan, tuple[Path, Path]]:
    """Build + persist a team's Tomorrow Plan from local data only (no network/LLM).

    Loads the latest daily review (building one if absent), the learning ledger,
    the deterministic SPY attribution, and best-effort equity/rank from the
    competition status. Never raises on missing data.
    """

    attribution = load_daily_spy_attribution(
        team_id, scorecard_dir=scorecard_dir, attribution_dir=attribution_dir
    )
    daily_review = load_latest_daily_team_review(team_id, reviews_dir=reviews_dir)
    if daily_review is None:
        # Build (and persist) a fresh daily review so the plan follows the review.
        try:
            daily_review = export_daily_team_review(
                team_id,
                scorecard_dir=scorecard_dir,
                attribution_dir=attribution_dir,
                learning_dir=learning_dir,
                reviews_dir=reviews_dir,
            )
        except Exception:  # noqa: BLE001 - degrade to a plan without a review
            daily_review = None

    learning_status = TeamLearningLedger.load(team_id, learning_dir)
    scorecard = load_latest_scorecard(team_id, scorecard_dir)

    if competition_status is None:
        try:
            from src.competition.week_competition import competition_status as _cs

            competition_status = _cs(scorecard_dir=scorecard_dir)
        except Exception:  # noqa: BLE001 - rank/equity stay n/a when unavailable
            competition_status = None

    plan = build_tomorrow_plan(
        team_id,
        daily_review,
        learning_status,
        attribution,
        competition_status,
        scorecard,
        equity_view=equity_view,
    )
    saved = save_tomorrow_plan(plan, reviews_dir=reviews_dir)
    return plan, saved


# --- optional Discord posting -----------------------------------------------

DISCORD_POST_ENV = "DISCORD_POST_TOMORROW_PLAN"
DISCORD_CHANNEL_ENV = "DISCORD_TOMORROW_PLAN_CHANNEL"


@dataclass(frozen=True)
class TomorrowPlanDiscordConfig:
    enabled: bool = False
    channel_target: str = "strategy_lab"  # special channel name or "team_channels"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TomorrowPlanDiscordConfig":
        env = env if env is not None else os.environ
        raw_enabled = (env.get(DISCORD_POST_ENV) or "").strip().lower()
        target = (env.get(DISCORD_CHANNEL_ENV) or "strategy_lab").strip().lower() or "strategy_lab"
        return cls(
            enabled=raw_enabled in {"1", "true", "yes", "on"},
            channel_target=target,
        )


def format_tomorrow_plan_discord(plan: TomorrowPlan, *, max_chars: int = 1800) -> str:
    """Compact, Discord-friendly rendering (kept short; no secrets)."""

    def short(items: list[str], limit: int = 3) -> str:
        if not items:
            return "(none)"
        return ", ".join(items[:limit])

    lines = [
        f"📋 {_team_display(plan.team_id)} — Tomorrow Plan ({plan.source_date})",
        f"Mode: {plan.recommended_mode} | Rank: {plan.rank} | Equity: {plan.equity} ({plan.equity_source})",
        f"Summary: {plan.executive_summary}",
        f"Worked: {short(plan.what_worked_today)}",
        f"Failed: {short(plan.what_failed_today)}",
        f"Stop: {short(plan.stop_doing)}",
        f"Keep: {short(plan.keep_doing)}",
        f"Test: {short(plan.test_tomorrow)}",
        f"Rules: {short(plan.tomorrow_rules, limit=3)}",
    ]
    if plan.consistency_warning:
        lines.append(f"⚠️ {plan.consistency_warning}")
    if plan.mixed_signal_warning:
        lines.append(f"⚠️ {plan.mixed_signal_warning}")
    lines.append(plan.safety_reminder)
    message = "\n".join(lines)
    return message[:max_chars]


def post_tomorrow_plan_to_discord(
    plan: TomorrowPlan,
    *,
    env: Mapping[str, str] | None = None,
    sender=None,
    dry_run: bool = False,
    state_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Post the compact Tomorrow Plan to Discord when enabled. Never raises.

    Disabled by default (``DISCORD_POST_TOMORROW_PLAN`` unset/false). Reuses the
    Phase 7S Discord plumbing for token + channel resolution + secret redaction.
    """

    config = TomorrowPlanDiscordConfig.from_env(env)
    if not config.enabled:
        return {"sent": False, "reason": "disabled"}

    try:
        from src.discord_bot.competition_updates import (
            DiscordIterationUpdateConfig,
            _send,
            redact_secrets,
            truncate_discord_message,
        )
    except Exception as exc:  # noqa: BLE001 - Discord must never crash the caller
        return {"sent": False, "reason": f"discord_unavailable: {exc}"}

    base = DiscordIterationUpdateConfig.from_env(env)
    message = format_tomorrow_plan_discord(plan, max_chars=base.max_chars)

    if config.channel_target == "team_channels":
        channel_id = base.channel_for_team(plan.team_id)
        key = f"tomorrow_plan:{plan.team_id}"
    else:
        channel_id = (base.special_channel_ids or {}).get(config.channel_target)
        key = f"tomorrow_plan:{config.channel_target}:{plan.team_id}"

    if dry_run:
        print(f"[dry-run] would post Tomorrow Plan for {plan.team_id} to {config.channel_target}:")
        print(redact_secrets(truncate_discord_message(message, base.max_chars)))
        return {"sent": False, "reason": "dry_run", "message": message}

    try:
        return _send(
            key=key,
            channel_id=channel_id,
            message=message,
            config=base,
            sender=sender,
            state_path=state_path,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - never crash the caller
        return {"sent": False, "reason": f"send_error: {exc}"}


def tomorrow_plan_discord_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Compact, secret-free status for the Tomorrow Plan Discord posting."""

    config = TomorrowPlanDiscordConfig.from_env(env)
    return {
        "enabled": config.enabled,
        "channel_target": config.channel_target,
    }


__all__ = [
    "TomorrowPlan",
    "TomorrowPlanDiscordConfig",
    "build_tomorrow_plan",
    "export_tomorrow_plan",
    "format_tomorrow_plan_discord",
    "format_tomorrow_plan_markdown",
    "format_tomorrow_plan_terminal",
    "load_latest_tomorrow_plan",
    "post_tomorrow_plan_to_discord",
    "save_tomorrow_plan",
    "tomorrow_plan_discord_status",
    "MODE_CONSERVATION",
    "MODE_EXPLORATION",
    "MODE_HOLD_OBSERVE",
    "MODE_RISK_REDUCTION",
]
