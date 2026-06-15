"""Angel One SmartAPI broker adapter.

The SmartConnect SDK and the websocket client are imported lazily (inside
methods) so this module loads — and the unit tests run — without the SDK or
any network. Inject a `transport` for tests; in production it defaults to the
real SmartConnect.

Assumptions (stated per the autonomy mandate; verify against current SmartAPI
docs at deploy time — they drift):
- auth: SmartConnect.generateSession(client_code, mpin, totp) -> jwt/refresh;
  generateToken(refresh) to refresh. TOTP from ANGEL_TOTP_SECRET via pyotp.
- order: placeOrder(variety, tradingsymbol, symboltoken, transactiontype,
  exchange, ordertype, producttype, quantity, price). ordertag carries our
  client_order_id for postback correlation.
- rate limits: order ~10/s, historical ~3/s (token buckets enforce).
- MPP: market orders on some segments convert to a protected LIMIT; we treat
  any fill price as ground truth from the fill/postback, never assume.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

from quantsys.core.types import (
    ExecutionStyle,
    Instrument,
    InstrumentKind,
    Urgency,
)
from quantsys.execution.broker import (
    Broker,
    BrokerError,
    BrokerOrder,
    BrokerPosition,
    OrderAck,
    OrderStatus,
)
from quantsys.execution.ratelimit import TokenBucket

log = logging.getLogger("quantsys.angelone")

_STATUS_MAP = {
    "open": OrderStatus.SUBMITTED, "open pending": OrderStatus.SUBMITTED,
    "trigger pending": OrderStatus.SUBMITTED, "validation pending": OrderStatus.SUBMITTED,
    "put order req received": OrderStatus.SUBMITTED,
    "complete": OrderStatus.FILLED, "executed": OrderStatus.FILLED,
    "partially executed": OrderStatus.PARTIAL,
    "cancelled": OrderStatus.CANCELLED, "rejected": OrderStatus.REJECTED,
}

_EXCHANGE = {InstrumentKind.EQUITY: "NSE", InstrumentKind.FUTURE: "NFO",
             InstrumentKind.OPTION: "NFO", InstrumentKind.INDEX: "NSE"}

# Angel One getCandleData max days per request, by interval (their published
# limits). We chunk a long backfill into windows no larger than these.
_HIST_MAX_DAYS = {
    "ONE_MINUTE": 30, "THREE_MINUTE": 60, "FIVE_MINUTE": 100,
    "TEN_MINUTE": 100, "FIFTEEN_MINUTE": 200, "THIRTY_MINUTE": 200,
    "ONE_HOUR": 400, "ONE_DAY": 2000,
}


class AngelOneBroker:
    """Implements the Broker protocol against SmartAPI."""

    def __init__(self, *, api_key: str | None = None, client_code: str | None = None,
                 mpin: str | None = None, totp_secret: str | None = None,
                 transport=None, instrument_master=None):
        self.api_key = api_key or os.environ.get("ANGEL_API_KEY", "")
        self.client_code = client_code or os.environ.get("ANGEL_CLIENT_CODE", "")
        self.mpin = mpin or os.environ.get("ANGEL_PASSWORD", "")
        self.totp_secret = totp_secret or os.environ.get("ANGEL_TOTP_SECRET", "")
        self._transport = transport            # injected SmartConnect-like object
        self._instr_master = instrument_master  # injected for tests
        self._connected = False
        self._refresh_token = ""
        self._feed_token = ""
        self._instruments: dict[str, Instrument] = {}
        self._token_to_symbol: dict[str, str] = {}
        self._orders = TokenBucket(10, 10, "orders")
        self._hist = TokenBucket(3, 3, "historical")
        self._generic = TokenBucket(3, 3, "generic")
        self._client_to_broker: dict[str, str] = {}

    # ----------------------------------------------------------- connect
    def _build_transport(self):
        if self._transport is not None:
            return self._transport
        try:
            from SmartApi import SmartConnect  # type: ignore
        except Exception as e:  # pragma: no cover - prod-only path
            raise BrokerError(
                "smartapi-python not installed; pip install smartapi-python", )
        self._transport = SmartConnect(api_key=self.api_key)
        return self._transport

    def _login(self) -> None:
        """Authenticate (TOTP) and capture fresh access/refresh/feed tokens.
        Does NOT pull the instrument master — that is connect()'s job; a feed
        reconnect re-logins without re-pulling the universe (see
        reconnect_feed_session)."""
        import pyotp

        t = self._build_transport()
        if not (self.client_code and self.mpin and self.totp_secret):
            raise BrokerError("Angel One credentials missing in environment")
        code = pyotp.TOTP(self.totp_secret).now()
        self._generic.acquire(timeout=10)
        try:
            data = t.generateSession(self.client_code, self.mpin, code)
        except Exception as e:  # pragma: no cover
            raise BrokerError(f"generateSession failed: {e}", retryable=True)
        if not data or not data.get("status", True):
            raise BrokerError(f"auth rejected: {data}")
        self._refresh_token = (data.get("data") or {}).get("refreshToken", "")
        try:
            self._feed_token = t.getfeedToken()
        except Exception:
            self._feed_token = ""
        self._connected = True

    def connect(self) -> None:
        self._login()
        self.refresh_instruments()
        log.info("Angel One connected; %d instruments", len(self._instruments))

    def reconnect_feed_session(self) -> dict:
        """Re-login for fresh websocket tokens after a feed drop, WITHOUT
        re-pulling the instrument master (the universe is already loaded).
        Friday's token is dead by Monday, so the feed supervisor calls this
        before each reconnect. Returns the creds the websocket needs."""
        self._login()
        return {
            "auth_token": getattr(self._transport, "access_token", ""),
            "feed_token": self._feed_token,
            "api_key": self.api_key,
            "client_code": self.client_code,
        }

    def is_connected(self) -> bool:
        return self._connected

    def refresh_session(self) -> None:
        if self._transport is None or not self._refresh_token:
            raise BrokerError("not connected; cannot refresh")
        try:
            self._transport.generateToken(self._refresh_token)
        except Exception as e:  # pragma: no cover
            self._connected = False
            raise BrokerError(f"token refresh failed: {e}", retryable=True)

    # ------------------------------------------------- instrument master
    def refresh_instruments(self) -> dict[str, Instrument]:
        """Pull lot size / tick / token from the instrument master. The master
        is the daily source of truth; config values are only warm-start
        fallbacks (see live_runner)."""
        master = self._load_master()
        out: dict[str, Instrument] = {}
        for row in master:
            sym = row.get("symbol") or row.get("name")
            if not sym:
                continue
            kind = _infer_kind(row)
            inst = Instrument(
                symbol=sym,
                token=str(row.get("token", "")),
                exchange=row.get("exch_seg", _EXCHANGE.get(kind, "NSE")),
                kind=kind,
                lot_size=int(float(row.get("lotsize", 1) or 1)),
                tick_size=float(row.get("tick_size", 5) or 5) / 100.0,
                point_value=1.0,
            )
            out[sym] = inst
            self._token_to_symbol[inst.token] = sym
        self._instruments = out
        return out

    def _load_master(self) -> list[dict]:
        if self._instr_master is not None:
            return self._instr_master
        import json
        import urllib.request

        url = ("https://margincalculator.angelbroking.com/"
               "OpenAPI_File/files/OpenAPIScripMaster.json")
        self._hist.acquire(timeout=15)
        try:  # pragma: no cover - network
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # pragma: no cover
            raise BrokerError(f"instrument master fetch failed: {e}", retryable=True)

    def instruments(self) -> dict[str, Instrument]:
        return dict(self._instruments)

    # ----------------------------------------------------- historical data
    def historical_candles(self, symbol: str, interval: str,
                           start: datetime, end: datetime) -> list[tuple]:
        """Fetch OHLCV candles in [start, end] via getCandleData, chunked to
        Angel's per-request day limits and rate-limited. Returns
        (naive-IST ts, open, high, low, close, volume) tuples, ascending and
        de-duplicated. Read-only — never places an order.

        ``start``/``end`` are naive IST wall-clock (the engine's clock); Angel
        returns +05:30 stamps which we strip back to naive IST so backtest and
        live traverse identical timestamps.
        """
        inst = self._instruments.get(symbol)
        if inst is None or not inst.token:
            raise BrokerError(f"no instrument token for {symbol}")
        interval = interval.upper()
        max_days = _HIST_MAX_DAYS.get(interval)
        if max_days is None:
            raise BrokerError(f"unknown interval {interval!r}; one of "
                              f"{sorted(_HIST_MAX_DAYS)}")
        out: list[tuple] = []
        seen: set[datetime] = set()
        win_start = start
        while win_start <= end:
            win_end = min(end, win_start + timedelta(days=max_days - 1,
                                                     hours=23, minutes=59))
            params = {
                "exchange": inst.exchange,
                "symboltoken": inst.token,
                "interval": interval,
                "fromdate": win_start.strftime("%Y-%m-%d %H:%M"),
                "todate": win_end.strftime("%Y-%m-%d %H:%M"),
            }
            if not self._hist.acquire(timeout=20):
                raise BrokerError("historical rate limit timeout", retryable=True)
            try:
                resp = self._transport.getCandleData(params)
            except Exception as e:  # pragma: no cover - network
                raise BrokerError(f"getCandleData {symbol} failed: {e}",
                                  retryable=True)
            for row in (resp or {}).get("data") or []:
                ts = datetime.fromisoformat(str(row[0])).replace(tzinfo=None)
                if ts in seen:
                    continue
                seen.add(ts)
                out.append((ts, float(row[1]), float(row[2]), float(row[3]),
                            float(row[4]), float(row[5])))
            win_start = win_end + timedelta(minutes=1)
        out.sort(key=lambda r: r[0])
        return out

    # ------------------------------------------------------------- funds
    def funds(self) -> float:
        self._generic.acquire(timeout=10)
        try:
            rms = self._transport.rmsLimit()
        except Exception as e:  # pragma: no cover
            raise BrokerError(f"rmsLimit failed: {e}", retryable=True)
        data = (rms or {}).get("data") or {}
        for key in ("availablecash", "net", "availableCash"):
            if key in data:
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    pass
        raise BrokerError(f"could not parse funds from {data}")

    # --------------------------------------------------------- positions
    def positions(self) -> list[BrokerPosition]:
        self._generic.acquire(timeout=10)
        try:
            resp = self._transport.position()
        except Exception as e:
            raise BrokerError(f"position() failed: {e}", retryable=True)
        rows = (resp or {}).get("data") or []
        out: list[BrokerPosition] = []
        for r in rows:
            sym = r.get("tradingsymbol") or self._token_to_symbol.get(str(r.get("symboltoken", "")))
            if not sym:
                continue
            qty = int(float(r.get("netqty", 0) or 0))
            if qty == 0:
                continue
            out.append(BrokerPosition(sym, qty, float(r.get("netprice", 0) or 0)))
        return out

    def open_orders(self) -> list[BrokerOrder]:
        self._generic.acquire(timeout=10)
        try:
            resp = self._transport.orderBook()
        except Exception as e:
            raise BrokerError(f"orderBook failed: {e}", retryable=True)
        out = []
        for r in (resp or {}).get("data") or []:
            out.append(self._parse_order(r))
        return out

    # ------------------------------------------------------------- orders
    def place(self, order: BrokerOrder) -> OrderAck:
        # idempotency: never resubmit a known client id
        if order.client_order_id in self._client_to_broker:
            boid = self._client_to_broker[order.client_order_id]
            return OrderAck(order.client_order_id, boid, OrderStatus.SUBMITTED,
                            "idempotent: already placed")
        inst = self._instruments.get(order.symbol)
        if inst is None:
            raise BrokerError(f"unknown instrument {order.symbol}")
        # ordertag is the postback correlation key — must survive verbatim.
        # Refuse rather than silently truncate (a truncated tag = lost fills).
        if len(order.client_order_id) > 20:
            raise BrokerError(
                f"client_order_id {order.client_order_id!r} exceeds Angel's "
                "20-char ordertag limit; OMS must emit compact ids")
        params = {
            "variety": "NORMAL",
            "tradingsymbol": order.symbol,
            "symboltoken": inst.token,
            "transactiontype": order.side,
            "exchange": inst.exchange,
            "ordertype": "MARKET" if order.style == ExecutionStyle.MARKET_SINGLE else "LIMIT",
            "producttype": "INTRADAY" if inst.kind != InstrumentKind.EQUITY else "DELIVERY",
            "duration": "DAY",
            "quantity": str(order.qty),
            "price": str(order.limit_price or 0),
            "ordertag": order.client_order_id,  # postback correlation (<=20 chars)
        }
        if not self._orders.acquire(timeout=5):
            raise BrokerError("order rate limit timeout", retryable=True)
        try:
            resp = self._transport.placeOrder(params)
        except Exception as e:
            raise BrokerError(f"placeOrder failed: {e}", retryable=True)
        boid = _extract_order_id(resp)
        if not boid:
            raise BrokerError(f"no order id in response: {resp}")
        self._client_to_broker[order.client_order_id] = boid
        return OrderAck(order.client_order_id, boid, OrderStatus.SUBMITTED)

    def cancel(self, client_order_id: str) -> OrderAck:
        boid = self._client_to_broker.get(client_order_id, "")
        if not boid:
            raise BrokerError(f"unknown client_order_id {client_order_id}")
        if not self._orders.acquire(timeout=5):
            raise BrokerError("cancel rate limit timeout", retryable=True)
        try:
            self._transport.cancelOrder(boid, "NORMAL")
        except Exception as e:
            raise BrokerError(f"cancelOrder failed: {e}", retryable=True)
        return OrderAck(client_order_id, boid, OrderStatus.CANCELLED)

    def order_status(self, client_order_id: str) -> BrokerOrder | None:
        boid = self._client_to_broker.get(client_order_id)
        for o in self.open_orders():
            if o.broker_order_id == boid or o.client_order_id == client_order_id:
                return o
        return None

    def _parse_order(self, r: dict) -> BrokerOrder:
        status = _STATUS_MAP.get(str(r.get("status", "")).lower(), OrderStatus.NEW)
        return BrokerOrder(
            client_order_id=r.get("ordertag", ""),
            symbol=r.get("tradingsymbol", ""),
            side=r.get("transactiontype", ""),
            qty=int(float(r.get("quantity", 0) or 0)),
            style=ExecutionStyle.LIMIT_SINGLE,
            urgency=Urgency.NORMAL,
            broker_order_id=str(r.get("orderid", "")),
            status=status,
            filled_qty=int(float(r.get("filledshares", 0) or 0)),
            avg_fill_price=float(r.get("averageprice", 0) or 0),
            reject_reason=r.get("text", "") if status == OrderStatus.REJECTED else "",
        )


def _infer_kind(row: dict) -> InstrumentKind:
    seg = (row.get("exch_seg") or "").upper()
    sym = (row.get("symbol") or "").upper()
    if seg == "NFO":
        if sym.endswith("FUT"):
            return InstrumentKind.FUTURE
        if sym.endswith("CE") or sym.endswith("PE"):
            return InstrumentKind.OPTION
    return InstrumentKind.EQUITY


def _extract_order_id(resp) -> str:
    if isinstance(resp, dict):
        data = resp.get("data") or {}
        return str(data.get("orderid") or resp.get("orderid") or "")
    return str(resp or "")
