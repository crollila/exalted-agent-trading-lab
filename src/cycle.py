"""One trading cycle for one team:

    snapshot -> researcher -> strategist -> risk analyst -> risk engine -> orders

The researcher sees live prices, movers, news, the earnings calendar, and (when
enabled) the live web, and curates the team's own watchlist. The strategist
trades under its self-chosen charter — and may change that charter any cycle.
Every cycle writes a single audit JSON under ``data/cycles/<date>/`` that tells
the complete story. LLM failures are loud: recorded, printed, posted — a failing
provider can never masquerade as "the agents chose not to trade".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agents import (
    ResearchBrief,
    run_researcher,
    run_risk_analyst,
    run_strategist,
)
from src.broker import Broker, broker_for_team
from src.charter import TeamCharter
from src.config import ROLE_RESEARCHER, ROLE_RISK, ROLE_STRATEGIST, ROLES, Settings
from src.earnings import days_to_earnings, render_earnings
from src.kill_switch import read_kill_switch
from src.ledger import record_trade
from src.llm import LLM, LLMError
from src.market_time import now_utc, ny_trading_date
from src.memory import load_team_memories
from src.notify import post_discord, report_error
from src.risk import evaluate_proposals
from src.scoreboard import load_scoreboard, totals
from src.watchlist import TeamWatchlist


@dataclass
class CycleResult:
    team_id: str
    started_at: str
    ok: bool
    skipped_reason: str | None = None
    error: str | None = None
    orders_submitted: int = 0
    orders_rejected: int = 0
    proposals_count: int = 0
    no_trade_reason: str | None = None
    charter_changes: dict = field(default_factory=dict)
    narrative: list[str] = field(default_factory=list)
    audit_path: str | None = None


def _market_context(settings: Settings, broker: Broker, positions, watch_symbols: list[str]) -> dict[str, Any]:
    """Prices + day changes for the team's watchlist and held symbols, plus news,
    movers, and the earnings calendar. Prices are mandatory; the rest degrades
    visibly."""

    held_stock_symbols = [p.symbol for p in positions if not p.is_option]
    held_underlyings = []
    for p in positions:
        if p.is_option:
            from src.broker import parse_occ_symbol

            parsed = parse_occ_symbol(p.symbol)
            if parsed:
                held_underlyings.append(parsed["underlying"])
    symbols = watch_symbols + held_stock_symbols + held_underlyings
    snapshots = broker.snapshots(symbols)
    prices = {
        s.symbol: {
            "price": s.price,
            "prev_close": s.prev_close,
            "day_change_pct": round(s.day_change_pct, 5) if s.day_change_pct is not None else None,
        }
        for s in snapshots.values()
    }

    # News is helpful but optional; prices are mandatory (orders depend on them).
    try:
        news = [
            {
                "source_id": n.source_id,
                "headline": n.headline,
                "summary": n.summary,
                "symbols": n.symbols,
                "published_at": n.published_at,
            }
            for n in broker.news(symbols, limit=settings.news_items_per_cycle)
        ]
    except Exception as exc:  # noqa: BLE001 - degrade visibly, never silently
        print(f"(news unavailable this cycle: {exc})")
        news = [{"source_id": "news_unavailable", "headline": "News feed unavailable this cycle", "summary": str(exc)[:200], "symbols": [], "published_at": None}]

    movers = [
        {"symbol": m.symbol, "percent_change": m.percent_change, "note": m.note}
        for m in broker.movers()
    ]

    try:
        earnings = days_to_earnings(symbols, settings.data_dir)
        earnings_lines = render_earnings(earnings)
    except Exception as exc:  # noqa: BLE001 - degrade visibly
        print(f"(earnings calendar unavailable: {exc})")
        earnings, earnings_lines = {}, [f"earnings calendar unavailable: {exc}"]

    return {
        "prices": prices,
        "news": news,
        "movers": movers,
        "earnings_alerts": earnings_lines,
        "days_to_earnings": {k: v for k, v in earnings.items() if v is not None},
        "snapshots": snapshots,
    }


def _positions_view(positions) -> list[dict[str, Any]]:
    return [
        {
            "symbol": p.symbol,
            "describe": p.describe(),
            "asset_class": p.asset_class,
            "side": p.side,
            "qty": p.qty,
            "avg_entry_price": p.avg_entry_price,
            "current_price": p.current_price,
            "notional": round(p.notional, 2),
            "unrealized_pl_pct": round(p.unrealized_plpc, 5) if p.unrealized_plpc is not None else None,
        }
        for p in positions
    ]


def _competition_view(settings: Settings, team_id: str) -> dict[str, Any]:
    scoreboard = load_scoreboard(settings.data_dir)
    stats = totals(scoreboard)
    opponent = "team_beta" if team_id == "team_alpha" else "team_alpha"
    mine, theirs = stats.get(team_id, {}), stats.get(opponent, {})
    return {
        "your_cumulative_return": mine.get("cum_return"),
        "your_record_vs_spy": f"{mine.get('beat_spy', 0)}W-{mine.get('lost_to_spy', 0)}L",
        "opponent_cumulative_return": theirs.get("cum_return"),
        "opponent_record_vs_spy": f"{theirs.get('beat_spy', 0)}W-{theirs.get('lost_to_spy', 0)}L",
        "goal": "End today ahead of SPY and ahead of the opposing team.",
    }


def _minutes_to_close(clock) -> float | None:
    if clock.next_close is None or not clock.is_open:
        return None
    delta = (clock.next_close - now_utc()).total_seconds() / 60
    return round(max(delta, 0.0), 1)


def run_team_cycle(
    settings: Settings,
    team_id: str,
    *,
    force: bool = False,
    dry_run: bool | None = None,
    broker: Broker | None = None,
    llm: LLM | None = None,
) -> CycleResult:
    """Run one full cycle for one team. ``force`` skips the market-open check;
    ``dry_run`` (or DRY_RUN=true) does everything except submit orders."""

    team = settings.team(team_id)
    dry = settings.dry_run if dry_run is None else dry_run
    result = CycleResult(team_id=team_id, started_at=now_utc().isoformat(), ok=False)
    say = result.narrative.append

    kill_state = read_kill_switch()
    if kill_state.engaged:
        result.skipped_reason = kill_state.describe()
        say(result.skipped_reason)
        return result

    broker = broker or broker_for_team(settings, team_id)
    llm = llm or LLM(settings)

    clock = broker.clock()
    if not clock.is_open and not force and not dry:
        result.skipped_reason = f"market closed (next open: {clock.next_open})"
        say(result.skipped_reason)
        return result

    # --- Ground truth from the broker (read once per cycle) ------------------
    account = broker.account()
    positions = broker.positions()
    orders_today = broker.orders_today()
    charter = TeamCharter.load(team_id, settings.data_dir, settings.risk)
    watchlist = TeamWatchlist.load(team_id, settings.data_dir, settings.watchlist)
    say(
        f"{team.display_name}: equity ${account.equity:,.0f}, cash ${account.cash:,.0f}, "
        f"{len(positions)} position(s), {len(orders_today)} order(s) today | "
        f"charter: pos {charter.max_position_pct:.0%}, gross {charter.max_gross_exposure:.0%}, "
        f"every {charter.cycle_minutes}m, [{', '.join(charter.instruments)}]"
    )

    market = _market_context(settings, broker, positions, watchlist.symbols)
    snapshots = market.pop("snapshots")
    spy_snap = snapshots.get("SPY")
    spy_day = spy_snap.day_change_pct if spy_snap else None
    minutes_left = _minutes_to_close(clock)

    memories = load_team_memories(team_id, ROLES, settings.data_dir)

    audit: dict[str, Any] = {
        "team_id": team_id,
        "started_at": result.started_at,
        "dry_run": dry,
        "account": {
            "equity": account.equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "day_return_pct": account.day_return_pct,
        },
        "charter": {
            "max_position_pct": charter.max_position_pct,
            "max_gross_exposure": charter.max_gross_exposure,
            "cycle_minutes": charter.cycle_minutes,
            "instruments": list(charter.instruments),
            "style": charter.style,
        },
        "watchlist": watchlist.symbols,
        "positions": _positions_view(positions),
        "market_prices": market["prices"],
        "news_count": len(market["news"]),
        "earnings_alerts": market["earnings_alerts"],
        "minutes_to_market_close": minutes_left,
    }

    try:
        # --- Agent 1: researcher --------------------------------------------
        brief = run_researcher(
            llm, team, memories[ROLE_RESEARCHER],
            {
                "prices": market["prices"],
                "movers": market["movers"],
                "news": market["news"],
                "earnings_alerts": market["earnings_alerts"],
                "days_to_earnings": market["days_to_earnings"],
                "your_positions": _positions_view(positions),
                "your_watchlist": watchlist.symbols,
                "spy_day_change_pct": spy_day,
            },
        )
        audit["researcher"] = brief.as_dict()
        say(f"Researcher: {brief.market_view}")
        for idea in brief.ideas:
            say(f"  idea: {idea.get('direction', '?')} {idea.get('symbol', '?')} — {idea.get('note', '')}")
        for alert in market["earnings_alerts"][:5]:
            say(f"  earnings: {alert}")

        # Watchlist edits (validated against the broker; core symbols protected).
        wl_result = watchlist.apply_changes(brief.watchlist_add, brief.watchlist_remove, broker.asset)
        audit["watchlist_changes"] = wl_result
        if wl_result["added"] or wl_result["removed"]:
            say(
                f"Watchlist: +{wl_result['added'] or []} -{wl_result['removed'] or []} "
                f"(now {len(watchlist.symbols)} symbols)"
            )
        for rejected in wl_result["rejected"]:
            say(f"  watchlist rejected: {rejected}")

        # --- Agent 2: strategy developer ------------------------------------
        strategist_context = {
            "account": audit["account"],
            "your_positions": _positions_view(positions),
            "your_day_return_pct_so_far": account.day_return_pct,
            "spy_day_change_pct_so_far": spy_day,
            "minutes_to_market_close": minutes_left,
            "competition": _competition_view(settings, team_id),
            "earnings_alerts": market["earnings_alerts"],
            "platform_hard_caps": {
                "max_position_pct": settings.risk.max_position_pct,
                "max_gross_exposure": settings.risk.max_gross_exposure,
                "max_option_premium_pct_per_trade": settings.risk.max_option_premium_pct,
                "max_total_option_premium_pct": settings.risk.max_total_option_premium_pct,
                "max_proposals_this_cycle": settings.risk.max_proposals_per_cycle,
            },
            "orders_already_submitted_today": len(orders_today),
        }
        strategist = run_strategist(
            llm, team, memories[ROLE_STRATEGIST], brief, strategist_context,
            settings.risk.max_proposals_per_cycle,
            charter_text=charter.render(settings.risk),
        )
        audit["strategist"] = strategist.as_dict()
        result.proposals_count = len(strategist.proposals)
        say(f"Strategist: {strategist.portfolio_view}")
        if not strategist.proposals:
            result.no_trade_reason = strategist.no_trade_reason or "no proposals this cycle"
            say(f"No trades this cycle: {result.no_trade_reason}")

        # --- Agent 3: risk analyst ------------------------------------------
        verdicts = run_risk_analyst(
            llm, team, memories[ROLE_RISK], strategist.proposals,
            {
                "account": audit["account"],
                "your_positions": _positions_view(positions),
                "team_charter": audit["charter"],
                "platform_hard_caps": strategist_context["platform_hard_caps"],
                "minutes_to_market_close": minutes_left,
                "earnings_alerts": market["earnings_alerts"],
                "recent_order_errors": [
                    {"symbol": o.symbol, "status": o.status}
                    for o in orders_today
                    if o.status in ("rejected", "canceled", "cancelled", "failed")
                ],
            },
        )
        audit["risk_verdicts"] = [v.as_dict() for v in verdicts]
        for v in verdicts:
            say(f"Risk analyst on #{v.index}: {v.verdict} — {v.reason}")

    except LLMError as exc:
        # LOUD failure: recorded, printed, logged, posted. Never a silent no-trade.
        result.error = f"AGENT FAILURE: {exc}"
        say(result.error)
        audit["error"] = result.error
        _write_audit(settings.data_dir, team_id, audit, result)
        report_error(settings, f"{team_id} cycle", str(exc))
        return result

    # --- Deterministic risk engine (authoritative) ---------------------------
    def price_of(symbol: str) -> float | None:
        snap = snapshots.get(symbol.upper())
        return snap.price if snap else None

    decisions = evaluate_proposals(
        strategist.proposals,
        verdicts,
        account=account,
        positions=positions,
        limits=settings.risk,
        charter=charter,
        orders_today=len(orders_today),
        notional_today=broker.notional_submitted_today(price_of),
        price_of=price_of,
        asset_of=broker.asset,
        resolve_option=broker.resolve_option,
    )
    audit["risk_engine"] = [
        {
            "index": d.proposal_index, "symbol": d.symbol, "action": d.action,
            "instrument": d.instrument, "contract": d.contract_desc,
            "approved": d.approved, "qty": d.qty, "est_price": d.est_price,
            "est_notional": d.est_notional, "reason": d.reason_text,
        }
        for d in decisions
    ]

    # --- Execution ------------------------------------------------------------
    executions: list[dict[str, Any]] = []
    for decision in decisions:
        proposal = strategist.proposals[decision.proposal_index]
        label = decision.describe_symbol
        if not decision.approved:
            result.orders_rejected += 1
            say(f"BLOCKED {decision.action} {label}: {decision.reason_text}")
            continue

        if dry:
            say(
                f"DRY-RUN would submit: {decision.order_side} {decision.qty} {label} "
                f"(~${decision.est_notional:,.0f}) — {proposal.thesis[:100]}"
            )
            executions.append({"symbol": decision.symbol, "dry_run": True})
            continue

        order = broker.submit_market_order(decision.symbol, decision.qty, decision.order_side)
        record_trade(
            settings.data_dir,
            team_id,
            symbol=decision.symbol,
            action=decision.action,
            order_side=decision.order_side,
            qty=decision.qty,
            est_price=decision.est_price,
            est_notional=decision.est_notional,
            thesis=proposal.thesis,
            exit_plan=proposal.exit_plan,
            confidence=proposal.confidence,
            submitted=order.submitted,
            order_id=order.order_id,
            status=order.status,
            error=order.error,
        )
        executions.append(
            {
                "symbol": decision.symbol, "side": decision.order_side, "qty": decision.qty,
                "instrument": decision.instrument,
                "submitted": order.submitted, "order_id": order.order_id,
                "status": order.status, "error": order.error,
            }
        )
        if order.submitted:
            result.orders_submitted += 1
            say(
                f"SUBMITTED {decision.order_side.upper()} {decision.qty} {label} "
                f"(~${decision.est_notional:,.0f}) [{proposal.action}] — {proposal.thesis[:120]}"
            )
        else:
            result.orders_rejected += 1
            say(f"BROKER REJECTED {decision.order_side} {decision.qty} {label}: {order.error}")
            report_error(
                settings,
                f"{team_id} order",
                f"broker rejected {decision.order_side} {decision.qty} {label}: {order.error}",
            )

    # --- Charter self-updates (apply AFTER trading; effective next cycle) ------
    if strategist.charter_updates:
        changed = charter.apply_updates(
            strategist.charter_updates, settings.risk,
            strategist.charter_reason or "no reason given",
        )
        if changed:
            charter.save()
            result.charter_changes = {k: list(v) for k, v in changed.items()}
            changes_text = ", ".join(f"{k}: {v[0]} -> {v[1]}" for k, v in changed.items())
            say(f"CHARTER CHANGED: {changes_text} — {strategist.charter_reason}")
            post_discord(
                settings,
                f"**{team.display_name}** changed its charter: {changes_text}\n"
                f"Reason: {strategist.charter_reason or '(none given)'}",
            )
        audit["charter_changes"] = result.charter_changes

    audit["executions"] = executions
    result.ok = True
    _write_audit(settings.data_dir, team_id, audit, result)

    if result.orders_submitted:
        post_discord(
            settings,
            f"**{team.display_name}** submitted {result.orders_submitted} order(s):\n"
            + "\n".join(l for l in result.narrative if l.startswith("SUBMITTED")),
        )
    return result


def _write_audit(data_dir: Path, team_id: str, audit: dict, result: CycleResult) -> None:
    stamp = now_utc().strftime("%H%M%S")
    directory = Path(data_dir) / "cycles" / ny_trading_date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{team_id}_{stamp}.json"
    audit["finished_at"] = now_utc().isoformat()
    path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    result.audit_path = str(path)
