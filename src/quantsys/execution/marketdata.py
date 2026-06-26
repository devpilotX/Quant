"""Live market data: tick -> bar aggregation.

The Angel One WebSocket 2.0 client is imported lazily; a generic BarAggregator
turns ticks into the engine's 5-min Bars on the IST session clock. Tests feed
ticks directly into the aggregator (no network).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from quantsys.core.types import Bar, SESSION_OPEN, is_session_open, now_ist

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


def _default_ws_factory(auth_token, api_key, client_code, feed_token):  # pragma: no cover - network
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # type: ignore

    return SmartWebSocketV2(auth_token, api_key, client_code, feed_token)


class AngelWebSocketFeed:  # pragma: no cover - network path
    """Self-healing wrapper over SmartWebSocketV2 that pushes ticks into a
    BarAggregator.

    The 2026-06-13 incident: the SDK hit 'max retry attempts reached' over a
    weekend, its connect() thread exited, nothing restarted it, and yet the
    LiveRunner kept heart-beating — so the feed was silently dead at Monday's
    open and the dashboard showed no data. The old code delegated all reconnect
    to the SDK and assumed feed loss would surface as a stale heartbeat; neither
    held. This wrapper owns reconnection so a dropped or token-expired feed
    recovers on its own:

      * a supervisor thread (re)builds the socket and blocks in connect(); when
        connect() returns (SDK gave up / socket closed) it re-authenticates for
        fresh tokens — Friday's token is dead by Monday — and reconnects, with
        capped exponential backoff;
      * a watchdog thread force-closes a socket that stops delivering ticks
        during market hours (a 'connected but silent' feed), which makes
        connect() return so the supervisor rebuilds it;
      * on_close / on_error tolerate the SDK's varying callback arities (the
        SDK's own _on_close signature mismatch was part of the incident).

    The socket constructor (ws_factory) and re-auth (reauth) are injected so the
    whole control flow is unit-tested without a network or the SDK.
    """

    def __init__(self, *, auth_token: str, api_key: str, client_code: str,
                 feed_token: str, tokens_by_exchange: dict[str, list[str]],
                 aggregator: BarAggregator, token_to_symbol: dict[str, str],
                 reauth: Callable[[], dict] | None = None,
                 ws_factory: Callable[..., object] | None = None,
                 is_open: Callable[[], bool] | None = None,
                 stale_seconds: float = 90.0, initial_backoff: float = 1.0,
                 max_backoff: float = 60.0, watchdog_interval: float = 15.0,
                 reauth_min_interval: float = 300.0):
        self.auth_token = auth_token
        self.api_key = api_key
        self.client_code = client_code
        self.feed_token = feed_token
        self.tokens_by_exchange = tokens_by_exchange
        self.aggregator = aggregator
        self.token_to_symbol = token_to_symbol
        self._reauth = reauth
        self._ws_factory = ws_factory or _default_ws_factory
        self._is_open = is_open or (lambda: is_session_open(now_ist()))
        self._stale_seconds = stale_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._watchdog_interval = watchdog_interval
        self._sws = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_tick = 0.0       # monotonic; 0.0 => no tick yet
        self._connected_at = 0.0    # monotonic of last socket (re)build; 0 => down
        self._reauth_min_interval = reauth_min_interval
        self._last_reauth = 0.0     # monotonic of last generateSession; 0 => never
        self._ticks_this_conn = 0   # ticks delivered by the CURRENT socket

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._stop.clear()
        threading.Thread(target=self._supervise, daemon=True,
                         name="angel-ws-sup").start()
        threading.Thread(target=self._watchdog, daemon=True,
                         name="angel-ws-wd").start()

    def stop(self) -> None:
        self._stop.set()
        self._close_socket()

    def seconds_since_tick(self) -> float | None:
        """Monotonic seconds since the last tick, or None if none received yet."""
        with self._lock:
            last = self._last_tick
        return None if last == 0.0 else max(0.0, time.monotonic() - last)

    # ------------------------------------------------------------- callbacks
    def _bind_callbacks(self, sws) -> None:
        def on_data(wsapp, message):
            tok = str(message.get("token", ""))
            sym = self.token_to_symbol.get(tok)
            ltp = message.get("last_traded_price")
            if sym is None or ltp is None:
                return
            price = float(ltp) / 100.0  # Angel sends paise
            vol = message.get("volume_trade_for_the_day")
            with self._lock:
                self._last_tick = time.monotonic()
                self._ticks_this_conn += 1
            # now_ist(), NOT datetime.now(): the VPS container runs in UTC and
            # the aggregator buckets against the 09:15 IST session open.
            self.aggregator.on_tick(sym, price, now_ist(),
                                    float(vol) if vol is not None else None)

        def on_open(wsapp):
            mode = 2  # quote
            exch_map = {"NSE": 1, "NFO": 2, "BSE": 3}
            token_list = [{"exchangeType": exch_map.get(ex, 1), "tokens": toks}
                          for ex, toks in self.tokens_by_exchange.items()]
            sws.subscribe("qs-live", mode, token_list)

        def on_error(wsapp, *args):
            log.error("websocket error: %s", args[0] if args else "?")

        def on_close(wsapp, *args):
            log.warning("websocket closed%s", f": {args}" if args else "")

        sws.on_open = on_open
        sws.on_data = on_data
        sws.on_error = on_error
        sws.on_close = on_close

    # ------------------------------------------------------------- internals
    def _close_socket(self) -> None:
        with self._lock:
            sws = self._sws
        if sws is not None:
            try:
                sws.close_connection()
            except Exception:
                pass

    def _do_reauth(self) -> None:
        if self._reauth is None:
            return
        try:
            creds = self._reauth() or {}
        except Exception as e:
            log.error("feed: re-auth failed: %s", e)
            return
        self.auth_token = creds.get("auth_token", self.auth_token)
        self.feed_token = creds.get("feed_token", self.feed_token)
        self.api_key = creds.get("api_key", self.api_key)
        self.client_code = creds.get("client_code", self.client_code)
        log.info("feed: re-authenticated for fresh websocket tokens")

    def _supervise(self) -> None:
        backoff = self._initial_backoff
        while not self._stop.is_set():
            with self._lock:
                self._ticks_this_conn = 0  # only a tick-delivering socket is healthy
            try:
                sws = self._ws_factory(self.auth_token, self.api_key,
                                       self.client_code, self.feed_token)
                self._bind_callbacks(sws)
                with self._lock:
                    self._sws = sws
                    self._connected_at = time.monotonic()
                sws.connect()  # blocks until the socket closes / SDK gives up
            except Exception as e:
                log.error("feed: connect failed: %s", e)
            with self._lock:
                self._sws = None
                self._connected_at = 0.0  # mark down so the watchdog stays quiet
                had_ticks = self._ticks_this_conn > 0
            if self._stop.is_set():
                break
            # Reset backoff only when the socket actually delivered ticks. The old
            # code reset on "connection lasted >=30s", but a 'connected but silent'
            # socket lives ~90s before the watchdog recycles it — that read as
            # healthy, pinned backoff at 1s and produced a reconnect storm (which
            # drove the SSL-race memory runaway). Now a silent socket backs off.
            if had_ticks:
                backoff = self._initial_backoff
            else:
                backoff = min(self._max_backoff,
                              max(self._initial_backoff, backoff * 2))
            # Re-auth is RATE-LIMITED: a fresh generateSession on every reconnect
            # hammered the broker and fed the runaway. The day's feed token stays
            # valid, so refresh at most every reauth_min_interval — the first
            # reconnect still refreshes, so a stale Monday token recovers promptly.
            now = time.monotonic()
            if now - self._last_reauth >= self._reauth_min_interval:
                self._do_reauth()
                self._last_reauth = now
            log.warning("feed: socket down — reconnect in %.0fs", backoff)
            if self._stop.wait(backoff):
                break

    def _watchdog(self) -> None:
        while not self._stop.wait(self._watchdog_interval):
            if not self._is_open():
                continue
            with self._lock:
                connected_for = (time.monotonic() - self._connected_at
                                 if self._connected_at else 0.0)
            if connected_for < self._stale_seconds:
                continue  # no socket, or let a fresh one warm up before judging
            since = self.seconds_since_tick()
            if since is None or since > self._stale_seconds:
                log.error("feed: no ticks for >%.0fs during market hours — "
                          "forcing reconnect", self._stale_seconds)
                self._close_socket()  # unblock connect() -> supervisor rebuilds
