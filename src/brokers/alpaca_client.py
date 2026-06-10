from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.brokers.order_models import AssetClass, OrderRequest, TradeAction
from src.config.settings import Settings


PAPER_BASE_URL = "https://paper-api.alpaca.markets"


@dataclass
class AlpacaClientWrapper:
    settings: Settings
    client_factory: Callable[[Settings], Any] | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.settings.alpaca_paper is not True:
            raise ValueError("Alpaca paper mode is required. Set ALPACA_PAPER=true.")
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

    def submit_paper_order(self, order_request: OrderRequest) -> Any:
        self._validate_order_request(order_request)
        alpaca_request = self._to_alpaca_order_request(order_request)
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

    def _validate_order_request(self, order_request: OrderRequest) -> None:
        if not isinstance(order_request, OrderRequest):
            raise TypeError("submit_paper_order requires an OrderRequest.")
        if not order_request.risk_approved:
            raise ValueError("OrderRequest must be produced from an approved risk decision.")
        if order_request.dry_run:
            raise ValueError("Dry-run orders must not be submitted to Alpaca paper.")
        if order_request.asset_class != AssetClass.STOCK:
            raise ValueError("Only stock orders may be submitted to Alpaca paper.")

        for field_name in ("option_symbol", "option_contract", "margin", "short"):
            if getattr(order_request, field_name, None):
                raise ValueError(f"Unsupported order field for paper stock trading: {field_name}.")

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
