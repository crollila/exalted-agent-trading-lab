"""The three agents on each team: researcher, strategy developer, risk analyst.

Each agent is one LLM call with its own system prompt and its own persistent
memory. The chain per cycle is:

    researcher  ->  strategist  ->  risk analyst  ->  deterministic risk engine

Hard boundaries (identical to the old system's best rules, kept on purpose):
* Agents produce JSON only. They never call the broker and never size orders —
  the deterministic engine computes every share count.
* Agents must only use facts given in the prompt. Unknown facts stay "unknown";
  inventing prices or news is instructed against and prices are re-checked in
  code before any order.
* The risk analyst can only veto or shrink a proposal, never enlarge it
  (enforced in code, not just in the prompt).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.config import ROLE_RESEARCHER, ROLE_RISK, ROLE_STRATEGIST, TeamConfig
from src.llm import LLM
from src.memory import AgentMemory

VALID_ACTIONS = ("buy", "sell", "short", "cover")

_PAPER_NOTE = (
    "This is a PAPER-TRADING research competition. No real money exists anywhere. "
    "Your team competes daily against another AI team and against the S&P 500 (SPY)."
)

_GROUNDING_RULES = (
    "Rules:\n"
    "- Respond with ONE valid JSON object. No prose outside JSON.\n"
    "- Use ONLY facts provided in the context. If something is unknown, say 'unknown'. "
    "Never invent prices, news, or tickers.\n"
    "- When you rely on a news item, cite its source_id.\n"
)


# --------------------------------------------------------------------------
# Researcher
# --------------------------------------------------------------------------

@dataclass
class ResearchBrief:
    market_view: str
    key_events: list[str] = field(default_factory=list)
    ideas: list[dict] = field(default_factory=list)   # {symbol, direction, note, source_ids}
    risks: list[str] = field(default_factory=list)
    watchlist_add: list[str] = field(default_factory=list)
    watchlist_remove: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_view": self.market_view,
            "key_events": self.key_events,
            "ideas": self.ideas,
            "risks": self.risks,
            "watchlist_add": self.watchlist_add,
            "watchlist_remove": self.watchlist_remove,
        }


def run_researcher(llm: LLM, team: TeamConfig, memory: AgentMemory, context: dict) -> ResearchBrief:
    web_note = (
        "You can SEARCH THE LIVE WEB. Use it for anything that sharpens the brief: "
        "breaking news, earnings previews, unusual moves, sector chatter, macro data. "
        "Mark web-sourced claims with a URL or 'web:' note so they are checkable.\n"
        if llm.supports_web_search
        else ""
    )
    system = (
        f"You are the RESEARCHER for {team.display_name}. {_PAPER_NOTE}\n"
        f"Team style: {team.stance}\n\n"
        "Your job: digest today's prices, movers, news, and earnings calendar into a "
        "short, honest research brief for your strategy developer. Surface what is "
        "actually moving and why, flag opportunities that fit your team's style, and "
        "name the biggest risks. ALWAYS flag any held or watched symbol reporting "
        "earnings soon — holding through earnings must be a conscious choice. "
        "A boring 'nothing actionable today' brief is a good brief when true.\n"
        + web_note +
        "You also own the team WATCHLIST: add tickers you want tracked (prices/news "
        "every cycle) and drop stale ones. Additions are verified against the broker, "
        "so only real tradable tickers survive.\n\n"
        f"{_GROUNDING_RULES}"
        'Return JSON: {"market_view": "2-4 sentences on today\'s tape", '
        '"key_events": ["..."], '
        '"ideas": [{"symbol": "NVDA", "direction": "long|short", "note": "...", "source_ids": ["news_1"]}], '
        '"risks": ["..."], "watchlist_add": ["SMCI"], "watchlist_remove": []}\n'
        "At most 5 ideas. Idea symbols may come from the provided data or your web "
        "research; every symbol is later verified against the broker before any trade."
    )
    user = (
        f"YOUR MEMORY:\n{memory.render()}\n\n"
        f"CONTEXT (prices, movers, earnings, positions, news):\n"
        f"{json.dumps(context, indent=2, default=str)}"
    )
    data = llm.complete_json_with_web(ROLE_RESEARCHER, system, user)

    def _symbols(key: str) -> list[str]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        return [str(s).strip().upper() for s in raw if str(s).strip()][:5]

    return ResearchBrief(
        market_view=str(data.get("market_view", "")).strip() or "unknown",
        key_events=[str(x) for x in data.get("key_events", []) if str(x).strip()][:8],
        ideas=[i for i in data.get("ideas", []) if isinstance(i, dict)][:5],
        risks=[str(x) for x in data.get("risks", []) if str(x).strip()][:8],
        watchlist_add=_symbols("watchlist_add"),
        watchlist_remove=_symbols("watchlist_remove"),
    )


# --------------------------------------------------------------------------
# Strategy developer
# --------------------------------------------------------------------------

@dataclass
class Proposal:
    symbol: str            # stock ticker; for option exits, the OCC contract symbol
    action: str            # buy | sell | short | cover
    weight_pct: float      # for entries: target ADDED weight of equity (premium for options)
    fraction: float        # for exits: fraction of held qty to exit (0..1]
    thesis: str
    exit_plan: str
    confidence: float      # 0..1
    instrument: str = "stock"      # "stock" | "option"
    option_type: str | None = None # "call" | "put" (option entries)
    dte_target: int = 30           # option entries: target days to expiration
    moneyness: str = "atm"         # "atm" | "otm"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "weight_pct": self.weight_pct,
            "fraction": self.fraction,
            "thesis": self.thesis,
            "exit_plan": self.exit_plan,
            "confidence": self.confidence,
            "instrument": self.instrument,
            "option_type": self.option_type,
            "dte_target": self.dte_target,
            "moneyness": self.moneyness,
        }


@dataclass
class StrategistOutput:
    portfolio_view: str
    no_trade_reason: str | None
    proposals: list[Proposal] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    charter_updates: dict | None = None
    charter_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "portfolio_view": self.portfolio_view,
            "no_trade_reason": self.no_trade_reason,
            "proposals": [p.as_dict() for p in self.proposals],
            "parse_errors": self.parse_errors,
            "charter_updates": self.charter_updates,
            "charter_reason": self.charter_reason,
        }


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def parse_proposals(raw_list: Any, max_proposals: int) -> tuple[list[Proposal], list[str]]:
    """Strictly parse the strategist's proposal list; collect reject reasons."""

    proposals: list[Proposal] = []
    errors: list[str] = []
    if not isinstance(raw_list, list):
        return proposals, (["proposals was not a list"] if raw_list is not None else [])
    for raw in raw_list[: max_proposals * 2]:  # tolerate a few extras, then cap
        if not isinstance(raw, dict):
            errors.append("proposal was not an object")
            continue
        symbol = str(raw.get("symbol", "")).strip().upper()
        action = str(raw.get("action", "")).strip().lower()
        thesis = str(raw.get("thesis", "")).strip()
        instrument = str(raw.get("instrument", "stock")).strip().lower() or "stock"
        if instrument not in ("stock", "option"):
            errors.append(f"{symbol}: invalid instrument {instrument!r}")
            continue
        # Stock tickers are short; option EXITS reference the long OCC symbol.
        max_len = 21 if instrument == "option" else 6
        if not symbol or not symbol.isalnum() or len(symbol) > max_len:
            errors.append(f"invalid symbol {symbol!r}")
            continue
        if action not in VALID_ACTIONS:
            errors.append(f"{symbol}: invalid action {action!r} (use buy/sell/short/cover)")
            continue
        if instrument == "option" and action in ("short", "cover"):
            errors.append(f"{symbol}: options are LONG-only (buy to open, sell to close)")
            continue
        if not thesis:
            errors.append(f"{symbol}: missing thesis")
            continue
        option_type = str(raw.get("option_type", "") or "").strip().lower() or None
        if instrument == "option" and action == "buy" and option_type not in ("call", "put"):
            errors.append(f"{symbol}: option buys need option_type call|put")
            continue
        moneyness = str(raw.get("moneyness", "atm")).strip().lower()
        proposals.append(
            Proposal(
                symbol=symbol,
                action=action,
                weight_pct=_clamp(raw.get("weight_pct", 0.05), 0.005, 1.0, 0.05),
                fraction=_clamp(raw.get("fraction", 1.0), 0.05, 1.0, 1.0),
                thesis=thesis,
                exit_plan=str(raw.get("exit_plan", "")).strip() or "Exit on thesis invalidation.",
                confidence=_clamp(raw.get("confidence", 0.5), 0.0, 1.0, 0.5),
                instrument=instrument,
                option_type=option_type,
                dte_target=int(_clamp(raw.get("dte_target", 30), 3, 120, 30)),
                moneyness=moneyness if moneyness in ("atm", "otm") else "atm",
            )
        )
        if len(proposals) >= max_proposals:
            break
    return proposals, errors


def run_strategist(
    llm: LLM,
    team: TeamConfig,
    memory: AgentMemory,
    brief: ResearchBrief,
    context: dict,
    max_proposals: int,
    charter_text: str = "",
) -> StrategistOutput:
    system = (
        f"You are the STRATEGY DEVELOPER for {team.display_name}. {_PAPER_NOTE}\n"
        f"Founding identity: {team.stance}\n\n"
        f"YOUR CHARTER — parameters YOU chose and may change any cycle:\n{charter_text}\n\n"
        "Your job: turn the researcher's brief and your portfolio state into trade "
        "decisions that beat SPY today and compound an edge over weeks. You choose "
        "the approach — momentum, mean reversion, news, trend lines, hedges — and "
        "you own the consequences. First manage what you hold (cut losers whose "
        "thesis broke, take profits per exit plans; mind minutes_to_market_close "
        "when deciding what to carry overnight), then add positions only when an "
        "idea is clearly better than what you already hold. Doing NOTHING is a "
        "valid, often correct decision — say why.\n\n"
        "STOCKS — actions: 'buy' opens/adds a long; 'sell' exits part/all of a long; "
        "'short' opens/adds a short; 'cover' exits part/all of a short. weight_pct is "
        "the ADDED size as a fraction of equity; fraction applies to exits (1.0 = all).\n"
        "OPTIONS (if enabled in your charter) — LONG calls/puts only, never selling/"
        "writing: {\"instrument\": \"option\", \"symbol\": \"NVDA\", \"action\": \"buy\", "
        "\"option_type\": \"call|put\", \"dte_target\": 30, \"moneyness\": \"atm|otm\", "
        "\"weight_pct\": 0.02} where weight_pct is PREMIUM at risk (max loss) as a "
        "fraction of equity. To close, use action 'sell' with the OCC contract symbol "
        "shown in your positions. A deterministic risk engine picks the exact contract, "
        "computes every share/contract count, and enforces hard caps — you cannot "
        "bypass it.\n\n"
        "CHARTER CONTROL: to change your own parameters (style, max_position_pct, "
        "max_gross_exposure, cycle_minutes, instruments), include \"charter_updates\" "
        "with only the fields you want changed plus \"charter_reason\". Changes apply "
        "next cycle, are clamped to platform caps, and are announced publicly.\n\n"
        f"{_GROUNDING_RULES}"
        f"At most {max_proposals} proposals.\n"
        'Return JSON: {"portfolio_view": "2-4 sentences: what is working/failing vs SPY and why", '
        '"proposals": [{"symbol": "NVDA", "action": "buy", "weight_pct": 0.08, "fraction": 1.0, '
        '"instrument": "stock", "thesis": "...", "exit_plan": "...", "confidence": 0.6}], '
        '"no_trade_reason": "required if proposals is empty, else null", '
        '"charter_updates": null, "charter_reason": ""}'
    )
    user = (
        f"YOUR MEMORY:\n{memory.render()}\n\n"
        f"RESEARCH BRIEF (from your researcher):\n{json.dumps(brief.as_dict(), indent=2)}\n\n"
        f"PORTFOLIO & COMPETITION STATE:\n{json.dumps(context, indent=2, default=str)}"
    )
    data = llm.complete_json(ROLE_STRATEGIST, system, user)
    proposals, errors = parse_proposals(data.get("proposals"), max_proposals)
    no_trade = data.get("no_trade_reason")
    updates = data.get("charter_updates")
    return StrategistOutput(
        portfolio_view=str(data.get("portfolio_view", "")).strip() or "unknown",
        no_trade_reason=(str(no_trade).strip() if no_trade else None),
        proposals=proposals,
        parse_errors=errors,
        charter_updates=updates if isinstance(updates, dict) else None,
        charter_reason=str(data.get("charter_reason", "") or "").strip(),
    )


# --------------------------------------------------------------------------
# Risk analyst
# --------------------------------------------------------------------------

@dataclass
class RiskVerdict:
    index: int                 # position in the proposal list
    verdict: str               # approve | reduce | reject
    adjusted_weight_pct: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "verdict": self.verdict,
            "adjusted_weight_pct": self.adjusted_weight_pct,
            "reason": self.reason,
        }


def run_risk_analyst(
    llm: LLM,
    team: TeamConfig,
    memory: AgentMemory,
    proposals: list[Proposal],
    context: dict,
) -> list[RiskVerdict]:
    """One verdict per proposal. Missing/invalid model verdicts fail CLOSED (reject)."""

    if not proposals:
        return []
    system = (
        f"You are the RISK ANALYST for {team.display_name}. {_PAPER_NOTE}\n\n"
        "Your job: protect the portfolio. Review each proposed trade against the "
        "portfolio state, concentration, correlation between today's proposals, thesis "
        "quality, and your own lessons from past losses. You may APPROVE a trade, "
        "REDUCE its size (give adjusted_weight_pct smaller than requested), or REJECT "
        "it with a concrete reason. You cannot increase sizes and you cannot propose "
        "trades. Vague theses, stale reasoning, and doubling down on repeated losers "
        "deserve rejection. Hard platform caps are enforced in code after you.\n\n"
        f"{_GROUNDING_RULES}"
        'Return JSON: {"verdicts": [{"index": 0, "verdict": "approve|reduce|reject", '
        '"adjusted_weight_pct": 0.05, "reason": "..."}]}\n'
        "Exactly one verdict per proposal index."
    )
    user = (
        f"YOUR MEMORY:\n{memory.render()}\n\n"
        f"PROPOSALS (indexed):\n"
        f"{json.dumps([p.as_dict() for p in proposals], indent=2)}\n\n"
        f"PORTFOLIO & LIMITS:\n{json.dumps(context, indent=2, default=str)}"
    )
    data = llm.complete_json(ROLE_RISK, system, user)

    by_index: dict[int, RiskVerdict] = {}
    for raw in data.get("verdicts", []) if isinstance(data.get("verdicts"), list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        verdict = str(raw.get("verdict", "")).strip().lower()
        if verdict not in ("approve", "reduce", "reject"):
            continue
        adjusted = raw.get("adjusted_weight_pct")
        try:
            adjusted = float(adjusted) if adjusted is not None else None
        except (TypeError, ValueError):
            adjusted = None
        by_index[index] = RiskVerdict(
            index=index,
            verdict=verdict,
            adjusted_weight_pct=adjusted,
            reason=str(raw.get("reason", "")).strip() or "(no reason given)",
        )

    verdicts: list[RiskVerdict] = []
    for i in range(len(proposals)):
        verdicts.append(
            by_index.get(i)
            or RiskVerdict(
                index=i,
                verdict="reject",
                adjusted_weight_pct=None,
                reason="Risk analyst returned no verdict for this proposal (fail closed).",
            )
        )
    return verdicts


# --------------------------------------------------------------------------
# End-of-day team debrief (the team's voice in the daily report)
# --------------------------------------------------------------------------

DEBRIEF_SECTIONS = (
    ("what_we_did", "What we did today"),
    ("why_we_did_it", "Why we did it"),
    ("what_we_expected", "What we expected"),
    ("what_we_observed", "What we've observed so far"),
    ("what_we_learned", "What we learned"),
    ("plan_going_forward", "How we intend to go forward"),
)


def run_team_debrief(
    llm: LLM,
    team: TeamConfig,
    day_summary: dict,
    agent_lessons: dict[str, list[str]],
) -> dict[str, str]:
    """One end-of-day narrative for the whole team, written for the human owner.

    Returns a dict with the six DEBRIEF_SECTIONS keys. Uses the strategist's
    model (it is the team's voice).
    """

    system = (
        f"You are {team.display_name}'s spokesperson, writing the end-of-day debrief "
        f"for the human who owns this competition. {_PAPER_NOTE}\n"
        f"Team style: {team.stance}\n\n"
        "Write plainly and honestly, in first-person plural ('we'). Be specific: name "
        "the actual symbols, actions, and numbers from today's summary. If we did "
        "nothing, say so and explain why that was the decision. If we lost, do not "
        "spin it. 2-4 sentences per section.\n\n"
        f"{_GROUNDING_RULES}"
        'Return JSON: {"what_we_did": "...", "why_we_did_it": "...", '
        '"what_we_expected": "...", "what_we_observed": "...", '
        '"what_we_learned": "...", "plan_going_forward": "..."}'
    )
    user = (
        f"TODAY'S SUMMARY:\n{json.dumps(day_summary, indent=2, default=str)}\n\n"
        f"WHAT EACH AGENT LEARNED TODAY:\n{json.dumps(agent_lessons, indent=2)}"
    )
    data = llm.complete_json(ROLE_STRATEGIST, system, user)
    return {
        key: (str(data.get(key, "")).strip() or "(nothing recorded)")
        for key, _ in DEBRIEF_SECTIONS
    }


# --------------------------------------------------------------------------
# Rival rebuttal (cross-team learning; runs after both debriefs)
# --------------------------------------------------------------------------

def run_team_rebuttal(
    llm: LLM,
    team: TeamConfig,
    opponent_name: str,
    own_summary: dict,
    opponent_debrief: dict,
    opponent_result: dict,
) -> dict[str, Any]:
    """React to the rival's debrief: a public rebuttal + private lessons.

    Returns {"rebuttal": str, "lessons_from_rival": [str, ...]}. The rebuttal is
    posted to Discord; the lessons are appended to this team's strategist memory
    so tomorrow's decisions actually carry what the rival did right or wrong.
    """

    system = (
        f"You are {team.display_name}'s spokesperson. {_PAPER_NOTE}\n"
        f"Team style: {team.stance}\n\n"
        f"You just read {opponent_name}'s end-of-day debrief. Do two things:\n"
        f"1) REBUTTAL (public, 2-4 sentences): respond to {opponent_name} directly — "
        "call out flawed reasoning, concede what they genuinely got right, and say "
        "why your approach wins from here. Sharp and competitive is good; dishonest "
        "is not. Use the actual numbers.\n"
        "2) LESSONS (private, 1-2): what should YOUR team copy or avoid based on "
        "what the rival did right or wrong today? Concrete and actionable tomorrow — "
        "reference their actual trades/decisions, not vibes.\n\n"
        f"{_GROUNDING_RULES}"
        'Return JSON: {"rebuttal": "...", "lessons_from_rival": ["..."]}'
    )
    user = (
        f"YOUR DAY:\n{json.dumps(own_summary, indent=2, default=str)}\n\n"
        f"{opponent_name.upper()}'S RESULT:\n{json.dumps(opponent_result, indent=2, default=str)}\n\n"
        f"{opponent_name.upper()}'S DEBRIEF:\n{json.dumps(opponent_debrief, indent=2)}"
    )
    data = llm.complete_json(ROLE_STRATEGIST, system, user)
    return {
        "rebuttal": str(data.get("rebuttal", "")).strip(),
        "lessons_from_rival": [
            str(x).strip() for x in data.get("lessons_from_rival", []) if str(x).strip()
        ][:2],
    }


# --------------------------------------------------------------------------
# End-of-day reflection (shared by all roles)
# --------------------------------------------------------------------------

def run_reflection(
    llm: LLM,
    team: TeamConfig,
    role: str,
    memory: AgentMemory,
    day_summary: dict,
) -> dict[str, list[str]]:
    """Return {"lessons": [...], "playbook": [...]} from today's results.

    ``playbook`` is only requested when the memory needs compaction; otherwise
    the model returns lessons only.
    """

    wants_playbook = memory.needs_compaction
    role_focus = {
        "researcher": "what information helped or misled today, and what to look for tomorrow",
        "strategist": "which trade decisions worked or failed versus SPY, and what to do differently",
        "risk": "what should have been sized differently, vetoed, or allowed through",
    }.get(role, "what to improve")

    playbook_part = (
        '"playbook": ["8-12 durable principles distilled from ALL your lessons"], '
        if wants_playbook
        else ""
    )
    system = (
        f"You are the {role.upper()} for {team.display_name}, reflecting after the "
        f"trading day. {_PAPER_NOTE}\n\n"
        f"Write 1-3 SPECIFIC lessons about {role_focus}. Reference real symbols and "
        "outcomes from today's summary. Skip platitudes; a lesson you cannot act on "
        "tomorrow is not a lesson. If today taught nothing new, return one short "
        "note saying so.\n"
        + (
            "Your lesson list has grown long, so also distill your full history into "
            "a fresh playbook of durable principles.\n"
            if wants_playbook
            else ""
        )
        + f"\n{_GROUNDING_RULES}"
        f'Return JSON: {{"lessons": ["..."], {playbook_part}"noted": true}}'
    )
    user = (
        f"YOUR MEMORY:\n{memory.render(max_chars=4000)}\n\n"
        f"TODAY'S RESULTS:\n{json.dumps(day_summary, indent=2, default=str)}"
    )
    data = llm.complete_json(role, system, user)
    lessons = [str(x).strip() for x in data.get("lessons", []) if str(x).strip()][:3]
    playbook = [str(x).strip() for x in data.get("playbook", []) if str(x).strip()][:12]
    return {"lessons": lessons, "playbook": playbook}
