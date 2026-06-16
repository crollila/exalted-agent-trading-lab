"""LLM-backed advisory review agents on routed cheap models (Phase 7P).

These agents *advise* — they improve reasoning and written strategy quality. They
never control execution. Every function here:

* accepts an injected/mock ``provider`` for tests (never calls real OpenAI in
  tests);
* tolerates malformed/invalid LLM JSON and provider failure, falling back to a
  deterministic compact text;
* falls back to deterministic behavior when its feature flag is disabled;
* returns ``model_used`` + ``provider_used`` metadata and keeps outputs compact;
* never reads, prints, or logs secrets (only model NAMES + a key-configured bool).

Routing (Phase 7O ``build_routed_provider``):

* portfolio advice      -> ``portfolio_manager`` model
* proposal/trade critique -> ``critique`` model
* daily review narrative -> ``review`` (or ``summary``) model
* memory compression    -> ``summary`` model
* research synthesis    -> ``research_synthesis`` model

Hard safety boundary: the deterministic ``PortfolioManager`` / risk engine remain
authoritative. The advisory portfolio manager may NARROW behavior (lower the cap,
recommend no-trade/hold, add warnings, suggest advisory trims) but can never widen
caps, unblock low-buying-power buys, bypass deterministic risk/review approvals,
authorize options/spreads/naked options, or change team credentials / broker mode.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any, Callable, Mapping

from src.agents.llm_provider import LLMProvider, LLMProviderConfig
from src.agents.model_routing import build_routed_provider, resolve_model, routing_status
from src.competition.portfolio_manager import PortfolioDecision
from src.config.permissions import _read_bool

# --- Feature flags ----------------------------------------------------------


class LLMReviewFlags:
    """Per-stage advisory LLM enable flags (Phase 7P).

    Conservative defaults: the portfolio manager and research synthesis stages
    (closest to trade decisions / least-proven value) default OFF; the cheap
    advisory stages default ON.
    """

    __slots__ = (
        "portfolio_manager",
        "review_agent",
        "critique_agent",
        "summary_agent",
        "research_synthesis",
        "daily_review",
    )

    def __init__(
        self,
        *,
        portfolio_manager: bool = False,
        review_agent: bool = True,
        critique_agent: bool = True,
        summary_agent: bool = True,
        research_synthesis: bool = False,
        daily_review: bool = True,
    ) -> None:
        self.portfolio_manager = portfolio_manager
        self.review_agent = review_agent
        self.critique_agent = critique_agent
        self.summary_agent = summary_agent
        self.research_synthesis = research_synthesis
        self.daily_review = daily_review

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LLMReviewFlags":
        if env is None:
            env = os.environ
        return cls(
            portfolio_manager=_read_bool(env, "ENABLE_LLM_PORTFOLIO_MANAGER", False),
            review_agent=_read_bool(env, "ENABLE_LLM_REVIEW_AGENT", True),
            critique_agent=_read_bool(env, "ENABLE_LLM_CRITIQUE_AGENT", True),
            summary_agent=_read_bool(env, "ENABLE_LLM_SUMMARY_AGENT", True),
            research_synthesis=_read_bool(env, "ENABLE_LLM_RESEARCH_SYNTHESIS", False),
            daily_review=_read_bool(env, "ENABLE_LLM_DAILY_REVIEW", True),
        )

    def as_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in self.__slots__}


def review_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Observable advisory-stage status. Model NAMES + enabled bool only — no secrets."""

    flags = LLMReviewFlags.from_env(env)
    routing = routing_status(env)
    return {
        "provider": routing["provider"],
        "api_key_configured": routing["api_key_configured"],
        "stages": {
            "portfolio_manager": {
                "enabled": flags.portfolio_manager,
                "model": routing["portfolio_manager_model"],
            },
            "review_agent": {"enabled": flags.review_agent, "model": routing["review_model"]},
            "critique_agent": {"enabled": flags.critique_agent, "model": routing["critique_model"]},
            "summary_agent": {"enabled": flags.summary_agent, "model": routing["summary_model"]},
            "research_synthesis": {
                "enabled": flags.research_synthesis,
                "model": routing["research_synthesis_model"],
            },
            "daily_review": {"enabled": flags.daily_review, "model": routing["review_model"]},
        },
    }


# --- Internal helpers -------------------------------------------------------


def _provider_name(provider: LLMProvider | None, env: Mapping[str, str] | None) -> str:
    if provider is not None:
        return getattr(provider, "name", "injected")
    try:
        return LLMProviderConfig.from_env(env).provider
    except Exception:  # noqa: BLE001 - status helpers must never crash
        return "unknown"


def _resolve_provider(
    task: str,
    provider: LLMProvider | None,
    env: Mapping[str, str] | None,
) -> tuple[LLMProvider | None, str | None]:
    """Return ``(provider, None)`` or ``(None, reason)`` — never raises, never prints secrets."""

    if provider is not None:
        return provider, None
    try:
        return build_routed_provider(task, env=env), None
    except Exception as exc:  # noqa: BLE001 - missing key / misconfig -> safe deterministic fallback
        return None, f"provider_unavailable: {exc}"


def _safe_complete(
    provider: LLMProvider,
    system_prompt: str,
    user_prompt: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Call a provider and parse JSON, tolerating any failure. Never raises."""

    try:
        raw = provider.complete_json(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001 - provider/runtime failure must not crash advisory stage
        return None, f"provider_error: {exc}"
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None, "malformed_json"
    if not isinstance(data, dict):
        return None, "non_object_json"
    return data, None


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _str_list(value: Any, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned[:limit]


def _opt_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _present(content: dict[str, Any]) -> dict[str, Any]:
    """Keep only non-empty keys so deterministic defaults fill any gaps."""

    return {k: v for k, v in content.items() if v not in (None, "", [], {})}


def _run_llm_stage(
    *,
    task: str,
    enabled: bool,
    provider: LLMProvider | None,
    env: Mapping[str, str] | None,
    deterministic: dict[str, Any],
    build_prompts: Callable[[], tuple[str, str]],
    adapt: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Generic advisory runner: deterministic base + optional LLM enrichment.

    Always returns the deterministic content; ``source`` records whether the LLM
    enriched it (``llm``), the flag was off (``disabled``), or it fell back
    (``fallback`` with a ``fallback_reason``).
    """

    model = resolve_model(task, env)
    meta = {"model_used": model, "provider_used": _provider_name(provider, env)}
    if not enabled:
        return {**deterministic, "available": False, "source": "disabled", **meta}

    prov, reason = _resolve_provider(task, provider, env)
    if prov is None:
        return {**deterministic, "available": False, "source": "fallback", "fallback_reason": reason, **meta}
    meta["provider_used"] = getattr(prov, "name", meta["provider_used"])

    system_prompt, user_prompt = build_prompts()
    data, err = _safe_complete(prov, system_prompt, user_prompt)
    if data is None:
        return {**deterministic, "available": False, "source": "fallback", "fallback_reason": err, **meta}

    enriched = _present(adapt(data))
    return {**deterministic, **enriched, "available": True, "source": "llm", **meta}


def _compact_context(context: Mapping[str, Any] | None) -> str:
    """Serialize allowlisted context compactly. Callers pass NO secrets in context."""

    return json.dumps(context or {}, indent=2, default=str)[:6000]


# --- Trade / proposal critique (task="critique") ----------------------------


def _deterministic_critique(
    team_id: str,
    context: Mapping[str, Any] | None,
    candidates: list[Any] | None,
) -> dict[str, Any]:
    context = context or {}
    candidates = candidates if candidates is not None else (context.get("candidates") or [])
    count = len(candidates) if candidates else int(context.get("candidate_count", 0) or 0)
    spy_excess = context.get("spy_excess")
    sectors = context.get("sector_exposure") or {}
    concentrated = sorted(s for s, c in sectors.items() if s and s != "unknown" and int(c or 0) >= 3)

    concerns: list[str] = []
    if count == 0:
        concerns.append("No candidate proposals cleared review; holding may be correct.")
    if isinstance(spy_excess, (int, float)) and spy_excess < 0:
        concerns.append(f"Team trailing SPY ({spy_excess:+.4f}); avoid beta-chasing risk.")
    if concentrated:
        concerns.append(f"Concentration risk in {', '.join(concentrated)}.")

    spy_text = (
        "SPY-relative performance unknown."
        if not isinstance(spy_excess, (int, float))
        else f"{'Beating' if spy_excess > 0 else 'Trailing'} SPY by {spy_excess:+.4f} excess."
    )
    no_trade_better = count == 0 or (
        isinstance(spy_excess, (int, float)) and spy_excess < -0.02 and team_id == "team_beta"
    )
    return {
        "concerns": concerns or ["No critical concerns flagged."],
        "missing_information": [
            "quote freshness / staleness",
            "borrow availability for any shorts",
            "catalyst timing vs holding period",
        ],
        "spy_relative_risk": spy_text,
        "sector_concentration_risk": (
            f"Concentrated in {', '.join(concentrated)}." if concentrated else "No obvious sector concentration."
        ),
        "no_trade_may_be_better": bool(no_trade_better),
        "summary": f"Deterministic critique for {team_id}: {count} candidate idea(s) reviewed.",
    }


def generate_trade_critique(
    *,
    team_id: str,
    context: Mapping[str, Any] | None = None,
    candidates: list[Any] | None = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Advisory critique of candidate proposals + current holdings (task=critique).

    Returns concerns, missing information, SPY-relative risk, sector concentration
    risk, and whether no-trade may be better. Advisory only.
    """

    if enabled is None:
        enabled = LLMReviewFlags.from_env(env).critique_agent
    deterministic = _deterministic_critique(team_id, context, candidates)

    def build_prompts() -> tuple[str, str]:
        system = (
            "You are a risk-aware trade critic in a PAPER-ONLY trading competition. You never place "
            "trades, never size positions, and never authorize bypassing risk controls — a deterministic "
            "risk engine does all of that. Critique only. Respond with a single JSON object: "
            '{"concerns": [...], "missing_information": [...], "spy_relative_risk": "...", '
            '"sector_concentration_risk": "...", "no_trade_may_be_better": true/false, "summary": "..."}'
        )
        user = f"Team: {team_id}\nReview this allowlisted context (no secrets):\n{_compact_context(context)}"
        return system, user

    def adapt(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "concerns": _str_list(data.get("concerns")),
            "missing_information": _str_list(data.get("missing_information")),
            "spy_relative_risk": _str(data.get("spy_relative_risk")),
            "sector_concentration_risk": _str(data.get("sector_concentration_risk")),
            "no_trade_may_be_better": bool(data.get("no_trade_may_be_better"))
            if "no_trade_may_be_better" in data
            else None,
            "summary": _str(data.get("summary")),
        }

    return _run_llm_stage(
        task="critique",
        enabled=enabled,
        provider=provider,
        env=env,
        deterministic=deterministic,
        build_prompts=build_prompts,
        adapt=adapt,
    )


# --- Daily review narrative (task="review" or "summary") --------------------


def _as_plain(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    as_dict = getattr(obj, "as_dict", None)
    if callable(as_dict):
        try:
            return as_dict()
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _deterministic_daily_narrative(
    team_id: str,
    attribution: Any,
    review: Any,
) -> dict[str, Any]:
    a = _as_plain(attribution)
    r = _as_plain(review)
    excess = a.get("excess_return")
    if isinstance(excess, (int, float)):
        verdict = "beat" if excess > 0 else ("trailed" if excess < 0 else "matched")
        headline = f"{team_id} {verdict} SPY by {excess:+.4f} excess."
    else:
        headline = f"{team_id} SPY-relative result unknown."
    why = a.get("explanation") or r.get("why_vs_spy") or "Drivers unknown from local data."
    todo = _str_list(r.get("test_next")) or ["Hold and observe; only act if an idea beats the weakest holding."]
    return {
        "narrative": f"{headline} {why}",
        "why_beat_or_lost": _str(why),
        "what_to_do_tomorrow": todo,
        "recommended_mode": _str(r.get("recommended_mode")) or "conservation",
    }


def generate_daily_review_narrative(
    *,
    team_id: str,
    attribution: Any = None,
    review: Any = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
    task: str = "review",
) -> dict[str, Any]:
    """Turn deterministic daily-spy-attribution into a concise written review.

    Explains why the team beat/lost SPY and what to do tomorrow. Advisory only.
    """

    if task not in ("review", "summary"):
        task = "review"
    if enabled is None:
        enabled = LLMReviewFlags.from_env(env).daily_review
    deterministic = _deterministic_daily_narrative(team_id, attribution, review)

    def build_prompts() -> tuple[str, str]:
        system = (
            "You are a portfolio coach writing a SHORT daily review for a PAPER-ONLY trading team. "
            "You never place trades and never authorize bypassing risk. Respond with a single JSON "
            'object: {"narrative": "2-4 sentences", "why_beat_or_lost": "...", '
            '"what_to_do_tomorrow": [...], "recommended_mode": "exploration|conservation|reset"}'
        )
        payload = {
            "team_id": team_id,
            "attribution": _as_plain(attribution),
            "review": _as_plain(review),
        }
        user = f"Write the daily review from this local data (no secrets):\n{_compact_context(payload)}"
        return system, user

    def adapt(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "narrative": _str(data.get("narrative")),
            "why_beat_or_lost": _str(data.get("why_beat_or_lost")),
            "what_to_do_tomorrow": _str_list(data.get("what_to_do_tomorrow")),
            "recommended_mode": _str(data.get("recommended_mode")),
        }

    return _run_llm_stage(
        task=task,
        enabled=enabled,
        provider=provider,
        env=env,
        deterministic=deterministic,
        build_prompts=build_prompts,
        adapt=adapt,
    )


# --- Strategy-memory compression (task="summary") ---------------------------


def _deterministic_memory_summary(team_id: str, memory: Any) -> dict[str, Any]:
    m = _as_plain(memory)
    lessons = (
        _str_list(m.get("current_day_lessons"), limit=3)
        + _str_list(m.get("trailing_3_day_lessons"), limit=3)
    )
    key_lessons = list(dict.fromkeys(lessons))[:5]
    favor = _str_list(m.get("symbols_to_favor"), limit=6)
    avoid = _str_list(m.get("symbols_to_avoid"), limit=6)
    mode = _str(m.get("recommended_mode")) or "conservation"
    return {
        "compact_summary": (
            f"{team_id}: mode={mode}; favor {favor or '(none)'}; avoid {avoid or '(none)'}; "
            f"{len(key_lessons)} key lesson(s)."
        ),
        "key_lessons": key_lessons or ["No recurring lessons yet."],
    }


def summarize_strategy_memory(
    *,
    team_id: str,
    memory: Any = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compress multi-day team memory + daily reviews into compact future context."""

    if enabled is None:
        enabled = LLMReviewFlags.from_env(env).summary_agent
    deterministic = _deterministic_memory_summary(team_id, memory)

    def build_prompts() -> tuple[str, str]:
        system = (
            "You compress a trading team's multi-day memory into a COMPACT summary for future prompts. "
            "Advisory research feedback only; never authorize bypassing risk. Respond with a single JSON "
            'object: {"compact_summary": "...", "key_lessons": [...]}'
        )
        user = f"Compress this multi-day memory (no secrets):\n{_compact_context(_as_plain(memory))}"
        return system, user

    def adapt(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "compact_summary": _str(data.get("compact_summary")),
            "key_lessons": _str_list(data.get("key_lessons"), limit=5),
        }

    return _run_llm_stage(
        task="summary",
        enabled=enabled,
        provider=provider,
        env=env,
        deterministic=deterministic,
        build_prompts=build_prompts,
        adapt=adapt,
    )


# --- Research synthesis (task="research_synthesis"; off by default) ----------


def _deterministic_research_synthesis(sources: list[Any] | None) -> dict[str, Any]:
    sources = sources or []
    summary: list[str] = []
    for src in sources[:6]:
        s = _as_plain(src) if not isinstance(src, str) else {"summary": src}
        sid = _str(s.get("source_id") or s.get("source") or "src")
        text = _str(s.get("summary") or s.get("headline") or s.get("title"))
        if text:
            summary.append(f"[{sid}] {text[:160]}")
    return {
        "source_summary": summary or ["No research sources provided."],
        "uncertainty_notes": [
            "Local synthesis only; no web search performed.",
            "Treat as research feedback; never authorizes bypassing risk.",
        ],
    }


def synthesize_research_sources(
    *,
    team_id: str,
    sources: list[Any] | None = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Summarize already-fetched Alpaca news/research snippets. No web search."""

    if enabled is None:
        enabled = LLMReviewFlags.from_env(env).research_synthesis
    deterministic = _deterministic_research_synthesis(sources)

    def build_prompts() -> tuple[str, str]:
        system = (
            "You summarize ALREADY-FETCHED research snippets into a compact source summary with "
            "uncertainty notes. You DO NOT browse the web and DO NOT invent sources. Respond with a "
            'single JSON object: {"source_summary": [...], "uncertainty_notes": [...]}'
        )
        payload = {"team_id": team_id, "sources": [_as_plain(s) if not isinstance(s, str) else s for s in (sources or [])]}
        user = f"Summarize only these provided sources (no secrets, no web search):\n{_compact_context(payload)}"
        return system, user

    def adapt(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_summary": _str_list(data.get("source_summary"), limit=8),
            "uncertainty_notes": _str_list(data.get("uncertainty_notes"), limit=6),
        }

    return _run_llm_stage(
        task="research_synthesis",
        enabled=enabled,
        provider=provider,
        env=env,
        deterministic=deterministic,
        build_prompts=build_prompts,
        adapt=adapt,
    )


# --- Strategy debate (compact bull/bear; advisory) --------------------------


def _deterministic_debate(
    team_id: str,
    attribution: Any,
    feedback: Mapping[str, Any] | None,
    review: Any,
) -> dict[str, Any]:
    a = _as_plain(attribution)
    r = _as_plain(review)
    outcome = (feedback or {}).get("outcome_feedback", {}) if isinstance(feedback, dict) else {}
    worked = int(outcome.get("worked_count", 0) or 0)
    failed = int(outcome.get("failed_count", 0) or 0)
    excess = a.get("excess_return")
    winners = _str_list([w.get("symbol") if isinstance(w, dict) else w for w in (a.get("top_winners") or [])], limit=3)
    losers = _str_list([l.get("symbol") if isinstance(l, dict) else l for l in (a.get("top_losers") or [])], limit=3)

    bull = (
        f"Winners {winners or '(none)'} are working; {worked} recent thesis(es) worked."
        if winners or worked
        else "Edge unproven but downside controlled; disciplined sizing preserves capital."
    )
    bear = (
        f"Losers {losers or '(none)'} and {failed} failed thesis(es) suggest weak selection / timing."
        if losers or failed
        else "No proven edge vs SPY; new risk may just add churn."
    )
    if isinstance(excess, (int, float)):
        recommend = "trade" if excess > 0 and team_id == "team_alpha" else ("observe" if excess < -0.02 else "hold")
    else:
        recommend = "observe"
    return {
        "bull_case": bull,
        "bear_case": bear,
        "what_would_prove_us_wrong": _str(a.get("explanation"))
        or "A close below the thesis invalidation level or a failed catalyst.",
        "better_than_weakest_holding": (
            "Only add if the new idea's risk/reward clearly beats the weakest current holding."
        ),
        "trade_hold_or_observe": recommend,
        "cost_risk_note": "Advisory only; deterministic risk sizes/gates every trade. Keep token spend low.",
        "recommended_mode": _str(r.get("recommended_mode")) or ("exploration" if team_id == "team_alpha" else "conservation"),
    }


def team_debate_context(
    team_id: str,
    *,
    attribution: Any = None,
    feedback: Mapping[str, Any] | None = None,
    review: Any = None,
    enabled: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compact, deterministic team-debate block for inclusion in LLM strategy context.

    Advisory research feedback only — never authorizes bypassing risk/credentials.
    Returns ``{"available": False}`` when the critique/review agents are disabled.
    """

    if not enabled:
        return {"available": False, "note": "team debate disabled"}
    debate = _deterministic_debate(team_id, attribution, feedback, review)
    debate.update(
        available=True,
        model_used=resolve_model("critique", env),
        note="Advisory team debate (research feedback only; never bypass risk).",
    )
    return debate


def build_team_debate(
    *,
    team_id: str,
    attribution: Any = None,
    feedback: Mapping[str, Any] | None = None,
    review: Any = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Bull/bear strategy debate, optionally enriched by the critique model.

    Compact output: bull case, bear case, what would prove us wrong, better than
    weakest holding, trade/hold/observe, cost/risk note, and model used.
    """

    if enabled is None:
        flags = LLMReviewFlags.from_env(env)
        enabled = flags.critique_agent or flags.review_agent
    deterministic = _deterministic_debate(team_id, attribution, feedback, review)

    def build_prompts() -> tuple[str, str]:
        system = (
            "You run a SHORT bull-vs-bear debate for a PAPER-ONLY trading team. You never place trades "
            "and never authorize bypassing risk. Respond with a single JSON object: "
            '{"bull_case": "...", "bear_case": "...", "what_would_prove_us_wrong": "...", '
            '"better_than_weakest_holding": "...", "trade_hold_or_observe": "trade|hold|observe", '
            '"cost_risk_note": "..."}'
        )
        payload = {
            "team_id": team_id,
            "attribution": _as_plain(attribution),
            "review": _as_plain(review),
            "outcome_feedback": (feedback or {}).get("outcome_feedback", {}) if isinstance(feedback, dict) else {},
        }
        user = f"Debate this team's position from local data (no secrets):\n{_compact_context(payload)}"
        return system, user

    def adapt(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "bull_case": _str(data.get("bull_case")),
            "bear_case": _str(data.get("bear_case")),
            "what_would_prove_us_wrong": _str(data.get("what_would_prove_us_wrong")),
            "better_than_weakest_holding": _str(data.get("better_than_weakest_holding")),
            "trade_hold_or_observe": _str(data.get("trade_hold_or_observe")),
            "cost_risk_note": _str(data.get("cost_risk_note")),
        }

    return _run_llm_stage(
        task="critique",
        enabled=enabled,
        provider=provider,
        env=env,
        deterministic=deterministic,
        build_prompts=build_prompts,
        adapt=adapt,
    )


# --- Advisory portfolio manager (task="portfolio_manager"; off by default) ---


def _deterministic_pm_advice(decision: PortfolioDecision) -> dict[str, Any]:
    return {
        "recommended_decision": "",
        "recommend_no_trade": False,
        "max_new_proposals_this_cycle": None,
        "warnings": [],
        "suggested_trims": [],
        "risk_notes": "Deterministic portfolio manager remains authoritative.",
    }


def generate_portfolio_manager_advice(
    *,
    team_id: str,
    decision: PortfolioDecision,
    context: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Advisory portfolio-manager opinion (task=portfolio_manager).

    Structured advice ONLY. The deterministic ``PortfolioDecision`` stays
    authoritative; see :func:`merge_portfolio_advice` for the narrow-only merge.
    """

    if enabled is None:
        enabled = LLMReviewFlags.from_env(env).portfolio_manager
    deterministic = _deterministic_pm_advice(decision)

    def build_prompts() -> tuple[str, str]:
        system = (
            "You are an advisory portfolio manager in a PAPER-ONLY competition. A deterministic risk "
            "engine is AUTHORITATIVE: you may only make things SAFER. You may lower "
            "max_new_proposals_this_cycle, recommend no_trade/hold, add warnings, and suggest advisory "
            "trims. You may NOT raise caps, unblock buys, authorize options/spreads, or change "
            "credentials/broker mode. Respond with a single JSON object: "
            '{"recommended_decision": "hold|no_trade|trim|close|rotate|add|...", '
            '"recommend_no_trade": true/false, "max_new_proposals_this_cycle": 0-3, '
            '"warnings": [...], "suggested_trims": [...], "risk_notes": "..."}'
        )
        payload = {
            "team_id": team_id,
            "deterministic_decision": decision.as_dict(),
            "context": context or {},
        }
        user = f"Advise on this deterministic decision (no secrets):\n{_compact_context(payload)}"
        return system, user

    def adapt(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "recommended_decision": _str(data.get("recommended_decision")),
            "recommend_no_trade": bool(data.get("recommend_no_trade"))
            if "recommend_no_trade" in data
            else None,
            "max_new_proposals_this_cycle": _opt_int(data.get("max_new_proposals_this_cycle")),
            "warnings": _str_list(data.get("warnings")),
            "suggested_trims": _str_list(data.get("suggested_trims")),
            "risk_notes": _str(data.get("risk_notes")),
        }

    return _run_llm_stage(
        task="portfolio_manager",
        enabled=enabled,
        provider=provider,
        env=env,
        deterministic=deterministic,
        build_prompts=build_prompts,
        adapt=adapt,
    )


def merge_portfolio_advice(decision: PortfolioDecision, advice: Mapping[str, Any] | None) -> PortfolioDecision:
    """Merge advisory PM advice into a deterministic decision — NARROW ONLY.

    The LLM may lower the cap, force no-trade/hold, append warnings, and add
    advisory trims. It can never increase the cap, unblock a deterministically
    blocked decision, or authorize options/spreads/credentials/broker changes.
    """

    if not advice:
        return decision

    merged = replace(decision)

    # Cap can only DECREASE.
    llm_cap = _opt_int(advice.get("max_new_proposals_this_cycle"))
    if llm_cap is not None:
        merged.max_new_proposals_this_cycle = max(0, min(decision.max_new_proposals_this_cycle, llm_cap))

    # Hold / no-trade recommendation forces zero new orders.
    rec = str(advice.get("recommended_decision") or "").strip().lower()
    if bool(advice.get("recommend_no_trade")) or rec in ("no_trade", "hold"):
        merged.allowed_to_generate_new_orders = False
        merged.max_new_proposals_this_cycle = 0

    # Never unblock what the deterministic engine blocked (e.g. low buying power).
    if not decision.allowed_to_generate_new_orders:
        merged.allowed_to_generate_new_orders = False
        merged.max_new_proposals_this_cycle = min(
            merged.max_new_proposals_this_cycle, decision.max_new_proposals_this_cycle
        )
    if merged.max_new_proposals_this_cycle <= 0:
        merged.allowed_to_generate_new_orders = False

    # Advisory warnings / notes appended (never authorize anything new).
    note_bits = [decision.risk_notes] if decision.risk_notes else []
    warnings = _str_list(advice.get("warnings"))
    if warnings:
        note_bits.append("LLM warnings: " + "; ".join(warnings))
    extra_notes = _str(advice.get("risk_notes"))
    if extra_notes and extra_notes != "Deterministic portfolio manager remains authoritative.":
        note_bits.append("LLM note: " + extra_notes)
    note_bits.append("LLM advisory only; deterministic risk authoritative.")
    merged.risk_notes = " ".join(note_bits)

    # Advisory trims (informational; sizing/gating still deterministic).
    trims = _str_list(advice.get("suggested_trims"))
    if trims:
        merged.proposed_closes_or_trims = list(
            dict.fromkeys([*decision.proposed_closes_or_trims, *trims])
        )

    return merged


def apply_llm_portfolio_manager(
    decision: PortfolioDecision,
    *,
    team_id: str,
    context: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
    provider: LLMProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[PortfolioDecision, dict[str, Any]]:
    """Run the advisory PM (gated) and merge it narrow-only into ``decision``.

    Returns ``(possibly_narrowed_decision, advice_meta)``. When disabled or on
    failure, the original deterministic decision is returned unchanged.
    """

    if enabled is None:
        enabled = LLMReviewFlags.from_env(env).portfolio_manager
    advice = generate_portfolio_manager_advice(
        team_id=team_id, decision=decision, context=context, enabled=enabled, provider=provider, env=env
    )
    if advice.get("source") != "llm":
        return decision, advice
    return merge_portfolio_advice(decision, advice), advice
