import os
import subprocess
import sys
from datetime import datetime, timezone

from src.reporting.shorting_simulation_report import (
    deterministic_short_simulation_example,
    export_shorting_simulation_report,
    format_shorting_simulation_report,
)
from src.simulation.shorting_simulator import simulate_short_proposal


def test_shorting_simulation_report_includes_simulation_only_disclaimer():
    markdown = _report_markdown()

    assert "Simulation only" in markdown
    assert "Executable shorting remains disabled" in markdown
    assert "hardcoded deterministic local fixture inputs only" in markdown


def test_shorting_simulation_report_includes_key_shorting_metrics():
    markdown = _report_markdown()

    assert "Symbol: `AAPL`" in markdown
    assert "Action: `sell_short`" in markdown
    assert "Target short weight: 10.00%" in markdown
    assert "Entry price: $100.00" in markdown
    assert "Cover price: $113.00" in markdown
    assert "Gross exposure: 10.00%" in markdown
    assert "Net exposure: -10.00%" in markdown
    assert "Short exposure: 10.00%" in markdown
    assert "Gross P/L before fees: -$130.00" in markdown
    assert "Borrow fee estimate: $10.00" in markdown
    assert "Realized P/L: -$140.00" in markdown
    assert "Unrealized P/L: -$130.00" in markdown


def test_shorting_simulation_report_includes_forced_cover_status():
    markdown = _report_markdown()

    assert "Forced-cover status: triggered" in markdown
    assert "Risk event status: forced_cover" in markdown
    assert "forced_cover: AAPL at $113.00" in markdown


def test_shorting_simulation_report_export_creates_output_directory(tmp_path):
    report_path = tmp_path / "missing" / "nested" / "short_report.md"

    result = export_shorting_simulation_report(report_path=report_path)

    assert result.saved
    assert report_path.exists()
    assert "Saved simulation only shorting simulation report" in result.message


def test_shorting_simulation_report_does_not_require_credentials(tmp_path):
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    report_path = tmp_path / "reports" / "short_report.md"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "export-short-simulation-report",
            "--report-path",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "simulation only" in result.stdout
    assert f"Saved simulation only shorting simulation report: {report_path}" in result.stdout
    assert report_path.exists()
    assert "Traceback" not in result.stderr


def _report_markdown():
    example = deterministic_short_simulation_example()
    simulation = simulate_short_proposal(
        proposal=example.proposal,
        starting_equity=example.starting_equity,
        local_prices=example.local_prices,
    )
    return format_shorting_simulation_report(
        proposal=example.proposal,
        simulation=simulation,
        starting_equity=example.starting_equity,
        local_prices=example.local_prices,
        generated_at=datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
    )
