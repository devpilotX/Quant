"""Live market-data layer: IST session-clock bar aggregation and the
self-healing Angel One websocket feed.

Regression cover for the 2026-06-15 blank-dashboard incident:
- ticks must be stamped in IST, not the container's UTC, or no bar ever emits
  during the real NSE session (floor_to_bucket anchors at 09:15 IST);
- a dead/silent feed must reconnect (with re-auth) on its own — the SDK gave up
  at 'max retry attempts reached' over a weekend and nothing restarted it.

The socket constructor and re-auth are injected, so the feed's control flow is
exercised with no network and no SmartApi SDK.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from quantsys.core.types import now_ist
from quantsys.execution.marketdata import (
    AngelWebSocketFeed,
    BarAggregator,
    floor_to_bucket,
)


# ----------------------------------------------------------------- IST clock
def test_now_ist_is_ist_wall_clock():
    expected = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
    assert now_ist().tzinfo is None
    assert abs((now_ist() - expected).total_seconds()) < 5


def test_floor_to_bucket_anchors_at_session_open():
    assert floor_to_bucket(datetime(2026, 6, 15, 9, 17), 5) == datetime(2026, 6, 15, 9, 15)
    assert floor_to_bucket(datetime(2026, 6, 15, 9, 20), 5) == datetime(2026, 6, 15, 9, 20)
    # before the open clamps to the open (no overnight buckets)
    assert floor_to_bucket(datetime(2026, 6, 15, 9, 10), 5) == datetime(2026, 6, 15, 9, 15)


def test_aggregator_emits_on_ist_session_clock():
    bars: list[tuple[str, object]] = []
    agg = BarAggregator(5, lambda s, b: bars.append((s, b)))
    base = datetime(2026, 6, 15, 9, 15)  # NSE session open, IST-naive
    agg.on_tick("NIFTY", 100.0, base)
    agg.on_tick("NIFTY", 101.0, base + timedelta(minutes=1))
    agg.on_tick("NIFTY", 99.0, base + timedelta(minutes=4, seconds=59))
    assert bars == []  # 09:15-09:20 bucket still building
    agg.on_tick("NIFTY", 102.0, base + timedelta(minutes=5))  # crosses into 09:20
    assert len(bars) == 1
    sym, bar = bars[0]
    assert sym == "NIFTY"
    assert bar.ts == base
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 101.0, 99.0, 99.0)


# ------------------------------------------------------------------- fakes
class _AggStub:
    def __init__(self):
        self.ticks: list[tuple] = []

    def on_tick(self, *args, **kwargs):
        self.ticks.append(args)


class _FakeSws:
    """Stand-in for SmartWebSocketV2 driving the feed's control flow.

    block=False: connect() returns at once (a socket that dies immediately).
    block=True : connect() blocks until close_connection() (a live socket).
    """

    def __init__(self, *, block: bool = False):
        self.on_open = self.on_data = self.on_error = self.on_close = None
        self._gate = threading.Event()
        self._block = block
        self.closed = False
        self.connect_calls = 0
        self.subscribed: list = []

    def connect(self):
        self.connect_calls += 1
        if self._block:
            self._gate.wait()

    def close_connection(self):
        self.closed = True
        self._gate.set()

    def subscribe(self, *args, **kwargs):
        self.subscribed.append((args, kwargs))


def _make_feed(**kwargs) -> AngelWebSocketFeed:
    defaults = dict(
        auth_token="a", api_key="k", client_code="c", feed_token="f",
        tokens_by_exchange={}, aggregator=_AggStub(), token_to_symbol={},
    )
    defaults.update(kwargs)
    return AngelWebSocketFeed(**defaults)


# ----------------------------------------------------- tick handling (IST)
def test_feed_on_data_stamps_ist_and_parses_paise():
    agg = _AggStub()
    feed = _make_feed(aggregator=agg, token_to_symbol={"3045": "SBIN-EQ"})
    fake = _FakeSws()
    feed._bind_callbacks(fake)
    fake.on_data(fake, {"token": "3045", "last_traded_price": 55000,
                        "volume_trade_for_the_day": 1000})
    assert len(agg.ticks) == 1
    sym, price, ts, vol = agg.ticks[0]
    assert sym == "SBIN-EQ"
    assert price == 550.0           # paise -> rupees
    assert vol == 1000.0
    assert ts.tzinfo is None        # naive IST, the engine's one clock
    assert abs((ts - now_ist()).total_seconds()) < 5  # IST, not container UTC
    assert feed.seconds_since_tick() is not None


def test_feed_on_data_ignores_unknown_token_and_missing_price():
    agg = _AggStub()
    feed = _make_feed(aggregator=agg, token_to_symbol={"3045": "SBIN-EQ"})
    fake = _FakeSws()
    feed._bind_callbacks(fake)
    fake.on_data(fake, {"token": "9999", "last_traded_price": 100})   # unknown token
    fake.on_data(fake, {"token": "3045"})                            # no price
    assert agg.ticks == []


def test_feed_close_callback_tolerates_sdk_arity():
    # the SDK calls on_close with (ws), (ws, code, msg), etc. — must not raise.
    feed = _make_feed()
    fake = _FakeSws()
    feed._bind_callbacks(fake)
    fake.on_close(fake)
    fake.on_close(fake, 1006, "abnormal")
    fake.on_error(fake)
    fake.on_error(fake, RuntimeError("boom"))


# --------------------------------------------------- reconnect / re-auth
def test_feed_supervisor_reconnects_and_reauth_is_rate_limited():
    """The supervisor rebuilds the socket every time it dies, but re-auth is
    RATE-LIMITED — it refreshes the token on the first reconnect and then REUSES
    it. (Re-logging in / generateSession on every reconnect hammered the broker
    and fed the 2026-06-26 SSL-race memory runaway.) Many reconnects, one re-auth.
    """
    built: list[_FakeSws] = []
    reauth_calls: list[int] = []

    def factory(auth, key, code, feed):
        s = _FakeSws(block=False)  # dies immediately -> supervisor must rebuild
        built.append(s)
        return s

    def reauth():
        reauth_calls.append(1)
        return {"auth_token": "fresh", "feed_token": "ft2"}

    feed = _make_feed(reauth=reauth, ws_factory=factory,
                      is_open=lambda: False,  # keep the watchdog out of it
                      initial_backoff=0.01, max_backoff=0.02)  # default reauth_min_interval=300s
    feed.start()
    deadline = time.monotonic() + 3.0
    while len(built) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    feed.stop()

    assert len(built) >= 5            # rebuilt the socket every time it died
    assert len(reauth_calls) == 1     # re-auth ONCE (rate-limited), not per reconnect
    assert feed.auth_token == "fresh" and feed.feed_token == "ft2"  # token refreshed


def test_feed_reauth_recurs_after_interval():
    """Re-auth still RECURS after reauth_min_interval (a stale Monday token must
    refresh) — just far less often than reconnects, so it never storms."""
    built: list[_FakeSws] = []
    reauth_calls: list[float] = []

    def factory(auth, key, code, feed):
        s = _FakeSws(block=False)
        built.append(s)
        return s

    def reauth():
        reauth_calls.append(time.monotonic())
        return {}

    feed = _make_feed(reauth=reauth, ws_factory=factory, is_open=lambda: False,
                      initial_backoff=0.005, max_backoff=0.01, reauth_min_interval=0.1)
    feed.start()
    time.sleep(0.6)
    feed.stop()

    assert len(built) >= 8                       # many reconnects
    assert len(reauth_calls) >= 2                # re-auth recurred (token refresh works)
    assert len(reauth_calls) < len(built)        # but rate-limited, not every reconnect


def test_feed_watchdog_forces_reconnect_on_silent_feed():
    built: list[_FakeSws] = []

    def factory(auth, key, code, feed):
        s = _FakeSws(block=True)  # stays "connected" but never ticks
        built.append(s)
        return s

    feed = _make_feed(ws_factory=factory, is_open=lambda: True,
                      stale_seconds=0.05, watchdog_interval=0.02,
                      initial_backoff=0.01, max_backoff=0.02)
    feed.start()
    deadline = time.monotonic() + 3.0
    while len(built) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    feed.stop()

    assert len(built) >= 2     # watchdog force-closed the silent socket; rebuilt
    assert built[0].closed     # the first, silent socket was force-closed


def test_feed_watchdog_leaves_socket_alone_when_market_closed():
    built: list[_FakeSws] = []

    def factory(auth, key, code, feed):
        s = _FakeSws(block=True)
        built.append(s)
        return s

    feed = _make_feed(ws_factory=factory, is_open=lambda: False,  # market closed
                      stale_seconds=0.05, watchdog_interval=0.02,
                      initial_backoff=0.01, max_backoff=0.02)
    feed.start()
    time.sleep(0.3)
    closed_first = built[0].closed if built else False
    feed.stop()

    assert len(built) == 1      # one socket, never force-reconnected
    assert closed_first is False  # not touched while the market was closed
