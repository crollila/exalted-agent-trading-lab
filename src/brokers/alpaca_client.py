from __future__ import annotations

from dataclasses import dataclass

from src.config.settings import Settings


@dataclass
class AlpacaClientWrapper:
    settings: Settings

    def __post_init__(self) -> None:
        if not self.settings.alpaca_paper:
            raise ValueError("Live Alpaca trading is disabled. ALPACA_PAPER must be true.")

    def has_credentials(self) -> bool:
        return bool(self.settings.alpaca_api_key and self.settings.alpaca_secret_key)

    def get_account(self):
        if not self.has_credentials():
            raise RuntimeError("Missing Alpaca paper credentials.")
        # TODO Phase 2: instantiate alpaca-py TradingClient and call get_account().
        raise NotImplementedError("Alpaca account fetch will be implemented in Phase 2.")

    def submit_order(self, order_request):
        if not self.has_credentials():
            raise RuntimeError("Missing Alpaca paper credentials.")
        # TODO Phase 2: convert internal OrderRequest to alpaca-py request and submit.
        raise NotImplementedError("Alpaca paper order submission will be implemented in Phase 2.")
