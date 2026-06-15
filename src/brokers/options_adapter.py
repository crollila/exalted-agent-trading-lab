"""Paper options execution adapter.

Wires approved, defined-risk option proposals to Alpaca **paper** as real orders
instead of refusing. Boundaries (all deterministic, never the LLM):

* Single-leg long calls/puts execute by default (well-supported by the SDK).
* Multileg spreads are OFF by default: runtime broker/account support for MLEG is
  uncertain, so spreads are refused with a clear logged reason unless explicitly
  enabled via ``enable_spreads=True``.
* Refused outright (never submitted): 0DTE, naked/uncovered short options,
  unapproved contract quantity, and missing/invalid option contract data.
* Never fakes a fill. It either submits to the real (paper) client, goes through
  an injected ``submit_fn`` (tests), or raises a clear, logged refusal.

Paper-only and team-credential enforcement live in :class:`AlpacaClientWrapper`;
this adapter receives the already-paper-bound trading client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from src.brokers.order_models import AssetClass, OrderRequest, TradeAction


class OptionsAdapterNotConfigured(RuntimeError):
    """Raised when options execution is explicitly disabled."""


class OptionsExecutionRefused(RuntimeError):
    """Raised when an options order is deterministically refused (logged, non-fatal)."""


def build_occ_symbol(underlying: str, expiration: date, option_type: str, strike: float) -> str:
    """Build an OCC option symbol, e.g. SPY + 240920 + C + 00510000."""

    root = underlying.strip().upper()
    if not root:
        raise OptionsExecutionRefused("missing option underlying for OCC symbol")
    cp = "C" if option_type.strip().lower() == "call" else "P"
    strike_int = int(round(float(strike) * 1000))
    if strike_int <= 0:
        raise OptionsExecutionRefused("invalid option strike for OCC symbol")
    return f"{root}{expiration.strftime('%y%m%d')}{cp}{strike_int:08d}"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                return None
    return None


def _legs_from_order(order_request: OrderRequest) -> list[dict[str, Any]]:
    contract = order_request.option_contract or {}
    legs = contract.get("legs") if isinstance(contract, dict) else None
    return [leg for leg in (legs or []) if isinstance(leg, dict)]


def _has_uncovered_short(legs: list[dict[str, Any]]) -> bool:
    def count(side: str, opt: str) -> int:
        return sum(
            1
            for leg in legs
            if str(leg.get("side", "")).lower() == side and str(leg.get("option_type", "")).lower() == opt
        )

    return count("short", "call") > count("long", "call") or count("short", "put") > count("long", "put")


@dataclass
class OptionsExecutionAdapter:
    """Real Alpaca paper options adapter with deterministic safety gates."""

    enabled: bool = True
    enable_spreads: bool = False
    submit_fn: Callable[[OrderRequest, Any], Any] | None = None

    @property
    def configured(self) -> bool:
        return self.enabled

    @property
    def single_leg_enabled(self) -> bool:
        return self.enabled

    @property
    def spreads_enabled(self) -> bool:
        return self.enabled and self.enable_spreads

    def submit(self, order_request: OrderRequest, client: Any) -> Any:
        if order_request.asset_class != AssetClass.OPTION:
            raise ValueError("OptionsExecutionAdapter only handles option orders.")
        if order_request.dry_run:
            raise ValueError("Dry-run options orders must not be submitted.")
        if not order_request.risk_approved:
            raise OptionsExecutionRefused("options order is not from an approved risk decision")
        if not self.enabled:
            raise OptionsAdapterNotConfigured(
                "Options execution adapter is disabled. Paper options were risk-approved but "
                "execution is turned off; refusing to submit (no fake fill)."
            )

        contracts = order_request.contracts or 0
        if contracts < 1:
            raise OptionsExecutionRefused("unapproved option contract quantity (must be >= 1)")

        legs = _legs_from_order(order_request)
        if not legs:
            raise OptionsExecutionRefused("missing option contract legs; cannot build OCC symbol")

        # Deterministic option safety gates (defence in depth alongside the risk engine).
        if _has_uncovered_short(legs):
            raise OptionsExecutionRefused("naked/uncovered short option legs are not allowed")
        for leg in legs:
            exp = _parse_date(leg.get("expiration")) or _parse_date(
                (order_request.option_contract or {}).get("expiration")
            )
            if exp is None:
                raise OptionsExecutionRefused("missing option expiration; cannot build OCC symbol")
            if exp <= date.today():
                raise OptionsExecutionRefused("0DTE (or expired) options are not allowed")

        underlying = order_request.option_symbol or order_request.symbol

        if len(legs) == 1:
            leg = legs[0]
            if str(leg.get("side", "")).lower() != "long":
                raise OptionsExecutionRefused("single short option leg is not allowed (long only)")
            occ = build_occ_symbol(
                underlying,
                _parse_date(leg.get("expiration")),
                str(leg.get("option_type", "call")),
                float(leg.get("strike")),
            )
            print(f"[options] submitting single-leg long {leg.get('option_type')} {occ} x{contracts}")
            if self.submit_fn is not None:
                return self.submit_fn(order_request, client)
            return self._submit_single_leg(client, occ, contracts)

        # Multileg spread.
        if not self.spreads_enabled:
            raise OptionsExecutionRefused(
                "multileg spread execution is disabled (single-leg long only); "
                "routed to refusal. Enable enable_spreads=True once MLEG paper support is verified."
            )
        print(f"[options] submitting multileg spread on {underlying} x{contracts}")
        if self.submit_fn is not None:
            return self.submit_fn(order_request, client)
        return self._submit_multileg(client, underlying, legs, contracts)

    def _submit_single_leg(self, client: Any, occ_symbol: str, contracts: int) -> Any:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        request = MarketOrderRequest(
            symbol=occ_symbol,
            qty=contracts,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        return client.submit_order(request)

    def _submit_multileg(self, client: Any, underlying: str, legs: list[dict[str, Any]], contracts: int) -> Any:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, OptionLegRequest

        leg_requests = []
        for leg in legs:
            occ = build_occ_symbol(
                underlying,
                _parse_date(leg.get("expiration")),
                str(leg.get("option_type", "call")),
                float(leg.get("strike")),
            )
            side = OrderSide.BUY if str(leg.get("side", "")).lower() == "long" else OrderSide.SELL
            leg_requests.append(OptionLegRequest(symbol=occ, side=side, ratio_qty=1))

        request = MarketOrderRequest(
            qty=contracts,
            order_class=OrderClass.MLEG,
            time_in_force=TimeInForce.DAY,
            legs=leg_requests,
        )
        return client.submit_order(request)
