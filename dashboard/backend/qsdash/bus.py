"""Event bus: engine -> (Redis pub/sub | Postgres LISTEN/NOTIFY) -> websocket hub.

Two backends behind one interface:

- ``RedisBus``  — production (VPS docker-compose ships Redis).
- ``PgBus``     — zero-extra-dependency fallback using Postgres LISTEN/NOTIFY;
                  the default on the Windows dev box where Redis is absent.

Both carry the same envelope on one channel::

    {"topic": "orders", "ts": "...", "data": {...}}

Topics: decisions, orders, fills, positions, equity, regime, risk, alerts,
        engine (heartbeat/status), config, commands.

NOTIFY payloads are capped at ~8000 bytes; oversized events are sent as a
reference ``{"topic": t, "ref": {"table": ..., "id": ...}}`` and clients
re-fetch via REST. Correctness over convenience: nothing is truncated.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from qsdash.config import settings
from qsdash.db import now_ist

log = logging.getLogger(__name__)

PG_CHANNEL = "qs_events"
_NOTIFY_LIMIT = 7500


def make_event(topic: str, data: Any, ref: dict | None = None) -> dict:
    ev: dict = {"topic": topic, "ts": now_ist().isoformat()}
    if ref is not None:
        ev["ref"] = ref
    else:
        ev["data"] = data
    return ev


# ----------------------------------------------------------- sync publishers
class SyncPublisher:
    """Used by the engine bridge (synchronous world)."""

    def publish(self, topic: str, data: Any, ref: dict | None = None) -> None:
        raise NotImplementedError


class PgSyncPublisher(SyncPublisher):
    """NOTIFY on the same connection/transaction the recorder writes with,
    so events and rows commit atomically (NOTIFY is transactional)."""

    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._session = None  # bound per-transaction by the recorder

    def bind(self, session) -> None:
        self._session = session

    def publish(self, topic: str, data: Any, ref: dict | None = None) -> None:
        from sqlalchemy import text

        from qsdash.db import engine as _engine

        if _engine.dialect.name != "postgresql":
            return  # tests on SQLite: rows still written, NOTIFY is a no-op

        ev = make_event(topic, data, ref)
        payload = json.dumps(ev, default=str)
        if len(payload) > _NOTIFY_LIMIT and ref is None:
            ev = make_event(topic, None, ref={"oversize": True})
            payload = json.dumps(ev)
        sess = self._session
        if sess is None:
            sess = self._session_factory()
            try:
                sess.execute(
                    text("SELECT pg_notify(:ch, :payload)"),
                    {"ch": PG_CHANNEL, "payload": payload},
                )
                sess.commit()
            finally:
                sess.close()
        else:
            sess.execute(
                text("SELECT pg_notify(:ch, :payload)"),
                {"ch": PG_CHANNEL, "payload": payload},
            )


class RedisSyncPublisher(SyncPublisher):
    def __init__(self, url: str):
        import redis

        self._r = redis.Redis.from_url(url)

    def publish(self, topic: str, data: Any, ref: dict | None = None) -> None:
        self._r.publish(PG_CHANNEL, json.dumps(make_event(topic, data, ref), default=str))


def make_sync_publisher(session_factory) -> SyncPublisher:
    if settings.redis_url:
        return RedisSyncPublisher(settings.redis_url)
    return PgSyncPublisher(session_factory)


# ---------------------------------------------------------- async subscriber
class AsyncSubscriber:
    async def events(self) -> AsyncIterator[dict]:
        raise NotImplementedError
        yield  # pragma: no cover


class RedisAsyncSubscriber(AsyncSubscriber):
    def __init__(self, url: str):
        self._url = url

    async def events(self) -> AsyncIterator[dict]:
        import redis.asyncio as aioredis

        while True:
            try:
                r = aioredis.from_url(self._url)
                ps = r.pubsub()
                await ps.subscribe(PG_CHANNEL)
                async for msg in ps.listen():
                    if msg["type"] == "message":
                        try:
                            yield json.loads(msg["data"])
                        except json.JSONDecodeError:
                            log.warning("bad event payload dropped")
            except Exception as e:  # reconnect forever, loudly
                log.error("redis subscriber error: %s — reconnecting in 2s", e)
                await asyncio.sleep(2)


class PgAsyncSubscriber(AsyncSubscriber):
    """LISTEN on a dedicated thread with a SYNC connection, bridged into the
    event loop via call_soon_threadsafe. psycopg's async I/O needs a selector
    loop, which uvicorn on Windows doesn't provide (Proactor) — the thread
    approach works on every platform and event loop."""

    def __init__(self, dsn: str):
        self._dsn = dsn.replace("postgresql+psycopg://", "postgresql://")

    async def events(self) -> AsyncIterator[dict]:
        import threading
        import time

        import psycopg

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10_000)

        def _worker() -> None:
            while True:
                try:
                    with psycopg.connect(self._dsn, autocommit=True) as conn:
                        conn.execute(f"LISTEN {PG_CHANNEL}")
                        log.info("pg LISTEN established")
                        for notify in conn.notifies():
                            loop.call_soon_threadsafe(
                                queue.put_nowait, notify.payload
                            )
                except Exception as e:  # reconnect forever, loudly
                    log.error("pg listener error: %s — reconnecting in 2s", e)
                    time.sleep(2)

        threading.Thread(target=_worker, daemon=True, name="pg-listen").start()
        while True:
            payload = await queue.get()
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                log.warning("bad event payload dropped")


class NullAsyncSubscriber(AsyncSubscriber):
    """No live events possible (e.g. SQLite tests): idle forever."""

    async def events(self) -> AsyncIterator[dict]:
        while True:
            await asyncio.sleep(3600)
        yield  # pragma: no cover


def make_async_subscriber() -> AsyncSubscriber:
    if settings.redis_url:
        return RedisAsyncSubscriber(settings.redis_url)
    if settings.database_url.startswith(("postgresql", "postgres")):
        return PgAsyncSubscriber(settings.database_url)
    return NullAsyncSubscriber()
