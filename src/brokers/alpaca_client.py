from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from src.brokers.options_adapter import OptionsExecutionAdapter
from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.config.settings import Settings
from src.safety.kill_switch import assert_clear


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URLS = (
    "https://api.alpaca.markets",
    "http://api.alpaca.markets",
)


@dataclass
class AlpacaClientWrapper:
    settings: Settings
    client_factory: Callable[[Settings], Any] | None = None
    kill_switch_path: Path | str | None = None
    options_adapter: OptionsExecutionAdapter | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.settings.alpaca_paper is not True:
            raise ValueError("Alpaca paper mode is required. Set ALPACA_PAPER=true.")
        if self.settings.alpaca_base_url in LIVE_BASE_URLS:
            raise ValueError(
                "Live Alpaca endpoint is not allowed. This system is paper-only; "
                f"base URL must be exactly {PAPER_BASE_URL}."
            )
        if self.settings.alpaca_base_url != PAPER_BASE_URL:
            raise ValueError(f"Alpaca base URL must be exactly {PAPER_BASE_URL}.")

    def has_credentials(self) -> bool:
        return bool(self.settings.alpaca_api_key and self.settings.alpaca_secret_key)

    def get_account(self) -> Any:
        return self._get_client().get_account()

    def get_positions(self) -> list[Any]:
        return list(self._get_client().get_all_positions())

    def is_market_open(self) -> bool:
        clock = self._get_client().get_clock()
        return bool(clock.is_open)

    def get_clock_snapshot(self) -> dict[str, Any]:
        """Read-only market clock snapshot: is_open + next open/close as ISO strings.

        Never submits anything. Used by the loop diagnostic; degrades fields to
        ``None`` when the SDK does not expose them.
        """

        clock = self._get_client().get_clock()

        def _iso(value: Any) -> str | None:
            if value is None:
                return None
            iso = getattr(value, "isoformat", None)
            return iso() if callable(iso) else str(value)

        return {
            "is_open": bool(getattr(clock, "is_open", False)),
            "timestamp": _iso(getattr(clock, "timestamp", None)),
            "next_open": _iso(getattr(clock, "next_open", None)),
            "next_close": _iso(getattr(clock, "next_close", None)),
        }

    def get_calendar_day(self, day: "date") -> dict[str, Any] | None:
        """Read-only Alpaca trading-calendar entry for ``day`` (None on non-trading days).

        Returns ``{"date","open","close"}`` ISO strings for a trading day, or
        ``None`` for weekends/holidays. Used to gate the end-of-day report so it
        never fires on non-trading days or pre-open. Never submits.
        """

        from alpaca.trading.requests import GetCalendarRequest

        entries = self._get_client().get_calendar(GetCalendarRequest(start=day, end=day))
        for entry in (entries or []):
            entry_date = getattr(entry, "date", None)
            if entry_date is None:
                continue
            iso_date = entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
            if iso_date != day.isoformat():
                continue

            def _iso(v: Any) -> str | None:
                if v is None:
                    return None
                return v.isoformat() if hasattr(v, "isoformat") else str(v)

            return {"date": iso_date, "open": _iso(getattr(entry, "open", None)),
                    "close": _iso(getattr(entry, "close", None))}
        return None

    def count_orders_since(self, after: "datetime") -> int:
        """Count broker orders submitted at/after ``after`` (read-only).

        Used to reconcile the per-team daily-order cap against actual paper
        orders for the current trading date. Never submits; on any SDK/runtime
        failure the caller is expected to degrade safely.
        """

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        request = GetOrdersRequest(status=QueryOrderStatus.ALL, after=after, limit=500)
        orders = self._get_client().get_orders(filter=request)
        return len(list(orders or []))

    def daily_notional_since(self, after: "datetime") -> float:
        """Sum gross submitted-order notional at/after ``after`` (read-only).

        Reconciles the per-team daily-notional cap from actual paper orders.
        Excludes rejected/cancelled/expired/failed orders. Never submits.
        """

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        # Order-status filtering + notional math live in the competition layer so
        # they stay broker-credential-free and unit-testable (lazy import avoids a
        # module-load cycle).
        from src.competition.daily_notional import daily_notional_from_orders

        request = GetOrdersRequest(status=QueryOrderStatus.ALL, after=after, limit=500)
        orders = self._get_client().get_orders(filter=request)
        return float(daily_notional_from_orders(list(orders or [])))

    def submit_paper_order(self, order_request: OrderRequest) -> Any:
        """Submit a paper long stock order (existing Level 1 path)."""

        self._validate_order_request(order_request)
        return self._guarded_submit_stock(order_request, label="paper long stock")

    def submit_paper_sell_to_close_order(self, order_request: OrderRequest) -> Any:
        """Submit an approved reduce-only SELL that closes/reduces a long stock position.

        This is the ONLY broker path that may reduce an existing long. It refuses
        anything that could open or increase a short (``short``/``margin``/option
        fields), and the deterministic gate that produced ``order_request`` has
        already capped the quantity to the currently-held long shares. No shorting,
        options, margin, or live trading is involved.
        """

        self._validate_sell_to_close_order(order_request)
        return self._guarded_submit_stock(order_request, label="paper sell-to-close long")

    def submit_paper_short_order(self, order_request: OrderRequest) -> Any:
        """Submit an approved paper short stock order (Level 2)."""

        self._validate_short_order(order_request)
        return self._guarded_submit_stock(order_request, label="paper short stock")

    def submit_paper_margin_order(self, order_request: OrderRequest) -> Any:
        """Submit an approved paper margin stock order (Level 3)."""

        self._validate_margin_order(order_request)
        return self._guarded_submit_stock(order_request, label="paper margin stock")

    def submit_paper_option_order(self, order_request: OrderRequest) -> Any:
        """Submit an approved paper options order (Level 4) via the adapter boundary.

        If no options adapter is configured this raises a clear runtime error
        rather than faking a fill.
        """

        self._validate_option_order(order_request)
        assert_clear(self.kill_switch_path)
        adapter = self.options_adapter or OptionsExecutionAdapter()
        print(f"[broker] attempting paper options submission: {order_request.option_symbol or order_request.symbol}")
        if not adapter.configured:
            print("[broker] options execution adapter not configured; refusing to submit (no fake fill).")
        return adapter.submit(order_request, self._get_client())

    def _guarded_submit_stock(self, order_request: OrderRequest, *, label: str) -> Any:
        # Kill switch is checked immediately before submission.
        assert_clear(self.kill_switch_path)
        alpaca_request = self._to_alpaca_order_request(order_request)
        print(f"[broker] attempting {label} submission: {order_request.action.value} {order_request.symbol}")
        return self._get_client().submit_order(alpaca_request)

    def _get_client(self) -> Any:
        if not self.has_credentials():
            raise RuntimeError("Missing Alpaca paper credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY.")
        if self._client is None:
            factory = self.client_factory or self._default_client_factory
            self._client = factory(self.settings)
        return self._client

    def _default_client_factory(self, settings: Settings) -> Any:
        from alpaca.trading.client import TradingClient

        return TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=True,
        )

    def _validate_common_paper_order(self, order_request: OrderRequest) -> None:
        if not isinstance(order_request, OrderRequest):
            raise TypeError("submit requires an OrderRequest.")
        if not order_request.risk_approved:
            raise ValueError("OrderRequest must be produced from an approved risk decision.")
        if order_request.dry_run:
            raise ValueError("Dry-run orders must not be submitted to Alpaca paper.")
        if self.settings.alpaca_paper is not True:
            raise ValueError("Refusing to submit: Alpaca paper mode is required.")

    def _validate_order_request(self, order_request: OrderRequest) -> None:
        self._validate_common_paper_order(order_request)
        if order_request.asset_class != AssetClass.STOCK:
            raise ValueError("Only stock orders may be submitted to the long stock path.")
        if order_request.action != TradeAction.BUY:
            raise ValueError("Paper long stock orders must use the BUY action.")
        for field_name in ("option_symbol", "option_contract", "margin", "short", "sell_to_close"):
            if getattr(order_request, field_name, None):
                raise ValueError(f"Unsupported order field for paper long stock trading: {field_name}.")

    def _validate_sell_to_close_order(self, order_request: OrderRequest) -> None:
        self._validate_common_paper_order(order_request)
        if order_request.asset_class != AssetClass.STOCK:
            raise ValueError("Sell-to-close orders must be stock orders.")
        if not order_request.sell_to_close:
            raise ValueError("Sell-to-close path requires order_request.sell_to_close=True.")
        if order_request.action != TradeAction.SELL:
            raise ValueError("Sell-to-close orders must use the SELL action.")
        if order_request.short:
            raise ValueError("Sell-to-close must not set short=True (it can never open/increase a short).")
        if order_request.margin:
            raise ValueError("Sell-to-close must not set margin=True.")
        if order_request.option_symbol or order_request.option_contract:
            raise ValueError("Option fields are not valid on a sell-to-close stock order.")

    def _validate_short_order(self, order_request: OrderRequest) -> None:
        self._validate_common_paper_order(order_request)
        if order_request.asset_class != AssetClass.STOCK:
            raise ValueError("Paper short orders must be stock orders.")
        if not order_request.short:
            raise ValueError("Short order path requires order_request.short=True.")
        if order_request.action != TradeAction.SELL:
            raise ValueError("Paper short orders must use the SELL action.")
        if order_request.option_symbol or order_request.option_contract:
            raise ValueError("Option fields are not valid on a short stock order.")

    def _validate_margin_order(self, order_request: OrderRequest) -> None:
        self._validate_common_paper_order(order_request)
        if order_request.asset_class != AssetClass.STOCK:
            raise ValueError("Paper margin orders must be stock orders.")
        if not order_request.margin:
            raise ValueError("Margin order path requires order_request.margin=True.")
        if order_request.option_symbol or order_request.option_contract:
            raise ValueError("Option fields are not valid on a margin stock order.")

    def _validate_option_order(self, order_request: OrderRequest) -> None:
        self._validate_common_paper_order(order_request)
        if order_request.asset_class != AssetClass.OPTION:
            raise ValueError("Paper option orders must use asset_class=OPTION.")

    def _to_alpaca_order_request(self, order_request: OrderRequest) -> Any:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if order_request.action == TradeAction.BUY else OrderSide.SELL
        common_args = {
            "symbol": order_request.symbol,
            "qty": order_request.quantity,
            "side": side,
            "time_in_force": TimeInForce.DAY,
        }

        if order_request.order_type == "market":
            return MarketOrderRequest(**common_args)

        if order_request.limit_price is None:
            raise ValueError("Limit orders require limit_price.")
        return LimitOrderRequest(**common_args, limit_price=order_request.limit_price)
