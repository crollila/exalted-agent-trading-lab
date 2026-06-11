import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.reporting.research_decisions import (
    read_research_decision_ledger,
    record_research_decision,
)


def test_research_decision_creates_new_ledger(tmp_path):
    ledger_path = tmp_path / "notes" / "research_decisions.md"

    result = record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Won fixture but needs more scenarios",
        ledger_path=ledger_path,
        decision_timestamp=datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc),
    )

    assert result.saved
    assert result.ledger_path == ledger_path
    assert ledger_path.exists()
    markdown = ledger_path.read_text(encoding="utf-8")
    assert "# Research Decision Ledger" in markdown
    assert "Decision timestamp: 2026-06-11T03:00:00+00:00" in markdown
    assert "Strategy ID: `momentum_v1`" in markdown
    assert "Decision: `retest`" in markdown
    assert "Reason: Won fixture but needs more scenarios" in markdown


def test_research_decision_appends_multiple_decisions(tmp_path):
    ledger_path = tmp_path / "research_decisions.md"

    record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Needs more fixtures",
        ledger_path=ledger_path,
        decision_timestamp=datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc),
    )
    record_research_decision(
        strategy_id="spy_buy_hold",
        decision="modify",
        reason="Baseline labeling needs review",
        ledger_path=ledger_path,
        decision_timestamp=datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc),
    )

    markdown = ledger_path.read_text(encoding="utf-8")
    assert markdown.count("## Decision -") == 2
    assert "Strategy ID: `momentum_v1`" in markdown
    assert "Strategy ID: `spy_buy_hold`" in markdown


@pytest.mark.parametrize("decision", ["promote", "modify", "retest", "retire", "no_decision"])
def test_research_decision_accepts_allowed_decision_values(tmp_path, decision):
    result = record_research_decision(
        strategy_id="momentum_v1",
        decision=decision,
        reason="Human review complete",
        ledger_path=tmp_path / f"{decision}.md",
    )

    assert result.saved


def test_research_decision_rejects_invalid_decision_value(tmp_path):
    with pytest.raises(ValueError, match="decision must be one of"):
        record_research_decision(
            strategy_id="momentum_v1",
            decision="buy_live",
            reason="Not allowed",
            ledger_path=tmp_path / "research_decisions.md",
        )


def test_research_decision_rejects_missing_required_fields(tmp_path):
    with pytest.raises(ValueError, match="strategy ID is required"):
        record_research_decision(
            strategy_id=" ",
            decision="retest",
            reason="Needs more fixtures",
            ledger_path=tmp_path / "research_decisions.md",
        )

    with pytest.raises(ValueError, match="reason is required"):
        record_research_decision(
            strategy_id="momentum_v1",
            decision="retest",
            reason=" ",
            ledger_path=tmp_path / "research_decisions.md",
        )


def test_research_decision_includes_optional_source_note_path(tmp_path):
    ledger_path = tmp_path / "research_decisions.md"
    source_note = tmp_path / "notes" / "analysis_note.md"

    record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Needs more fixtures",
        ledger_path=ledger_path,
        source_note=source_note,
    )

    markdown = ledger_path.read_text(encoding="utf-8")
    assert f"Source note path: `{source_note}`" in markdown


def test_research_decision_includes_optional_next_action(tmp_path):
    ledger_path = tmp_path / "research_decisions.md"

    record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Needs more fixtures",
        ledger_path=ledger_path,
        next_action="Run more fixtures",
    )

    markdown = ledger_path.read_text(encoding="utf-8")
    assert "Next action: Run more fixtures" in markdown


def test_research_decision_includes_safety_reminder(tmp_path):
    ledger_path = tmp_path / "research_decisions.md"

    record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Needs more fixtures",
        ledger_path=ledger_path,
    )

    markdown = ledger_path.read_text(encoding="utf-8")
    assert "Safety reminder:" in markdown
    assert "Research decision only." in markdown
    assert "Not live trading approval." in markdown
    assert "No broker/order behavior changed." in markdown


def test_research_decisions_read_existing_ledger(tmp_path):
    ledger_path = tmp_path / "research_decisions.md"
    record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Needs more fixtures",
        ledger_path=ledger_path,
    )

    result = read_research_decision_ledger(ledger_path=ledger_path)

    assert result.ledger_path == ledger_path
    assert "# Research Decision Ledger" in result.message
    assert "momentum_v1" in result.message


def test_research_decisions_read_missing_ledger(tmp_path):
    ledger_path = tmp_path / "missing.md"

    result = read_research_decision_ledger(ledger_path=ledger_path)

    assert f"No research decision ledger found at {ledger_path}." == result.message


def test_research_decision_cli_output_includes_saved_ledger_path(tmp_path):
    ledger_path = tmp_path / "notes" / "research_decisions.md"
    env = os.environ.copy()
    env.pop("ALPACA_API_KEY", None)
    env.pop("ALPACA_SECRET_KEY", None)
    env.pop("HERMES_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "record-research-decision",
            "--strategy-id",
            "momentum_v1",
            "--decision",
            "retest",
            "--reason",
            "Won fixture but needs more scenarios",
            "--next-action",
            "Run more fixtures",
            "--ledger-path",
            str(ledger_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert f"Saved research decision ledger: {ledger_path}" in result.stdout
    assert ledger_path.exists()
    assert "Traceback" not in result.stderr


def test_research_decision_cli_rejects_invalid_decision_value(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "record-research-decision",
            "--strategy-id",
            "momentum_v1",
            "--decision",
            "live_trade",
            "--reason",
            "Not allowed",
            "--ledger-path",
            str(tmp_path / "research_decisions.md"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_research_decision_cli_missing_required_field_shows_help(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "record-research-decision",
            "--strategy-id",
            "momentum_v1",
            "--decision",
            "retest",
            "--ledger-path",
            str(tmp_path / "research_decisions.md"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "usage:" in result.stderr
    assert "the following arguments are required: --reason" in result.stderr


def test_research_decisions_cli_prints_existing_ledger(tmp_path):
    ledger_path = tmp_path / "research_decisions.md"
    record_research_decision(
        strategy_id="momentum_v1",
        decision="retest",
        reason="Needs more fixtures",
        ledger_path=ledger_path,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "research-decisions",
            "--ledger-path",
            str(ledger_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "# Research Decision Ledger" in result.stdout
    assert "momentum_v1" in result.stdout
    assert "Traceback" not in result.stderr
