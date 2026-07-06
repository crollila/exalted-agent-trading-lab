"""Alpaca paper-trading wrapper — the only file that talks to the broker.

One ``Broker`` per team. Paper-only is enforced at construction: the trading
client is always created with ``paper=True`` and there is no code path to a
live endpoint. Everything returned is a plain dataclass so the rest of the
system (and the tests) never touch SDK objects.

The kill switch is checked immediately before every order submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from src.kill_switch import assert_clear
from src.market_time import now_utc, ny_session_start_utc


@dataclass(frozen=True)
class AccountInfo:
    equity: float
    last_equity: float  # equity at previous trading-day close (Alpaca-provided)
    cash: float
    buying_power: float

    @property
    def day_return_pct(self) -> float | None:
        if self.last_equity and self.last_equity > 0:
            return (self.equity - self.last_equity) / self.last_equity
        return None


@dataclass(frozen=True)
class PositionInfo:
    symbol: str          # stock ticker OR OCC option symbol
    qty: float           # positive; use side for direction (contracts for options)
    side: str            # "long" | "short"
    avg_entry_price: float
    current_price: float | None
    market_value: float  # signed (negative for shorts)
    unrealized_plpc: float | None  # e.g. 0.031 = +3.1%
    asset_class: str = "us_equity"  # "us_equity" | "us_option"

    @property
    def notional(self) -> float:
        return abs(self.market_value)

    @property
    def is_option(self) -> bool:
        return self.asset_class == "us_option"

    def describe(self) -> str:
        """Human-readable label, expanding OCC option symbols."""

        if self.is_option:
            parsed = parse_occ_symbol(self.symbol)
            if parsed:
                return (
                    f"{parsed['underlying']} {parsed['expiration']} "
                    f"${parsed['strike']:g} {parsed['option_type']} x{self.qty:g}"
                )
        return f"{self.symbol} x{self.qty:g}"


def parse_occ_symbol(occ: str) -> dict | None:
    """Parse an OCC option symbol like ``NVDA260717C00200000``.

    Returns {underlying, expiration (YYYY-MM-DD), option_type, strike} or None.
    """

    occ = (occ or "").strip().upper()
    if len(occ) < 16 or occ[-9] not in ("C", "P") or not occ[-8:].isdigit():
        return None
    root = occ[:-15]
    date_part = occ[-15:-9]
    if not root or not date_part.isdigit():
        return None
    return {
        "underlying": root,
        "expiration": f"20{date_part[0:2]}-{date_part[2:4]}-{date_part[4:6]}",
        "option_type": "call" if occ[-9] == "C" else "put",
        "strike": int(occ[-8:]) / 1000.0,
    }


@dataclass(frozen=True)
class OptionContract:
    occ_symbol: str
    underlying: str
    option_type: str     # "call" | "put"
    strike: float
    expiration: str      # YYYY-MM-DD
    dte: int
    bid: float | None
    ask: float | None


@dataclass(frozen=True)
class MoverInfo:
    symbol: str
    percent_change: float | None
    note: str            # "top gainer" / "top loser" / "most active"


@dataclass(frozen=True)
class ClockInfo:
    is_open: bool
    next_open: datetime | None
    next_close: datetime | None
    timestamp: datetime | None


@dataclass(frozen=True)
class SnapshotInfo:
    symbol: str
    price: float | None          # latest trade
    prev_close: float | None     # previous trading day's close
    day_change_pct: float | None # latest vs prev close


@dataclass(frozen=True)
class NewsItem:
    source_id: str
    headline: str
    summary: str
    symbols: list[str]
    published_at: str | None


@dataclass(frozen=True)
class AssetInfo:
    symbol: str
    tradable: bool
    shortable: bool


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    symbol: str
    side: str            # "buy" | "sell"
    qty: float
    status: str
    filled_avg_price: float | None
    submitted_at: str | None


@dataclass(frozen=True)
class OrderResult:
    submitted: bool
    order_id: str | None
    status: str | None
    error: str | None = None


_TERMINAL_FAILED_STATUSES = {"canceled", "cancelled", "expired", "rejected", "failed"}


@dataclass
class Broker:
    """Read/trade wrapper over one team's Alpaca *paper* account."""

    api_key: str
    secret_key: str
    kill_switch_path: Path | str | None = None
    # Test seams: factories returning objects with the SDK client interfaces.
    trading_client_factory: Callable[[], Any] | None = None
    data_client_factory: Callable[[], Any] | None = None
    news_client_factory: Callable[[], Any] | None = None
    option_client_factory: Callable[[], Any] | None = None
    screener_client_factory: Callable[[], Any] | None = None
    _trading: Any = field(default=None, init=False, repr=False)
    _data: Any = field(default=None, init=False, repr=False)
    _news: Any = field(default=None, init=False, repr=False)
    _option: Any = field(default=None, init=False, repr=False)
    _screener: Any = field(default=None, init=False, repr=False)

    # --- clients -----------------------------------------------------------

    def _trading_client(self) -> Any:
        if self._trading is None:
            if self.trading_client_factory is not None:
                self._trading = self.trading_client_factory()
            else:
                from alpaca.trading.client import TradingClient

                # paper=True is not configurable. This system is paper-only.
                self._trading = TradingClient(self.api_key, self.secret_key, paper=True)
        return self._trading

    def _data_client(self) -> Any:
        if self._data is None:
            if self.data_client_factory is not None:
                self._data = self.data_client_factory()
            else:
                from alpaca.data.historical import StockHistoricalDataClient

                self._data = StockHistoricalDataClient(self.api_key, self.secret_key)
        return self._data

    def _news_client(self) -> Any:
        if self._news is None:
            if self.news_client_factory is not None:
                self._news = self.news_client_factory()
            else:
                from alpaca.data.historical.news import NewsClient

                self._news = NewsClient(api_key=self.api_key, secret_key=self.secret_key)
        return self._news

    def _option_client(self) -> Any:
        if self._option is None:
            if self.option_client_factory is not None:
                self._option = self.option_client_factory()
            else:
                from alpaca.data.historical.option import OptionHistoricalDataClient

                self._option = OptionHistoricalDataClient(
                    api_key=self.api_key, secret_key=self.secret_key
                )
        return self._option

    def _screener_client(self) -> Any:
        if self._screener is None:
            if self.screener_client_factory is not None:
                self._screener = self.screener_client_factory()
            else:
                from alpaca.data.historical.screener import ScreenerClient

                self._screener = ScreenerClient(api_key=self.api_key, secret_key=self.secret_key)
        return self._screener

    # --- reads -------------------------------------------------------------

    def account(self) -> AccountInfo:
        acct = self._trading_client().get_account()
        return AccountInfo(
            equity=float(acct.equity),
            last_equity=float(acct.last_equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
        )

    def positions(self) -> list[PositionInfo]:
        out: list[PositionInfo] = []
        for p in self._trading_client().get_all_positions():
            side = str(getattr(p.side, "value", p.side)).lower()
            side = "short" if "short" in side else "long"
            current = getattr(p, "current_price", None)
            plpc = getattr(p, "unrealized_plpc", None)
            asset_class = str(getattr(getattr(p, "asset_class", ""), "value", getattr(p, "asset_class", ""))).lower()
            out.append(
                PositionInfo(
                    symbol=str(p.symbol).upper(),
                    qty=abs(float(p.qty)),
                    side=side,
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(current) if current is not None else None,
                    market_value=float(p.market_value or 0),
                    unrealized_plpc=float(plpc) if plpc is not None else None,
                    asset_class="us_option" if "option" in asset_class else "us_equity",
                )
            )
        return out

    def clock(self) -> ClockInfo:
        c = self._trading_client().get_clock()
        return ClockInfo(
            is_open=bool(c.is_open),
            next_open=getattr(c, "next_open", None),
            next_close=getattr(c, "next_close", None),
            timestamp=getattr(c, "timestamp", None),
        )

    def calendar_day(self, day: date) -> dict | None:
        """Trading-calendar entry for ``day`` ({date, open, close}) or None."""

        from alpaca.trading.requests import GetCalendarRequest

        entries = self._trading_client().get_calendar(
            GetCalendarRequest(start=day, end=day)
        )
        for entry in entries or []:
            entry_date = getattr(entry, "date", None)
            if entry_date is not None and str(entry_date) == day.isoformat():
                return {
                    "date": str(entry_date),
                    "open": getattr(entry, "open", None),
                    "close": getattr(entry, "close", None),
                }
        return None

    def orders_today(self) -> list[OrderInfo]:
        """All orders submitted since ET midnight (any status)."""

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        request = GetOrdersRequest(
            status=QueryOrderStatus.ALL, after=ny_session_start_utc(), limit=500
        )
        orders = self._trading_client().get_orders(filter=request)
        out: list[OrderInfo] = []
        for o in orders or []:
            filled_price = getattr(o, "filled_avg_price", None)
            submitted_at = getattr(o, "submitted_at", None)
            out.append(
                OrderInfo(
                    order_id=str(o.id),
                    symbol=str(o.symbol).upper(),
                    side=str(getattr(o.side, "value", o.side)).lower(),
                    qty=float(o.qty or 0),
                    status=str(getattr(o.status, "value", o.status)).lower(),
                    filled_avg_price=float(filled_price) if filled_price else None,
                    submitted_at=str(submitted_at) if submitted_at else None,
                )
            )
        return out

    def orders_today_count(self) -> int:
        return len(self.orders_today())

    def notional_submitted_today(self, price_of: Callable[[str], float | None]) -> float:
        """Gross notional of today's non-failed orders (filled price preferred).

        Option orders count at premium x 100 (the contract multiplier).
        """

        total = 0.0
        for order in self.orders_today():
            if order.status in _TERMINAL_FAILED_STATUSES:
                continue
            price = order.filled_avg_price or price_of(order.symbol) or 0.0
            multiplier = 100.0 if parse_occ_symbol(order.symbol) else 1.0
            total += abs(order.qty) * price * multiplier
        return total

    def snapshots(self, symbols: list[str]) -> dict[str, SnapshotInfo]:
        """Latest price + previous close + day change for each symbol, one call."""

        from alpaca.data.requests import StockSnapshotRequest

        wanted = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not wanted:
            return {}
        raw = self._data_client().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=wanted)
        )
        out: dict[str, SnapshotInfo] = {}
        for symbol in wanted:
            snap = raw.get(symbol) if hasattr(raw, "get") else getattr(raw, symbol, None)
            price = prev_close = change = None
            if snap is not None:
                trade = getattr(snap, "latest_trade", None)
                price = float(trade.price) if trade is not None else None
                prev_bar = getattr(snap, "previous_daily_bar", None)
                prev_close = float(prev_bar.close) if prev_bar is not None else None
                if price and prev_close:
                    change = (price - prev_close) / prev_close
            out[symbol] = SnapshotInfo(
                symbol=symbol, price=price, prev_close=prev_close, day_change_pct=change
            )
        return out

    def news(self, symbols: list[str], limit: int = 12, lookback_hours: int = 36) -> list[NewsItem]:
        from alpaca.data.requests import NewsRequest

        wanted = sorted({s.strip().upper() for s in symbols if s.strip()})
        request = NewsRequest(
            symbols=",".join(wanted) if wanted else None,
            start=now_utc() - timedelta(hours=lookback_hours),
            limit=limit,
        )
        news_set = self._news_client().get_news(request)
        items = getattr(news_set, "data", None)
        if isinstance(items, dict):
            items = items.get("news", [])
        out: list[NewsItem] = []
        for index, item in enumerate(items or []):
            def _get(name: str) -> Any:
                return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

            published = _get("created_at") or _get("updated_at")
            out.append(
                NewsItem(
                    source_id=f"news_{index + 1}",
                    headline=str(_get("headline") or "").strip(),
                    summary=str(_get("summary") or "").strip()[:400],
                    symbols=[str(s).upper() for s in (_get("symbols") or [])],
                    published_at=str(published) if published else None,
                )
            )
        return out

    def asset(self, symbol: str) -> AssetInfo | None:
        """Existence/tradability check — rejects hallucinated tickers."""

        try:
            a = self._trading_client().get_asset(symbol.upper())
        except Exception:  # noqa: BLE001 - unknown symbol -> not tradable
            return None
        return AssetInfo(
            symbol=symbol.upper(),
            tradable=bool(getattr(a, "tradable", False)),
            shortable=bool(getattr(a, "shortable", False)),
        )

    def movers(self, top: int = 8) -> list[MoverInfo]:
        """Today's top gainers/losers + most actives (best-effort, may be [])."""

        out: list[MoverInfo] = []
        try:
            from alpaca.data.requests import MarketMoversRequest

            movers = self._screener_client().get_market_movers(MarketMoversRequest(top=top))
            for m in getattr(movers, "gainers", []) or []:
                out.append(MoverInfo(str(m.symbol).upper(), float(m.percent_change), "top gainer"))
            for m in getattr(movers, "losers", []) or []:
                out.append(MoverInfo(str(m.symbol).upper(), float(m.percent_change), "top loser"))
        except Exception as exc:  # noqa: BLE001 - screener is optional context
            print(f"(market movers unavailable: {exc})")
        try:
            from alpaca.data.requests import MostActivesRequest

            actives = self._screener_client().get_most_actives(MostActivesRequest(top=top))
            for m in getattr(actives, "most_actives", []) or []:
                out.append(MoverInfo(str(m.symbol).upper(), None, "most active"))
        except Exception as exc:  # noqa: BLE001
            print(f"(most actives unavailable: {exc})")
        return out

    def resolve_option(
        self,
        underlying: str,
        option_type: str,
        *,
        ref_price: float,
        dte_target: int = 30,
        moneyness: str = "atm",
    ) -> OptionContract | None:
        """Pick a concrete long call/put contract for a strategist's intent.

        Deterministic selection: expiration closest to ``dte_target`` (within
        [dte_target-7, dte_target+21], minimum 3 DTE), then strike closest to
        the moneyness target (ATM, or ~5% OTM). Returns None when no tradable
        contract or no quote is available — the engine then rejects the idea.
        """

        option_type = option_type.lower()
        if option_type not in ("call", "put"):
            return None
        target_strike = ref_price
        if moneyness == "otm":
            target_strike = ref_price * (1.05 if option_type == "call" else 0.95)

        try:
            from alpaca.trading.requests import GetOptionContractsRequest

            today = now_utc().date()
            request = GetOptionContractsRequest(
                underlying_symbols=[underlying.upper()],
                type=option_type,
                expiration_date_gte=today + timedelta(days=max(3, dte_target - 7)),
                expiration_date_lte=today + timedelta(days=dte_target + 21),
                strike_price_gte=str(round(target_strike * 0.85, 2)),
                strike_price_lte=str(round(target_strike * 1.15, 2)),
                limit=200,
            )
            response = self._trading_client().get_option_contracts(request)
            contracts = list(getattr(response, "option_contracts", None) or [])
        except Exception as exc:  # noqa: BLE001 - no chain -> no trade, visibly
            print(f"(option chain unavailable for {underlying}: {exc})")
            return None
        if not contracts:
            return None

        def _expiry(c: Any):
            raw = getattr(c, "expiration_date", None)
            return raw if isinstance(raw, date) else datetime.strptime(str(raw), "%Y-%m-%d").date()

        today = now_utc().date()
        best = min(
            (c for c in contracts if getattr(c, "tradable", True)),
            key=lambda c: (
                abs((_expiry(c) - today).days - dte_target),
                abs(float(c.strike_price) - target_strike),
            ),
            default=None,
        )
        if best is None:
            return None
        occ = str(best.symbol).upper()

        bid = ask = None
        try:
            from alpaca.data.requests import OptionLatestQuoteRequest

            quotes = self._option_client().get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=occ)
            )
            quote = quotes.get(occ) if hasattr(quotes, "get") else getattr(quotes, occ, None)
            if quote is not None:
                bid = float(getattr(quote, "bid_price", 0) or 0) or None
                ask = float(getattr(quote, "ask_price", 0) or 0) or None
        except Exception as exc:  # noqa: BLE001 - no quote -> engine rejects
            print(f"(option quote unavailable for {occ}: {exc})")

        return OptionContract(
            occ_symbol=occ,
            underlying=underlying.upper(),
            option_type=option_type,
            strike=float(best.strike_price),
            expiration=_expiry(best).isoformat(),
            dte=(_expiry(best) - today).days,
            bid=bid,
            ask=ask,
        )

    # --- trading -----------------------------------------------------------

    def submit_market_order(self, symbol: str, qty: int, side: str) -> OrderResult:
        """Submit a whole-share DAY market order. The ONLY order path.

        ``side`` is "buy" or "sell". The kill switch is checked here, right
        before submission. Broker errors are returned, never raised.
        """

        assert_clear(self.kill_switch_path)  # raises if engaged

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if side not in ("buy", "sell"):
            return OrderResult(False, None, None, error=f"invalid side {side!r}")
        if qty < 1:
            return OrderResult(False, None, None, error=f"invalid qty {qty}")

        request = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=int(qty),
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = self._trading_client().submit_order(request)
        except Exception as exc:  # noqa: BLE001 - broker rejection is data, not a crash
            return OrderResult(False, None, None, error=str(exc))
        return OrderResult(
            submitted=True,
            order_id=str(order.id),
            status=str(getattr(order.status, "value", order.status)).lower(),
        )


def broker_for_team(settings, team_id: str) -> Broker:
    """Build the team's broker from settings; raises if credentials missing."""

    team = settings.team(team_id)
    if not team.has_credentials:
        raise RuntimeError(
            f"{team_id} has no Alpaca paper credentials. "
            f"Set {team_id.upper()}_ALPACA_API_KEY and {team_id.upper()}_ALPACA_SECRET_KEY in .env."
        )
    return Broker(api_key=team.alpaca_api_key, secret_key=team.alpaca_secret_key)
