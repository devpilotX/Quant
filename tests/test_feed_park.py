"""Market-hours parking of the websocket feed supervisor.

Angel One drops idle sockets outside market hours; before parking existed the
supervisor reconnect-churned all night (broken-pipe loops, log spam, nightly
container restarts, and fuel for the SSL-race leak). These tests prove the
supervisor makes NO connection or re-auth attempts while the activity window is
closed, resumes when it opens, and parks again when it closes mid-flight.
Pure threads, no network (same idiom as test_feed_storm).
"""

from __future__ import annotations

import threading
import time

from quantsys.execution.marketdata import AngelWebSocketFeed, BarAggregator


class _SilentSock:
    """'Connects' (blocks in connect()) but never ticks; connect() returns when
    close_connection() is called."""

    def __init__(self) -> None:
        self._closed = threading.Event()
        self.on_open = self.on_data = self.on_error = self.on_close = None

    def connect(self) -> None:
        self._closed.wait(timeout=5.0)

    def close_connection(self) -> None:
        self._closed.set()


def _make_feed(active_flag: list[bool], socks: list, reauths: list):
    def factory(*_a):
        s = _SilentSock()
        socks.append(s)
        return s

    def reauth():
        reauths.append(time.monotonic())
        return {}

    agg = BarAggregator(5, lambda _sym, _bar: None)
    return AngelWebSocketFeed(
        auth_token="a", api_key="k", client_code="c", feed_token="f",
        tokens_by_exchange={"NSE": ["1"]}, aggregator=agg,
        token_to_symbol={"1": "X"}, reauth=reauth, ws_factory=factory,
        is_open=lambda: active_flag[0], active_fn=lambda: active_flag[0],
        stale_seconds=0.15, watchdog_interval=0.03,
        initial_backoff=0.03, max_backoff=0.2, park_poll_s=0.02,
    )


def test_parked_feed_makes_no_connections():
    """Closed market: zero sockets built, zero re-auth calls, parked flag set."""
    socks: list = []
    reauths: list = []
    feed = _make_feed([False], socks, reauths)
    feed.start()
    time.sleep(0.5)
    try:
        assert socks == [], f"parked feed must not build sockets, built {len(socks)}"
        assert reauths == [], "parked feed must not re-auth"
        assert feed.parked
    finally:
        feed.stop()


def test_feed_resumes_when_window_opens():
    """Parked -> window opens -> the supervisor re-auths once (token likely
    rolled overnight) and builds a socket."""
    active = [False]
    socks: list = []
    reauths: list = []
    feed = _make_feed(active, socks, reauths)
    feed.start()
    time.sleep(0.2)
    assert socks == []
    active[0] = True
    time.sleep(0.3)
    try:
        assert len(socks) >= 1, "feed must reconnect when the window opens"
        assert len(reauths) >= 1, "resume should refresh the (stale) day token"
        assert not feed.parked
    finally:
        feed.stop()


def test_session_close_parks_connected_feed():
    """Connected feed + market closes + broker drops the socket => the
    supervisor parks instead of reconnect-churning."""
    active = [True]
    socks: list = []
    reauths: list = []
    feed = _make_feed(active, socks, reauths)
    feed.start()
    time.sleep(0.2)
    assert len(socks) >= 1
    active[0] = False           # session over...
    socks[-1].close_connection()  # ...and Angel drops the idle socket
    time.sleep(0.3)
    n_after_close = len(socks)
    time.sleep(0.3)
    try:
        assert len(socks) == n_after_close, (
            f"supervisor kept reconnecting after close: {len(socks)} > {n_after_close}")
        assert feed.parked
    finally:
        feed.stop()
