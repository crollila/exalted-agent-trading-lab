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

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_view": self.market_view,
            "key_events": self.key_events,
            "ideas": self.ideas,
            "risks": self.risks,
        }


def run_researcher(llm: LLM, team: TeamConfig, memory: AgentMemory, context: dict) -> ResearchBrief:
    system = (
        f"You are the RESEARCHER for {team.display_name}. {_PAPER_NOTE}\n"
        f"Team style: {team.stance}\n\n"
        "Your job: digest today's prices and news into a short, honest research brief "
        "for your strategy developer. Surface what is actually moving and why, flag "
        "opportunities that fit your team's style, and name the biggest risks. "
        "A boring 'nothing actionable today' brief is a good brief when true.\n\n"
        f"{_GROUNDING_RULES}"
        'Return JSON: {"market_view": "2-4 sentences on today\'s tape", '
        '"key_events": ["..."], '
        '"ideas": [{"symbol": "NVDA", "direction": "long|short", "note": "...", "source_ids": ["news_1"]}], '
        '"risks": ["..."]}\n'
        "At most 5 ideas. Only symbols that appear in the provided prices or news."
    )
    user = (
        f"YOUR MEMORY:\n{memory.render()}\n\n"
        f"CONTEXT (prices, positions, news):\n{json.dumps(context, indent=2, default=str)}"
    )
    data = llm.complete_json(ROLE_RESEARCHER, system, user)
    return ResearchBrief(
        market_view=str(data.get("market_view", "")).strip() or "unknown",
        key_events=[str(x) for x in data.get("key_events", []) if str(x).strip()][:8],
        ideas=[i for i in data.get("ideas", []) if isinstance(i, dict)][:5],
        risks=[str(x) for x in data.get("risks", []) if str(x).strip()][:8],
    )


# --------------------------------------------------------------------------
# Strategy developer
# --------------------------------------------------------------------------

@dataclass
class Proposal:
    symbol: str
    action: str            # buy | sell | short | cover
    weight_pct: float      # for buy/short: target ADDED weight of equity
    fraction: float        # for sell/cover: fraction of held qty to exit (0..1]
    thesis: str
    exit_plan: str
    confidence: float      # 0..1

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "weight_pct": self.weight_pct,
            "fraction": self.fraction,
            "thesis": self.thesis,
            "exit_plan": self.exit_plan,
            "confidence": self.confidence,
        }


@dataclass
class StrategistOutput:
    portfolio_view: str
    no_trade_reason: str | None
    proposals: list[Proposal] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "portfolio_view": self.portfolio_view,
            "no_trade_reason": self.no_trade_reason,
            "proposals": [p.as_dict() for p in self.proposals],
            "parse_errors": self.parse_errors,
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
        if not symbol or not symbol.isalnum() or len(symbol) > 6:
            errors.append(f"invalid symbol {symbol!r}")
            continue
        if action not in VALID_ACTIONS:
            errors.append(f"{symbol}: invalid action {action!r} (use buy/sell/short/cover)")
            continue
        if not thesis:
            errors.append(f"{symbol}: missing thesis")
            continue
        proposals.append(
            Proposal(
                symbol=symbol,
                action=action,
                weight_pct=_clamp(raw.get("weight_pct", 0.05), 0.005, 1.0, 0.05),
                fraction=_clamp(raw.get("fraction", 1.0), 0.05, 1.0, 1.0),
                thesis=thesis,
                exit_plan=str(raw.get("exit_plan", "")).strip() or "Exit on thesis invalidation.",
                confidence=_clamp(raw.get("confidence", 0.5), 0.0, 1.0, 0.5),
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
) -> StrategistOutput:
    system = (
        f"You are the STRATEGY DEVELOPER for {team.display_name}. {_PAPER_NOTE}\n"
        f"Team style: {team.stance}\n\n"
        "Your job: turn the researcher's brief and your portfolio state into trade "
        "decisions that beat SPY today and compound an edge over weeks. First manage "
        "what you hold (cut losers whose thesis broke, take profits per exit plans), "
        "then add new positions only when an idea is clearly better than what you "
        "already hold. Doing NOTHING is a valid, often correct decision — say why.\n\n"
        "Actions: 'buy' opens/adds a long; 'sell' exits part/all of a long you hold; "
        "'short' opens/adds a short; 'cover' exits part/all of a short you hold. "
        "weight_pct is the ADDED position size as a fraction of equity (e.g. 0.08 = 8%); "
        "fraction applies to sell/cover (1.0 = exit fully). A deterministic risk engine "
        "computes actual share counts and enforces hard caps — you cannot bypass it.\n\n"
        f"{_GROUNDING_RULES}"
        f"At most {max_proposals} proposals.\n"
        'Return JSON: {"portfolio_view": "2-4 sentences: what is working/failing vs SPY and why", '
        '"proposals": [{"symbol": "NVDA", "action": "buy", "weight_pct": 0.08, "fraction": 1.0, '
        '"thesis": "...", "exit_plan": "...", "confidence": 0.6}], '
        '"no_trade_reason": "required if proposals is empty, else null"}'
    )
    user = (
        f"YOUR MEMORY:\n{memory.render()}\n\n"
        f"RESEARCH BRIEF (from your researcher):\n{json.dumps(brief.as_dict(), indent=2)}\n\n"
        f"PORTFOLIO & COMPETITION STATE:\n{json.dumps(context, indent=2, default=str)}"
    )
    data = llm.complete_json(ROLE_STRATEGIST, system, user)
    proposals, errors = parse_proposals(data.get("proposals"), max_proposals)
    no_trade = data.get("no_trade_reason")
    return StrategistOutput(
        portfolio_view=str(data.get("portfolio_view", "")).strip() or "unknown",
        no_trade_reason=(str(no_trade).strip() if no_trade else None),
        proposals=proposals,
        parse_errors=errors,
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
