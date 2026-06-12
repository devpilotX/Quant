"""Smoke the SINGLE-ORIGIN path the browser uses: Next server on :3000
proxies /api and /ws to FastAPI. Verifies cookies survive the proxy and the
websocket upgrade works through the rewrite.
Usage: python scripts/smoke_proxy.py <username> <password> <totp_secret>
"""

import asyncio
import sys

import httpx
import pyotp

BASE = "http://127.0.0.1:3000"
user, pw, secret = sys.argv[1], sys.argv[2], sys.argv[3]


async def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.get("/login")
    print("frontend /login:", r.status_code, f"({len(r.text)} bytes)")
    r = c.post("/api/auth/login", json={
        "username": user, "password": pw, "totp": pyotp.TOTP(secret).now(),
    })
    print("login via proxy:", r.status_code)
    r.raise_for_status()
    session_cookie = c.cookies.get("qs_session")
    ov = c.get("/api/overview").json()
    print("overview via proxy: mode=%s equity=%s" % (ov["mode"], ov["equity"]["value"]))

    import websockets

    try:
        async with websockets.connect(
            "ws://127.0.0.1:3000/ws?topics=engine,equity",
            additional_headers={"Cookie": f"qs_session={session_cookie}"},
            open_timeout=10,
        ) as ws:
            hello = await asyncio.wait_for(ws.recv(), timeout=10)
            print("WS through Next proxy: OK —", hello[:120])
    except Exception as e:
        print(f"WS through Next proxy FAILED ({type(e).__name__}: {e})")
        print("-> dev fallback: UI degrades to 5s REST polling and shows 'stream DOWN'")
        print("-> production unaffected: nginx handles /ws directly")

    # direct-to-API ws (what nginx does in prod) must work regardless
    try:
        async with websockets.connect(
            "ws://127.0.0.1:8000/ws?topics=engine",
            additional_headers={"Cookie": f"qs_session={session_cookie}"},
            open_timeout=10,
        ) as ws:
            hello = await asyncio.wait_for(ws.recv(), timeout=10)
            print("WS direct to API: OK —", hello[:120])
    except Exception as e:
        print(f"WS direct to API FAILED: {e}")
        raise SystemExit(1)


asyncio.run(main())
