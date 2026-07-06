"""End-of-day pass: score the day, make the agents learn, tell the owner.

For each team:
1. Day return (equity vs last_equity) is scored against SPY's day return and
   the other team — recorded on the scoreboard.
2. Each of the three agents reflects on the day and writes dated lessons into
   its persistent memory (with periodic compaction into a playbook). This is
   the "perpetually learn and get smarter" step: tomorrow's prompts include
   today's lessons.
3. The team writes a DEBRIEF for the human owner: what we did, why we did it,
   what we expected, what we've observed so far, what we learned, and how we
   intend to go forward. It goes into the markdown report and to Discord.
4. Each team then reads its RIVAL's debrief and answers with a rebuttal
   (public, posted to Discord) plus private lessons-from-the-rival that are
   written into its own strategist memory — the teams literally learn from
   what the other did right and wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents import DEBRIEF_SECTIONS, run_reflection, run_team_debrief, run_team_rebuttal
from src.broker import Broker, broker_for_team
from src.config import ROLE_STRATEGIST, ROLES, TEAM_IDS, Settings, TEAM_DISPLAY_NAMES
from src.ledger import read_trades
from src.llm import LLM, LLMError
from src.market_time import ny_trading_date
from src.memory import AgentMemory, load_team_memories
from src.notify import post_discord, report_error
from src.scoreboard import load_scoreboard, record_day, render, totals


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.2f}%"


def run_eod(
    settings: Settings,
    *,
    llm: LLM | None = None,
    brokers: dict[str, Broker] | None = None,
) -> str:
    """Run the end-of-day pass for both teams. Returns the report path."""

    date = ny_trading_date().isoformat()
    llm = llm or LLM(settings)
    brokers = brokers or {team_id: broker_for_team(settings, team_id) for team_id in TEAM_IDS}

    team_returns: dict[str, float | None] = {}
    team_equities: dict[str, float | None] = {}
    team_positions: dict[str, list] = {}
    team_trades: dict[str, list[dict]] = {}
    spy_return: float | None = None

    for team_id in TEAM_IDS:
        broker = brokers[team_id]
        account = broker.account()
        team_returns[team_id] = account.day_return_pct
        team_equities[team_id] = account.equity
        team_positions[team_id] = broker.positions()
        team_trades[team_id] = read_trades(settings.data_dir, team_id, date)
        if spy_return is None:
            spy = broker.snapshots(["SPY"]).get("SPY")
            spy_return = spy.day_change_pct if spy else None

    day = record_day(
        settings.data_dir,
        date=date,
        team_returns=team_returns,
        team_equities=team_equities,
        spy_return=spy_return,
    )

    # --- Reflection + debrief per team ---------------------------------------
    reflection_notes: dict[str, dict[str, list[str]]] = {}
    debriefs: dict[str, dict[str, str] | None] = {}
    day_summaries: dict[str, dict[str, Any]] = {}
    for team_id in TEAM_IDS:
        team = settings.team(team_id)
        memories = load_team_memories(team_id, ROLES, settings.data_dir)
        beat_spy = day["teams"][team_id]["beat_spy"]
        opponent = "team_beta" if team_id == "team_alpha" else "team_alpha"
        day_summary: dict[str, Any] = {
            "date": date,
            "your_day_return": team_returns[team_id],
            "spy_day_return": spy_return,
            "beat_spy_today": beat_spy,
            "opponent_day_return": team_returns[opponent],
            "won_head_to_head": day.get("head_to_head") == team_id,
            "closing_equity": team_equities[team_id],
            "todays_trades": [
                {
                    "symbol": t.get("symbol"), "action": t.get("action"), "qty": t.get("qty"),
                    "est_price": t.get("est_price"), "submitted": t.get("submitted"),
                    "status": t.get("status"), "error": t.get("error"),
                    "thesis": t.get("thesis"), "exit_plan": t.get("exit_plan"),
                }
                for t in team_trades[team_id]
            ],
            "open_positions": [
                {
                    "symbol": p.symbol, "describe": p.describe(), "side": p.side,
                    "qty": p.qty, "unrealized_pl_pct": p.unrealized_plpc,
                }
                for p in team_positions[team_id]
            ],
        }
        day_summaries[team_id] = day_summary

        # 1) Per-agent reflection -> persistent memory.
        reflection_notes[team_id] = {}
        for role in ROLES:
            memory = memories[role]
            try:
                reflection = run_reflection(llm, team, role, memory, day_summary)
            except LLMError as exc:
                report_error(settings, f"{team_id}/{role} reflection", str(exc))
                reflection_notes[team_id][role] = []
                continue
            if memory.needs_compaction and reflection.get("playbook"):
                memory.compact(reflection["playbook"])
            memory.add_lessons(date, reflection.get("lessons", []))
            memory.record_day(beat_spy)
            memory.save()
            reflection_notes[team_id][role] = reflection.get("lessons", [])

        # 2) Team debrief for the human owner.
        try:
            debriefs[team_id] = run_team_debrief(llm, team, day_summary, reflection_notes[team_id])
        except LLMError as exc:
            report_error(settings, f"{team_id} debrief", str(exc))
            debriefs[team_id] = None

    # --- Rival rebuttals: each team learns from the other's day ---------------
    rebuttals: dict[str, dict[str, Any]] = {}
    for team_id in TEAM_IDS:
        opponent = "team_beta" if team_id == "team_alpha" else "team_alpha"
        opponent_debrief = debriefs.get(opponent)
        if not opponent_debrief:
            continue  # nothing to react to today
        team = settings.team(team_id)
        opponent_result = {
            "day_return": team_returns[opponent],
            "beat_spy": day["teams"][opponent]["beat_spy"],
            "trades_submitted": sum(1 for t in team_trades[opponent] if t.get("submitted")),
        }
        try:
            rebuttal = run_team_rebuttal(
                llm, team, TEAM_DISPLAY_NAMES[opponent],
                day_summaries[team_id], opponent_debrief, opponent_result,
            )
        except LLMError as exc:
            report_error(settings, f"{team_id} rebuttal", str(exc))
            continue
        rebuttals[team_id] = rebuttal
        # Private cross-team lessons land in the strategist's memory so
        # tomorrow's decisions carry what the rival did right or wrong.
        lessons = rebuttal.get("lessons_from_rival", [])
        if lessons:
            memory = AgentMemory.load(team_id, ROLE_STRATEGIST, settings.data_dir)
            memory.add_lessons(date, [f"[from rival] {lesson}" for lesson in lessons])
            memory.save()

    # --- Report ---------------------------------------------------------------
    lines: list[str] = [f"# End of day — {date}", ""]
    winner = day.get("head_to_head")
    lines.append(
        f"**SPY:** {_pct(spy_return)} | **Alpha:** {_pct(team_returns['team_alpha'])} | "
        f"**Beta:** {_pct(team_returns['team_beta'])} | "
        f"**Day winner:** {TEAM_DISPLAY_NAMES.get(winner, 'tie') if winner else 'n/a'}"
    )
    lines.append("")

    for team_id in TEAM_IDS:
        display = TEAM_DISPLAY_NAMES[team_id]
        beat = day["teams"][team_id]["beat_spy"]
        beat_text = "beat SPY" if beat else ("lost to SPY" if beat is False else "vs SPY unknown")
        lines.append(f"## {display} — {_pct(team_returns[team_id])} ({beat_text})")
        lines.append("")

        debrief = debriefs.get(team_id)
        if debrief:
            for key, heading in DEBRIEF_SECTIONS:
                lines.append(f"**{heading}:** {debrief[key]}")
                lines.append("")
        else:
            lines.append("*(Team debrief unavailable today — see errors.log.)*")
            lines.append("")

        trades = team_trades[team_id]
        submitted = [t for t in trades if t.get("submitted")]
        lines.append(f"**Orders submitted today: {len(submitted)}**")
        for t in submitted:
            lines.append(
                f"- {t.get('order_side', '?').upper()} {t.get('qty')} {t.get('symbol')} "
                f"— {str(t.get('thesis', ''))[:140]}"
            )
        failed = [t for t in trades if not t.get("submitted")]
        if failed:
            lines.append(f"**Failed submissions: {len(failed)}**")
            for t in failed:
                lines.append(f"- {t.get('symbol')}: {str(t.get('error', ''))[:140]}")
        positions = team_positions[team_id]
        lines.append(f"**Open positions: {len(positions)}**")
        for p in positions:
            pl = f"{p.unrealized_plpc * 100:+.1f}%" if p.unrealized_plpc is not None else "n/a"
            lines.append(f"- {p.side} {p.describe()} ({pl})")
        lines.append("**Agent lessons banked today:**")
        for role in ROLES:
            for lesson in reflection_notes.get(team_id, {}).get(role, []):
                lines.append(f"- [{role}] {lesson}")
        rebuttal = rebuttals.get(team_id)
        if rebuttal and rebuttal.get("rebuttal"):
            opponent = "team_beta" if team_id == "team_alpha" else "team_alpha"
            lines.append(f"**Rebuttal to {TEAM_DISPLAY_NAMES[opponent]}:** {rebuttal['rebuttal']}")
            for lesson in rebuttal.get("lessons_from_rival", []):
                lines.append(f"- [from rival] {lesson}")
        lines.append("")

    lines.append("```")
    lines.append(render(load_scoreboard(settings.data_dir)))
    lines.append("```")

    reports_dir = Path(settings.data_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{date}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    # --- Discord: headline + one debrief post per team -------------------------
    stats = totals(load_scoreboard(settings.data_dir))
    post_discord(
        settings,
        f"**EOD {date}** — SPY {_pct(spy_return)} | Alpha {_pct(team_returns['team_alpha'])} "
        f"({stats['team_alpha']['beat_spy']}W-{stats['team_alpha']['lost_to_spy']}L vs SPY) | "
        f"Beta {_pct(team_returns['team_beta'])} "
        f"({stats['team_beta']['beat_spy']}W-{stats['team_beta']['lost_to_spy']}L vs SPY) | "
        f"day winner: {TEAM_DISPLAY_NAMES.get(winner, 'tie') if winner else 'n/a'}",
    )
    for team_id in TEAM_IDS:
        debrief = debriefs.get(team_id)
        if not debrief:
            continue
        parts = [f"**{TEAM_DISPLAY_NAMES[team_id]} — end-of-day debrief ({date})**"]
        for key, heading in DEBRIEF_SECTIONS:
            parts.append(f"**{heading}:** {debrief[key]}")
        post_discord(settings, "\n".join(parts))
    for team_id in TEAM_IDS:
        rebuttal = rebuttals.get(team_id)
        if rebuttal and rebuttal.get("rebuttal"):
            opponent = "team_beta" if team_id == "team_alpha" else "team_alpha"
            post_discord(
                settings,
                f"**{TEAM_DISPLAY_NAMES[team_id]} responds to {TEAM_DISPLAY_NAMES[opponent]}:** "
                f"{rebuttal['rebuttal']}",
            )

    return str(report_path)
