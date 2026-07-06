"""Command-line entry point.

    python -m src.main run                      # the forever competition loop
    python -m src.main cycle [--team X] [--force] [--dry-run]
    python -m src.main eod                      # score + learn + report, now
    python -m src.main status                   # accounts, positions, market, memory
    python -m src.main scoreboard               # who is winning
    python -m src.main kill on|off [--reason]   # pause/resume all trading
"""

from __future__ import annotations

import argparse
import sys

from src.config import ROLES, TEAM_DISPLAY_NAMES, TEAM_IDS, Settings
from src.kill_switch import disengage, engage, read_kill_switch
from src.market_time import ny_trading_date


def cmd_run(settings: Settings, _args) -> None:
    from src.loop import run_forever

    run_forever(settings)


def cmd_cycle(settings: Settings, args) -> None:
    from src.cycle import run_team_cycle

    teams = TEAM_IDS if args.team == "both" else (args.team,)
    for team_id in teams:
        result = run_team_cycle(settings, team_id, force=args.force, dry_run=args.dry_run or None)
        print(f"\n=== {team_id} cycle ===")
        for line in result.narrative:
            print(f"  {line}")
        if result.error:
            raise SystemExit(f"Cycle failed: {result.error}")
        if result.audit_path:
            print(f"  (full audit: {result.audit_path})")


def cmd_eod(settings: Settings, _args) -> None:
    from src.eod import run_eod

    report = run_eod(settings)
    print(f"End-of-day pass complete. Report: {report}")


def cmd_status(settings: Settings, _args) -> None:
    from src.broker import broker_for_team
    from src.charter import TeamCharter
    from src.memory import AgentMemory
    from src.notify import recent_errors
    from src.watchlist import TeamWatchlist

    print("=== Exalted Agent Trading Lab — status (paper only) ===")
    print(read_kill_switch().describe())
    print(f"LLM provider: {settings.llm_provider} | default model: {settings.model_default}")
    print(f"Web research: {'on' if settings.enable_web_research else 'off'} | dry_run: {settings.dry_run}")
    print(f"Platform caps (teams cannot exceed): position {settings.risk.max_position_pct:.0%}, "
          f"gross {settings.risk.max_gross_exposure:.0%}, "
          f"{settings.risk.max_orders_per_day} orders/day, "
          f"${settings.risk.max_daily_notional:,.0f} notional/day, "
          f"options premium {settings.risk.max_option_premium_pct:.0%}/trade "
          f"(long calls/puts only)")

    clock_shown = False
    for team_id in TEAM_IDS:
        print(f"\n--- {TEAM_DISPLAY_NAMES[team_id]} ---")
        charter = TeamCharter.load(team_id, settings.data_dir, settings.risk)
        watchlist = TeamWatchlist.load(team_id, settings.data_dir, settings.watchlist)
        print(f"Charter (self-chosen): pos {charter.max_position_pct:.0%} | "
              f"gross {charter.max_gross_exposure:.0%} | every {charter.cycle_minutes} min | "
              f"[{', '.join(charter.instruments)}]")
        print(f"  style: {charter.style[:120]}")
        print(f"Watchlist ({len(watchlist.symbols)}): {', '.join(watchlist.symbols)}")
        try:
            broker = broker_for_team(settings, team_id)
            if not clock_shown:
                clock = broker.clock()
                state = "OPEN" if clock.is_open else f"closed (next open {clock.next_open})"
                print(f"Market: {state}")
                clock_shown = True
            account = broker.account()
            day = account.day_return_pct
            day_text = f"{day * 100:+.2f}%" if day is not None else "n/a"
            print(f"Equity: ${account.equity:,.2f} (today: {day_text}) | "
                  f"cash ${account.cash:,.2f} | buying power ${account.buying_power:,.2f}")
            positions = broker.positions()
            print(f"Positions: {len(positions)}")
            for p in positions:
                pl = f"{p.unrealized_plpc * 100:+.1f}%" if p.unrealized_plpc is not None else "n/a"
                print(f"  {p.side} {p.describe()} @ {p.avg_entry_price:,.2f} ({pl})")
            print(f"Orders today: {len(broker.orders_today())}")
        except Exception as exc:  # noqa: BLE001 - status must show the problem, not crash
            print(f"UNAVAILABLE: {exc}")
        for role in ROLES:
            memory = AgentMemory.load(team_id, role, settings.data_dir)
            print(f"  {role} memory: {len(memory.playbook)} principle(s), "
                  f"{len(memory.lessons)} lesson(s), {memory.days_recorded} day(s) recorded")

    errors = recent_errors(settings, count=5)
    if errors:
        print("\nRecent errors (data/runtime/errors.log):")
        for line in errors:
            print(f"  {line}")
    else:
        print("\nNo errors logged.")


def cmd_scoreboard(settings: Settings, _args) -> None:
    from src.scoreboard import load_scoreboard, render

    print(render(load_scoreboard(settings.data_dir), last_days=15))


def cmd_kill(_settings: Settings, args) -> None:
    if args.state == "on":
        state = engage(args.reason or f"manual stop {ny_trading_date().isoformat()}")
    else:
        state = disengage()
    print(state.describe())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="Two AI teams trade paper accounts daily to beat SPY and each other.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run the forever competition loop")

    cycle = sub.add_parser("cycle", help="run one trading cycle now")
    cycle.add_argument("--team", choices=[*TEAM_IDS, "both"], default="both")
    cycle.add_argument("--force", action="store_true", help="run even if the market is closed")
    cycle.add_argument("--dry-run", action="store_true", help="do everything except submit orders")

    sub.add_parser("eod", help="run the end-of-day scoring + learning pass now")
    sub.add_parser("status", help="accounts, positions, market clock, agent memory")
    sub.add_parser("scoreboard", help="competition scoreboard")

    kill = sub.add_parser("kill", help="engage/disengage the kill switch")
    kill.add_argument("state", choices=["on", "off"])
    kill.add_argument("--reason", default=None)

    return parser


COMMANDS = {
    "run": cmd_run,
    "cycle": cmd_cycle,
    "eod": cmd_eod,
    "status": cmd_status,
    "scoreboard": cmd_scoreboard,
    "kill": cmd_kill,
}


def main(argv: list[str] | None = None) -> None:
    # Windows consoles often default to cp1252; agent narratives are UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    COMMANDS[args.command](settings, args)


if __name__ == "__main__":
    main()
