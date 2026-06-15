"""Proves chat / Agent Hub / ask / UI surfaces can never submit broker orders."""

from __future__ import annotations

import inspect

import pytest

import src.discord_bot.bot as bot
import src.ui.competition_view as competition_view
from src.brokers.alpaca_client import AlpacaClientWrapper

BROKER_SUBMIT_METHODS = (
    "submit_paper_order",
    "submit_paper_short_order",
    "submit_paper_margin_order",
    "submit_paper_option_order",
    "submit_order",
)


@pytest.fixture
def broker_tripwire(monkeypatch):
    """Make every broker submission raise, so any accidental submit fails loudly."""

    def boom(*args, **kwargs):
        raise AssertionError("This surface must never submit broker orders.")

    for method in ("submit_paper_order", "submit_paper_short_order",
                   "submit_paper_margin_order", "submit_paper_option_order"):
        monkeypatch.setattr(AlpacaClientWrapper, method, boom, raising=True)
    return boom


# --- structural guards: chat/ask/agent-hub builders contain no broker submit calls ---


def _source_has_no_submit(func) -> bool:
    source = inspect.getsource(func)
    return not any(name in source for name in BROKER_SUBMIT_METHODS)


def test_natural_chat_builder_has_no_broker_submit():
    assert _source_has_no_submit(bot.build_natural_team_chat_summary)
    assert _source_has_no_submit(bot.build_natural_message_response_for_channel)


def test_ask_builders_have_no_broker_submit():
    assert _source_has_no_submit(bot.build_ask_team_summary)
    assert _source_has_no_submit(bot.build_ask_agent_summary)


def test_agent_hub_reply_has_no_broker_submit():
    import src.ui.dashboard as dashboard

    assert _source_has_no_submit(dashboard._agent_hub_reply)


# --- functional guards: competition surfaces wire client=None and never submit ---


def test_discord_run_week_cycle_does_not_submit(broker_tripwire):
    summary = bot.build_run_week_cycle_summary("team_alpha")
    assert "team_alpha" in summary
    assert "no orders submitted" in summary.lower()


def test_discord_status_does_not_submit(broker_tripwire):
    # Should not raise even with broker tripwire armed.
    bot.build_week_competition_status_summary()


class FakeSt:
    def header(self, *a, **k):
        pass

    caption = subheader = text = write = json = info = warning = success = error = header

    def selectbox(self, _label, options, **k):
        return options[0]

    def button(self, *a, **k):
        return True

    def table(self, *a, **k):
        pass


def test_ui_weekly_competition_does_not_submit(broker_tripwire):
    # button() returns True so the gated cycle actually runs; client is None so no submit.
    competition_view.render_weekly_competition(FakeSt(), None)


def test_ui_cannot_bypass_execution_path():
    # The UI page builds no broker client; submission is impossible from the UI.
    source = inspect.getsource(competition_view.render_weekly_competition)
    assert "client=None" in source
