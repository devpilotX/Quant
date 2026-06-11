"""WebSocket hub: one bus subscription fanned out to all connected clients.

Client protocol:
- connect to /ws?topics=orders,equity,... (omit for all topics)
- cookie-authenticated like every other endpoint
- server sends {"type":"hello"} then {"type":"event", ...envelope} frames and
  {"type":"ping"} every 20s; client replies {"type":"pong"} (or anything).
On reconnect the client must re-fetch REST snapshots before trusting the
stream again (the UI's useLive hook does exactly that).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from qsdash.bus import make_async_subscriber
from qsdash.db import SessionLocal, now_ist
from qsdash.models import AuthSession
from qsdash.security import hash_token

log = logging.getLogger(__name__)
router = APIRouter()

ALL_TOPICS = {
    "decisions", "orders", "fills", "positions", "equity", "regime",
    "risk", "alerts", "engine", "config", "commands",
}


class Hub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, set[str]] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump(), name="ws-hub-pump")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _pump(self) -> None:
        sub = make_async_subscriber()
        async for event in sub.events():
            topic = event.get("topic", "")
            async with self._lock:
                targets = [ws for ws, topics in self._clients.items()
                           if topic in topics]
            for ws in targets:
                try:
                    await ws.send_json({"type": "event", **event})
                except Exception:
                    await self.detach(ws)

    async def attach(self, ws: WebSocket, topics: set[str]) -> None:
        async with self._lock:
            self._clients[ws] = topics

    async def detach(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(ws, None)


hub = Hub()


def _authenticate(ws: WebSocket) -> bool:
    raw = ws.cookies.get("qs_session")
    if not raw:
        return False
    db = SessionLocal()
    try:
        sess = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == hash_token(raw),
                    AuthSession.revoked == False)  # noqa: E712
            .first()
        )
        return sess is not None
    finally:
        db.close()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not _authenticate(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    raw_topics = ws.query_params.get("topics", "")
    topics = ({t.strip() for t in raw_topics.split(",") if t.strip()} & ALL_TOPICS
              ) or set(ALL_TOPICS)
    await hub.start()
    await hub.attach(ws, topics)
    await ws.send_json({"type": "hello", "server_ts": now_ist().isoformat(),
                        "topics": sorted(topics)})
    try:
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=20)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.debug("ws closed: %s", e)
    finally:
        await hub.detach(ws)
