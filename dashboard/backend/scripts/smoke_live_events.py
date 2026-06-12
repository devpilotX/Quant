"""Prove the real-time pipeline end-to-end: engine process -> Postgres
NOTIFY -> API ws hub -> client. Run while the runner is producing bars.
Usage: python scripts/smoke_live_events.py <user> <pass> <totp_secret>
"""

import asyncio
import json
import sys

import httpx
import pyotp
import websockets

user, pw, secret = sys.argv[1], sys.argv[2], sys.argv[3]


async def main() -> None:
    c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=20)
    r = c.post("/api/auth/login", json={
        "username": user, "password": pw, "totp": pyotp.TOTP(secret).now(),
    })
    r.raise_for_status()
    cookie = c.cookies.get("qs_session")

    got: dict[str, int] = {}
    async with websockets.connect(
        "ws://127.0.0.1:8000/ws",
        additional_headers={"Cookie": f"qs_session={cookie}"},
    ) as ws:
        await ws.recv()  # hello
        deadline = asyncio.get_event_loop().time() + 45
        while asyncio.get_event_loop().time() < deadline and sum(got.values()) < 8:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(raw)
            if ev.get("type") == "event":
                got[ev["topic"]] = got.get(ev["topic"], 0) + 1

    print("live events received by topic:", got)
    assert got, "no live events arrived — pipeline broken"
    print("REAL-TIME PIPELINE OK")


asyncio.run(main())
