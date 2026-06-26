"""Local reproduction of the in-session feed reconnect/re-auth STORM.

Background (2026-06-26 audit): during market hours the paper engine's RSS ran
away to ~23.5 GB and OOM'd every ~21 min. The trigger was the feed supervisor
re-logging in (generateSession) on EVERY reconnect while a 'connected but
silent' socket was force-reconnected every ~90 s — a re-auth storm that drove
the SDK into an SSL-race fast-resubscribe runaway.

This test drives a connected-but-silent socket (no ticks) so the watchdog keeps
force-reconnecting, and asserts the supervisor does NOT re-login on every
reconnect. It is a STORM-CONTAINMENT test (mock socket, no real SSL) — it proves
the reconnect/re-auth rate is bounded, NOT that the real-SDK memory leak is gone
(that verdict stays open until a live session). Pure threads, no network.
"""

from __future__ import annotations

import threading
import time

from quantsys.execution.marketdata import AngelWebSocketFeed, BarAggregator


class _SilentSock:
    """A socket that 'connects' (blocks in connect()) but never delivers a tick;
    connect() returns only when close_connection() is called — i.e. exactly the
    'connected but silent' feed the watchdog is meant to recycle."""

    def __init__(self) -> None:
        self._closed = threading.Event()
        self.on_open = self.on_data = self.on_error = self.on_close = None

    def connect(self) -> None:
        self._closed.wait(timeout=5.0)  # safety cap so a stuck test can't hang

    def close_connection(self) -> None:
        self._closed.set()


def _run_silent_feed(seconds: float = 2.0, **overrides):
    socks: list[_SilentSock] = []
    reauths: list[float] = []

    def factory(*_a):
        s = _SilentSock()
        socks.append(s)
        return s

    def reauth():
        reauths.append(time.monotonic())
        return {}

    agg = BarAggregator(5, lambda _sym, _bar: None)
    kw = dict(
        auth_token="a", api_key="k", client_code="c", feed_token="f",
        tokens_by_exchange={"NSE": ["1"]}, aggregator=agg,
        token_to_symbol={"1": "X"}, reauth=reauth, ws_factory=factory,
        is_open=lambda: True, stale_seconds=0.15, watchdog_interval=0.03,
        initial_backoff=0.03, max_backoff=0.2,
    )
    kw.update(overrides)
    feed = AngelWebSocketFeed(**kw)
    feed.start()
    time.sleep(seconds)
    feed.stop()
    time.sleep(0.2)
    return len(socks), len(reauths)


def test_silent_feed_does_not_storm_reauth():
    """A connected-but-silent feed gets recycled by the watchdog repeatedly, but
    the supervisor must NOT re-login on every reconnect — that broker hammering
    is what drove the SSL-race memory runaway. Re-auth must be rate-limited."""
    reconnects, reauths = _run_silent_feed()
    assert reconnects >= 4, f"watchdog should recycle the silent socket; got {reconnects} reconnects"
    assert reauths <= max(2, reconnects // 3), (
        f"re-auth STORM: {reauths} re-logins for {reconnects} reconnects "
        f"(must reuse the day's token, not generateSession every reconnect)"
    )
