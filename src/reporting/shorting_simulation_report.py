from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.risk.shorting_models import ShortProposal
from src.simulation.shorting_simulator import ShortSimulationResult, simulate_short_proposal


DEFAULT_SHORT_SIMULATION_REPORT_PATH = Path("data/reports/shorting_simulation_report.md")


@dataclass(frozen=True)
class ShortSimulationReportExportResult:
    saved: bool
    report_path: Path
    message: str


@dataclass(frozen=True)
class ShortSimulationExample:
    proposal: ShortProposal
    starting_equity: float
    local_prices: tuple[float, ...]


def deterministic_short_simulation_example() -> ShortSimulationExample:
    return ShortSimulationExample(
        proposal=ShortProposal(
            proposal_id="local-short-sim-example-001",
            strategy_id="local_short_simulation_fixture",
            symbol="AAPL",
            action="sell_short",
            target_short_weight=0.10,
            estimated_price=100.0,
            thesis="Deterministic local-only short simulation example.",
            confidence=0.70,
            borrow_available_assumption=True,
            borrow_fee_assumption=0.01,
            forced_cover_threshold=112.0,
        ),
        starting_equity=10_000.0,
        local_prices=(104.0, 113.0, 96.0),
    )


def export_shorting_simulation_report(
    report_path: Path | str = DEFAULT_SHORT_SIMULATION_REPORT_PATH,
    generated_at: datetime | None = None,
) -> ShortSimulationReportExportResult:
    example = deterministic_short_simulation_example()
    simulation = simulate_short_proposal(
        proposal=example.proposal,
        starting_equity=example.starting_equity,
        local_prices=example.local_prices,
    )
    markdown = format_shorting_simulation_report(
        proposal=example.proposal,
        simulation=simulation,
        starting_equity=example.starting_equity,
        local_prices=example.local_prices,
        generated_at=generated_at,
    )
    active_report_path = Path(report_path)
    active_report_path.parent.mkdir(parents=True, exist_ok=True)
    active_report_path.write_text(markdown, encoding="utf-8")
    return ShortSimulationReportExportResult(
        saved=True,
        report_path=active_report_path,
        message=f"Saved simulation only shorting simulation report: {active_report_path}",
    )


def format_shorting_simulation_report(
    proposal: ShortProposal,
    simulation: ShortSimulationResult,
    starting_equity: float,
    local_prices: tuple[float, ...],
    generated_at: datetime | None = None,
) -> str:
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    forced_cover_status = "triggered" if simulation.position.forced_cover_triggered else "not triggered"
    risk_event_status = _risk_event_status(simulation)

    lines = [
        "# Shorting Simulation Report",
        "",
        f"Generated timestamp: {timestamp}",
        "",
        "## Safety Disclaimer",
        "",
        "- Simulation only.",
        "- Executable shorting remains disabled.",
        "- This report uses hardcoded deterministic local fixture inputs only.",
        "- No Alpaca calls, broker calls, order submission, live trading, options, margin, or Hermes runtime wiring.",
        "",
        "## Proposal",
        "",
        f"- Proposal ID: `{proposal.proposal_id}`",
        f"- Strategy ID: `{proposal.strategy_id}`",
        f"- Symbol: `{proposal.symbol}`",
        f"- Action: `{proposal.action.value}`",
        f"- Target short weight: {_optional_percent(proposal.target_short_weight)}",
        f"- Notional exposure: {_optional_currency(proposal.notional_exposure)}",
        f"- Starting equity: {_currency(starting_equity)}",
        f"- Entry price: {_currency(simulation.position.entry_price)}",
        f"- Cover price: {_currency(simulation.position.cover_price)}",
        f"- Local fixture prices: {', '.join(_currency(price) for price in local_prices)}",
        "",
        "## Simulation Metrics",
        "",
        f"- Opening short notional: {_currency(simulation.position.opening_short_notional)}",
        f"- Quantity: {simulation.position.quantity:.4f}",
        f"- Gross exposure: {_percent(simulation.gross_exposure)}",
        f"- Net exposure: {_percent(simulation.net_exposure)}",
        f"- Short exposure: {_percent(simulation.short_exposure)}",
        f"- Gross P/L before fees: {_currency(simulation.position.gross_profit_loss_before_fees)}",
        f"- Borrow fee estimate: {_currency(simulation.position.borrow_fee_estimate)}",
        f"- Realized P/L: {_currency(simulation.position.realized_profit_loss)}",
        f"- Unrealized P/L: {_currency(simulation.position.unrealized_profit_loss)}",
        f"- Forced-cover status: {forced_cover_status}",
        f"- Risk event status: {risk_event_status}",
        "",
        "## Risk Events",
        "",
    ]
    if simulation.risk_events:
        lines.extend(
            f"- {event.event_type}: {event.symbol} at {_currency(event.trigger_price)}. {event.message}"
            for event in simulation.risk_events
        )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Execution State",
            "",
            "- Executable shorting remains disabled.",
            "- This artifact does not change normal dry-run execution.",
            "- This artifact does not change existing risk engine permissions.",
            "",
        ]
    )
    return "\n".join(lines)


def _risk_event_status(simulation: ShortSimulationResult) -> str:
    if not simulation.risk_events:
        return "none"
    return ", ".join(event.event_type for event in simulation.risk_events)


def _currency(value: float) -> str:
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _optional_percent(value: float | None) -> str:
    if value is None:
        return "not provided"
    return _percent(value)


def _optional_currency(value: float | None) -> str:
    if value is None:
        return "not provided"
    return _currency(value)
