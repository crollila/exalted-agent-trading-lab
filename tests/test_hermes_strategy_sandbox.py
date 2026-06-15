import json
import os
import subprocess
import sys
from datetime import date, timedelta

from src.agents.hermes_strategy_sandbox import (
    PAPER_ELIGIBLE_STOCK_LONG,
    REJECTED,
    SIMULATION_ONLY_MARGIN,
    SIMULATION_ONLY_OPTION,
    SIMULATION_ONLY_SHORT,
    HermesSandboxResult,
    format_hermes_sandbox_result,
    parse_hermes_sandbox_json,
)
from src.brokers.order_models import AssetClass, TradeAction, TradeProposal
from src.main import run_review_hermes_sandbox
from src.risk.options_models import OptionProposal
from src.risk.shorting_models import ShortProposal


def test_valid_mixed_hermes_sandbox_json_parses_and_routes():
    result = parse_hermes_sandbox_json(json.dumps(_valid_payload()))

    assert result.ok
    assert result.request is not None
    assert result.request.agent_id == "agent-1"
    assert len(result.routed_proposals) == 3
    assert result.route_counts()[PAPER_ELIGIBLE_STOCK_LONG] == 1
    assert result.route_counts()[SIMULATION_ONLY_SHORT] == 1
    assert result.route_counts()[SIMULATION_ONLY_OPTION] == 1
    assert result.route_counts()[REJECTED] == 0


def test_stock_long_routes_to_paper_eligible_trade_proposal():
    result = parse_hermes_sandbox_json(json.dumps(_valid_payload()))
    routed = result.routed_proposals[0]

    assert routed.route == PAPER_ELIGIBLE_STOCK_LONG
    assert isinstance(routed.mapped_proposal, TradeProposal)
    assert routed.mapped_proposal.strategy_id == "sandbox-strategy"
    assert routed.mapped_proposal.symbol == "MSFT"
    assert routed.mapped_proposal.action == TradeAction.BUY
    assert routed.mapped_proposal.asset_class == AssetClass.STOCK


def test_short_routes_to_simulation_only_short_proposal():
    result = parse_hermes_sandbox_json(json.dumps(_valid_payload()))
    routed = result.routed_proposals[1]

    assert routed.route == SIMULATION_ONLY_SHORT
    assert isinstance(routed.mapped_proposal, ShortProposal)
    assert routed.mapped_proposal.symbol == "RISK"


def test_option_routes_to_simulation_only_option_proposal():
    result = parse_hermes_sandbox_json(json.dumps(_valid_payload()))
    routed = result.routed_proposals[2]

    assert routed.route == SIMULATION_ONLY_OPTION
    assert isinstance(routed.mapped_proposal, OptionProposal)
    assert routed.mapped_proposal.contract.underlying_symbol == "SPY"


def test_margin_routes_to_simulation_only_margin_placeholder():
    payload = _valid_payload()
    payload["proposals"] = [
        {
            "proposal_type": "margin",
            "requested_gross_exposure": 1.2,
            "symbols": ["MSFT", "SPY"],
            "thesis": "Sandbox-only margin exposure idea.",
            "confidence": 0.55,
        }
    ]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == SIMULATION_ONLY_MARGIN
    assert result.route_counts()[SIMULATION_ONLY_MARGIN] == 1


def test_phase_7f_structured_proposal_types_route_safely():
    payload = _valid_payload()
    payload["proposals"] = [
        {
            "proposal_type": "stock_short",
            "symbol": "risk",
            "target_short_weight": 0.04,
            "estimated_price": 50,
            "thesis": "Paper short candidate for deterministic risk review.",
            "confidence": 0.6,
            "borrow_available_assumption": True,
        },
        {
            "proposal_type": "stock_margin_long",
            "symbol": "msft",
            "requested_gross_exposure": 1.2,
            "estimated_price": 420.5,
            "thesis": "Margin long candidate for deterministic risk review.",
            "confidence": 0.62,
        },
        {
            "proposal_type": "stock_margin_short",
            "symbol": "weak",
            "requested_gross_exposure": 1.1,
            "estimated_price": 40,
            "thesis": "Margin short candidate for deterministic risk review.",
            "confidence": 0.52,
        },
        _structured_option("option_long_call", "call", "buy_to_open"),
        _structured_option("option_long_put", "put", "buy_to_open"),
        _structured_option("covered_call", "call", "sell_to_open", covered_shares=100),
        _structured_option("cash_secured_put", "put", "sell_to_open", cash_reserved=50000),
    ]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.route_counts()[SIMULATION_ONLY_SHORT] == 1
    assert result.route_counts()[SIMULATION_ONLY_MARGIN] == 2
    assert result.route_counts()[SIMULATION_ONLY_OPTION] == 4
    assert result.route_counts()[REJECTED] == 0


def test_phase_7f_options_reject_zero_dte_and_naked_short_shapes():
    payload = _valid_payload()
    zero_dte = _structured_option("option_long_call", "call", "buy_to_open")
    zero_dte["expiration_date"] = date.today().isoformat()
    uncovered_call = _structured_option("covered_call", "call", "sell_to_open")
    cash_short_put = _structured_option("cash_secured_put", "put", "sell_to_open")
    payload["proposals"] = [zero_dte, uncovered_call, cash_short_put]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.route_counts()[REJECTED] == 3
    assert any("0DTE options are disabled" in error for error in result.routed_proposals[0].errors)
    assert any("covered_shares" in error for error in result.routed_proposals[1].errors)
    assert any("cash_reserved" in error for error in result.routed_proposals[2].errors)


def test_phase_7f_missing_required_symbol_thesis_confidence_is_rejected():
    payload = _valid_payload()
    missing_symbol = {
        "proposal_type": "stock_margin_long",
        "requested_gross_exposure": 1.1,
        "estimated_price": 50,
        "thesis": "Missing symbol.",
        "confidence": 0.5,
    }
    missing_thesis = {
        "proposal_type": "stock_short",
        "symbol": "RISK",
        "target_short_weight": 0.03,
        "estimated_price": 50,
        "confidence": 0.5,
        "borrow_available_assumption": True,
    }
    missing_confidence = _structured_option("option_long_call", "call", "buy_to_open")
    del missing_confidence["confidence"]
    payload["proposals"] = [missing_symbol, missing_thesis, missing_confidence]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.route_counts()[REJECTED] == 3
    assert any("symbol" in error for error in result.routed_proposals[0].errors)
    assert any("thesis" in error for error in result.routed_proposals[1].errors)
    assert any("confidence" in error for error in result.routed_proposals[2].errors)


def test_expired_structured_option_is_rejected():
    payload = _valid_payload()
    expired = _structured_option("option_long_call", "call", "buy_to_open")
    expired["expiration_date"] = (date.today() - timedelta(days=1)).isoformat()
    payload["proposals"] = [expired]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert any("expiration_date must be after today" in error for error in result.routed_proposals[0].errors)


def test_stock_long_missing_thesis_is_rejected():
    payload = _valid_payload()
    missing_thesis = {
        "proposal_type": "stock_long",
        "symbol": "MSFT",
        "target_weight": 0.05,
        "estimated_price": 100,
        "confidence": 0.7,
    }
    payload["proposals"] = [missing_thesis]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert any("thesis" in error for error in result.routed_proposals[0].errors)


def test_cash_secured_put_without_thesis_is_rejected():
    payload = _valid_payload()
    cash_secured_put = _structured_option("cash_secured_put", "put", "sell_to_open", cash_reserved=50000)
    del cash_secured_put["thesis"]
    payload["proposals"] = [cash_secured_put]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert any("thesis" in error for error in result.routed_proposals[0].errors)


def test_covered_call_side_action_consistency_is_enforced():
    payload = _valid_payload()
    covered_call = _structured_option("covered_call", "call", "long", covered_shares=100)
    payload["proposals"] = [covered_call]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert any("covered_call requires side sell_to_open" in error for error in result.routed_proposals[0].errors)


def test_stale_option_long_expiration_is_rejected():
    payload = _valid_payload()
    payload["proposals"] = [
        {
            "proposal_type": "option_long",
            "contract": {
                "underlying_symbol": "SPY",
                "option_type": "call",
                "expiration": (date.today() - timedelta(days=7)).isoformat(),
                "strike": 500,
            },
            "action": "buy_to_open",
            "contracts": 1,
            "premium": 4.25,
            "estimated_total_premium": 425,
            "thesis": "Defined-risk long call for simulation.",
            "confidence": 0.65,
            "liquidity_open_interest_assumption": "Open interest appears sufficient.",
            "assignment_exercise_risk_note": "Long options can expire worthless.",
        }
    ]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert any("expiration" in error for error in result.routed_proposals[0].errors)


def test_stale_or_fake_option_quote_metadata_is_rejected():
    payload = _valid_payload()
    option = _structured_option("option_long_call", "call", "buy_to_open")
    option["fake_current_price"] = 123.45
    option["quote_age_days"] = 14
    payload["proposals"] = [option]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert any("Extra inputs are not permitted" in error for error in result.routed_proposals[0].errors)


def test_blank_optional_text_is_coerced_to_none_not_rejected():
    # Regression: a model that echoes an empty learning_goal/strategy_notes must not fail the
    # whole proposal request (these optional descriptive fields mean "absent" when blank).
    payload = _valid_payload()
    payload["learning_goal"] = ""
    payload["strategy_notes"] = "   "

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.request is not None
    assert result.request.learning_goal is None
    assert result.request.strategy_notes is None
    assert not any("must not be empty" in error for error in result.errors)


def test_stock_long_spy_is_allowed_but_warned_for_beat_spy_goal():
    payload = _valid_payload()
    payload["learning_goal"] = "Try to beat SPY over time."
    payload["proposals"] = [
        {
            "proposal_type": "stock_long",
            "symbol": "SPY",
            "target_weight": 0.05,
            "estimated_price": 500,
            "thesis": "Temporary benchmark-like hedge while waiting for differentiated signals.",
            "confidence": 0.55,
        }
    ]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    routed = result.routed_proposals[0]
    assert routed.route == PAPER_ELIGIBLE_STOCK_LONG
    assert routed.warnings
    assert "benchmark-like" in routed.warnings[0]


def test_unknown_proposal_type_is_rejected():
    payload = _valid_payload()
    payload["proposals"] = [{"proposal_type": "crypto_pair", "symbol": "BTCUSD"}]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert result.routed_proposals[0].route == REJECTED
    assert "Unknown proposal_type" in result.routed_proposals[0].errors[0]


def test_invalid_json_is_rejected():
    result = parse_hermes_sandbox_json("{not json")

    assert not result.ok
    assert result.routed_proposals == []
    assert "Invalid JSON" in result.errors[0]


def test_missing_required_request_field_is_rejected():
    payload = _valid_payload()
    del payload["agent_id"]

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert not result.ok
    assert result.routed_proposals == []
    assert any("agent_id" in error for error in result.errors)


def test_empty_proposals_are_rejected():
    payload = _valid_payload()
    payload["proposals"] = []

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert not result.ok
    assert any("proposals" in error for error in result.errors)


def test_invalid_stock_short_and_option_proposals_are_rejected():
    payload = _valid_payload()
    payload["proposals"][0]["estimated_price"] = 0
    payload["proposals"][1].pop("borrow_available_assumption")
    payload["proposals"][2]["contract"]["expiration"] = date.today().isoformat()

    result = parse_hermes_sandbox_json(json.dumps(payload))

    assert result.ok
    assert [proposal.route for proposal in result.routed_proposals] == [REJECTED, REJECTED, REJECTED]
    assert result.route_counts()[REJECTED] == 3


def test_extra_request_and_proposal_fields_are_rejected():
    payload = _valid_payload()
    payload["broker_access"] = True
    request_result = parse_hermes_sandbox_json(json.dumps(payload))

    payload = _valid_payload()
    payload["proposals"][0]["order_now"] = True
    proposal_result = parse_hermes_sandbox_json(json.dumps(payload))

    assert not request_result.ok
    assert proposal_result.routed_proposals[0].route == REJECTED


def test_cli_review_works_without_credentials():
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
            "review-hermes-sandbox",
            "--file",
            "docs/examples/hermes_strategy_sandbox_example.json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Team ID: team_alpha" in result.stdout
    assert "Agent ID: alpha_research_01" in result.stdout
    assert "Strategy ID: alpha_quality_momentum_v1" in result.stdout
    assert "Hermes proposals are not execution approval" in result.stdout
    assert f"- {PAPER_ELIGIBLE_STOCK_LONG}: 1" in result.stdout
    assert f"- {SIMULATION_ONLY_SHORT}: 1" in result.stdout
    assert f"- {SIMULATION_ONLY_OPTION}: 1" in result.stdout
    assert f"- {REJECTED}: 1" in result.stdout
    assert "Traceback" not in result.stderr


def test_review_command_does_not_call_alpaca_network_llm_or_database(tmp_path, monkeypatch):
    sandbox_file = tmp_path / "hermes.json"
    sandbox_file.write_text(json.dumps(_valid_payload()), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("review-hermes-sandbox must stay local and side-effect free")

    monkeypatch.setattr("src.main.AlpacaClientWrapper", forbidden)
    monkeypatch.setattr("src.main.initialize_database", forbidden)
    monkeypatch.setattr("src.main.Settings.from_env", forbidden)

    run_review_hermes_sandbox(sandbox_file)


def test_format_includes_execution_approval_warning():
    output = format_hermes_sandbox_result(HermesSandboxResult(errors=["Invalid JSON: nope."]))

    assert "Hermes proposals are not execution approval" in output


def _valid_payload():
    return {
        "agent_id": "agent-1",
        "team_id": "team-1",
        "strategy_id": "sandbox-strategy",
        "agent_role": "portfolio strategist",
        "strategy_notes": "Local sandbox review only.",
        "learning_goal": "Route mixed proposals safely.",
        "proposals": [
            {
                "proposal_type": "stock_long",
                "symbol": "msft",
                "target_weight": 0.08,
                "estimated_price": 420.5,
                "thesis": "Positive momentum and strong balance sheet.",
                "confidence": 0.72,
            },
            {
                "proposal_type": "short_stock",
                "symbol": "risk",
                "target_short_weight": 0.04,
                "estimated_price": 50,
                "thesis": "Future paper-short thesis for a weakening stock.",
                "confidence": 0.6,
                "borrow_available_assumption": True,
                "borrow_fee_assumption": 0.02,
                "max_loss_exit_price": 58,
            },
            {
                "proposal_type": "option_long",
                "contract": {
                    "underlying_symbol": "spy",
                    "option_type": "call",
                    "expiration": (date.today() + timedelta(days=45)).isoformat(),
                    "strike": 500,
                    "open_interest": 2500,
                },
                "action": "buy_to_open",
                "contracts": 1,
                "premium": 4.25,
                "estimated_total_premium": 425,
                "thesis": "Defined-risk long call for simulation.",
                "confidence": 0.65,
                "liquidity_open_interest_assumption": "Open interest appears sufficient.",
                "assignment_exercise_risk_note": "Long options can expire worthless.",
            },
        ],
    }


def _structured_option(proposal_type, option_type, side, **overrides):
    value = {
        "proposal_type": proposal_type,
        "underlying_symbol": "SPY",
        "option_type": option_type,
        "strike": 500,
        "expiration_date": (date.today() + timedelta(days=45)).isoformat(),
        "side": side,
        "max_premium": 425,
        "contracts": 1,
        "thesis": "Structured option proposal for local review only.",
        "confidence": 0.6,
    }
    value.update(overrides)
    return value
