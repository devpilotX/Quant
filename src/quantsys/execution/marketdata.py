"""Live market data: tick -> bar aggregation.

The Angel One WebSocket 2.0 client is imported lazily; a generic BarAggregator
turns ticks into the engine's 5-min Bars on the IST session clock. Tests feed
ticks directly into the aggregator (no network).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from quantsys.core.types import Bar, SESSION_OPEN

log = logging.getLogger("quantsys.marketdata")


def floor_to_bucket(ts: datetime, bar_minutes: int) -> datetime:
    """Floor a timestamp to its bar bucket start, anchored at the 09:15 open
    so buckets align to the session, not the wall hour."""
    open_dt = ts.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1],
                         second=0, microsecond=0)
    if ts < open_dt:
        return open_dt
    delta_min = int((ts - open_dt).total_seconds() // 60)
    bucket = (delta_min // bar_minutes) * bar_minutes
    return open_dt + timedelta(minutes=bucket)


@dataclass
class _Building:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class BarAggregator:
    """Aggregates ticks into completed bars. Thread-safe. On each tick that
    crosses into a new bucket, the previous bucket's bar is emitted via the
    on_bar callback (symbol, Bar)."""

    def __init__(self, bar_minutes: int,
                 on_bar: Callable[[str, Bar], None]):
        self.bar_minutes = bar_minutes
        self.on_bar = on_bar
        self._building: dict[str, _Building] = {}
        self._last_vol: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_tick(self, symbol: str, price: float, ts: datetime,
                cum_volume: float | None = None) -> None:
        if not (price > 0):
            return
        bucket = floor_to_bucket(ts, self.bar_minutes)
        with self._lock:
            cur = self._building.get(symbol)
            vol_delta = 0.0
            if cum_volume is not None:
                prev = self._last_vol.get(symbol)
                vol_delta = max(0.0, cum_volume - prev) if prev is not None else 0.0
                self._last_vol[symbol] = cum_volume
            if cur is None:
                self._building[symbol] = _Building(bucket, price, price, price, price, vol_delta)
                return
            if bucket > cur.ts:
                self.on_bar(symbol, Bar(ts=cur.ts, open=cur.open, high=cur.high,
                                        low=cur.low, close=cur.close, volume=cur.volume))
                self._building[symbol] = _Building(bucket, price, price, price, price, vol_delta)
            else:
                cur.high = max(cur.high, price)
                cur.low = min(cur.low, price)
                cur.close = price
                cur.volume += vol_delta

    def flush(self) -> None:
        """Emit all in-progress bars (e.g. at session close)."""
        with self._lock:
            for sym, cur in list(self._building.items()):
                self.on_bar(sym, Bar(ts=cur.ts, open=cur.open, high=cur.high,
                                     low=cur.low, close=cur.close, volume=cur.volume))
            self._building.clear()


class AngelWebSocketFeed:  # pragma: no cover - network path
    """Thin wrapper over SmartWebSocketV2 that pushes ticks into a
    BarAggregator. Lazily imported. Auto-reconnect is delegated to the SDK's
    callbacks; on any error we log loudly and the runner's heartbeat goes
    stale -> dashboard shows feed loss -> fail safe to no new risk."""

    def __init__(self, *, auth_token: str, api_key: str, client_code: str,
                 feed_token: str, tokens_by_exchange: dict[str, list[str]],
                 aggregator: BarAggregator, token_to_symbol: dict[str, str]):
        self.auth_token = auth_token
        self.api_key = api_key
        self.client_code = client_code
        self.feed_token = feed_token
        self.tokens_by_exchange = tokens_by_exchange
        self.aggregator = aggregator
        self.token_to_symbol = token_to_symbol
        self._sws = None

    def start(self) -> None:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # type: ignore

        sws = SmartWebSocketV2(self.auth_token, self.api_key,
                               self.client_code, self.feed_token)
        self._sws = sws

        def on_data(wsapp, message):
            tok = str(message.get("token", ""))
            sym = self.token_to_symbol.get(tok)
            ltp = message.get("last_traded_price")
            if sym is None or ltp is None:
                return
            price = float(ltp) / 100.0  # Angel sends paise
            vol = message.get("volume_trade_for_the_day")
            self.aggregator.on_tick(sym, price, datetime.now(),
                                    float(vol) if vol is not None else None)

        def on_open(wsapp):
            mode = 2  # quote
            exch_map = {"NSE": 1, "NFO": 2, "BSE": 3}
            token_list = [{"exchangeType": exch_map.get(ex, 1), "tokens": toks}
                          for ex, toks in self.tokens_by_exchange.items()]
            sws.subscribe("qs-live", mode, token_list)

        def on_error(wsapp, error):
            log.error("websocket error: %s", error)

        def on_close(wsapp):
            log.warning("websocket closed")

        sws.on_open = on_open
        sws.on_data = on_data
        sws.on_error = on_error
        sws.on_close = on_close
        threading.Thread(target=sws.connect, daemon=True, name="angel-ws").start()

    def stop(self) -> None:
        if self._sws is not None:
            try:
                self._sws.close_connection()
            except Exception:
                pass
