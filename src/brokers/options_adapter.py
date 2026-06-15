"""Options paper execution adapter boundary (Part 6).

Alpaca options paper support is not guaranteed to be available/configured in
every environment. Rather than fake a successful broker order, we expose a clear
adapter boundary:

* By default the adapter is **not configured** and any options submission raises
  :class:`OptionsAdapterNotConfigured` with an explicit message.
* A real or mocked adapter can be injected (tests inject a mock).

This keeps options execution honest: it either goes through a real configured
adapter or it loudly refuses — it never silently pretends to fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.brokers.order_models import AssetClass, OrderRequest


class OptionsAdapterNotConfigured(RuntimeError):
    """Raised when an options order is submitted with no configured adapter."""


@dataclass
class OptionsExecutionAdapter:
    """Boundary around options paper submission.

    ``submit_fn`` is the only thing that can actually place an options order. When
    it is ``None`` the adapter is unconfigured and refuses loudly.
    """

    submit_fn: Callable[[OrderRequest, Any], Any] | None = None

    @property
    def configured(self) -> bool:
        return self.submit_fn is not None

    def submit(self, order_request: OrderRequest, client: Any) -> Any:
        if order_request.asset_class != AssetClass.OPTION:
            raise ValueError("OptionsExecutionAdapter only handles option orders.")
        if not order_request.risk_approved:
            raise ValueError("Options order must come from an approved risk decision.")
        if order_request.dry_run:
            raise ValueError("Dry-run options orders must not be submitted.")
        if self.submit_fn is None:
            raise OptionsAdapterNotConfigured(
                "Options execution adapter is not configured. "
                "Paper options were risk-approved but no broker options adapter is wired; "
                "refusing to submit. Configure an options adapter to enable execution."
            )
        return self.submit_fn(order_request, client)
