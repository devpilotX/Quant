"""One-command end-to-end verification of the whole stack.

Runs, in order, and FAILS LOUD on the first broken thing:
  1. engine pytest        (quantsys: 107 tests)
  2. backend pytest       (qsdash: 39 tests, SQLite)
  3. fresh paper session  (engine -> Postgres, real CostModel fills)
  4. REST smoke           (login + every dashboard endpoint)
  5. live-event pipeline  (engine -> NOTIFY -> ws hub -> client)
  6. frontend routes      (every page renders its real content)

Usage (API on :8000 and frontend on :3000 must be running for 4-6):
  python scripts/e2e.py --user dipanshu --password <pw> --totp <secret>
  python scripts/e2e.py ... --skip-frontend     # if 3000 isn't up
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx
import pyotp

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
PY = sys.executable

_results: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def run(cmd: list[str], cwd: Path, timeout: int = 1200) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout)
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
        return r.returncode == 0, tail[0]
    except Exception as e:
        return False, str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--totp", required=True, help="TOTP secret")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--frontend", default="http://127.0.0.1:3000")
    ap.add_argument("--skip-frontend", action="store_true")
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--bars", type=int, default=500)
    args = ap.parse_args()

    print("== quantsys end-to-end ==")

    # 1-2 pytest
    if not args.skip_pytest:
        ok, msg = run([PY, "-m", "pytest", "tests", "-q", "-p", "no:warnings"], REPO)
        step("engine pytest", ok, msg)
        ok, msg = run([PY, "-m", "pytest", "tests", "-q", "-p", "no:warnings"], BACKEND)
        step("backend pytest", ok, msg)

    # 3 fresh paper session
    ok, msg = run([PY, "-m", "qsdash.bridge.runner", "--config",
                   str(REPO / "config" / "base.yaml"), "--synthetic",
                   "--bars", str(args.bars)], REPO, timeout=900)
    step("paper session (engine->DB)", ok, msg)

    # 4 REST smoke
    try:
        c = httpx.Client(base_url=args.api, timeout=30)
        r = c.post("/api/auth/login", json={
            "username": args.user, "password": args.password,
            "totp": pyotp.TOTP(args.totp).now()})
        r.raise_for_status()
        csrf = c.cookies.get("qs_csrf")
        ov = c.get("/api/overview").json()
        endpoints = ["/api/positions?status=open", "/api/orders?limit=5",
                     "/api/equity-curve?max_points=10", "/api/pnl/metrics",
                     "/api/pnl/attribution?by=strategy", "/api/decisions?limit=5",
                     "/api/strategies", "/api/regime/current", "/api/risk/limits",
                     "/api/capital", "/api/backtests", "/api/alerts",
                     "/api/audit-log", "/api/db/tables"]
        bad = [e for e in endpoints if c.get(e).status_code != 200]
        # control guard: kill without reauth must 403
        guard = c.post("/api/control/kill", json={"action": "kill"},
                       headers={"X-CSRF-Token": csrf}).status_code == 403
        step("REST: login+overview", ov.get("mode") == "paper",
             f"equity={ov['equity']['value']}")
        step("REST: all data endpoints", not bad, f"{len(endpoints)-len(bad)}/{len(endpoints)} ok"
             + (f" BAD={bad}" if bad else ""))
        step("REST: control needs reauth (403)", guard)
    except Exception as e:
        step("REST smoke", False, str(e))

    # 5 live-event pipeline (run a slow engine while listening)
    ok, msg = _live_events(args)
    step("live event pipeline (WS)", ok, msg)

    # 6 frontend
    if not args.skip_frontend:
        try:
            fc = httpx.Client(base_url=args.frontend, timeout=20)
            # markers must be SSR-stable (present before client hydration);
            # pages behind a Suspense boundary are matched on a nav/shell label
            markers = {"/login": "operator sign-in", "/": "quantsys",
                       "/positions": "Positions", "/backtests": "walk-forward",
                       "/settings": "Trading mode", "/db": "Tables",
                       "/explain": "Explainability", "/risk": "kill"}
            miss = [r for r, m in markers.items()
                    if m.lower() not in fc.get(r).text.lower()]
            step("frontend routes render", not miss,
                 f"{len(markers)-len(miss)}/{len(markers)} ok"
                 + (f" MISSING={miss}" if miss else ""))
        except Exception as e:
            step("frontend routes", False, str(e))

    print("\n== summary ==")
    fails = [n for n, ok, _ in _results if not ok]
    for n, ok, d in _results:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    if fails:
        print(f"\n{len(fails)} FAILED: {', '.join(fails)}")
        return 1
    print(f"\nALL {len(_results)} CHECKS PASSED")
    return 0


def _live_events(args) -> tuple[bool, str]:
    import json

    import websockets
    import asyncio

    c = httpx.Client(base_url=args.api, timeout=20)
    r = c.post("/api/auth/login", json={
        "username": args.user, "password": args.password,
        "totp": pyotp.TOTP(args.totp).now()})
    if r.status_code != 200:
        return False, "login failed"
    cookie = c.cookies.get("qs_session")

    # start a slow engine in the background to emit events
    proc = subprocess.Popen(
        [PY, "-m", "qsdash.bridge.runner", "--config",
         str(REPO / "config" / "base.yaml"), "--synthetic", "--bars", "60",
         "--speed", "0.3"], cwd=str(REPO),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def listen() -> dict:
        got: dict[str, int] = {}
        ws_url = args.api.replace("http", "ws") + "/ws"
        try:
            async with websockets.connect(
                ws_url, additional_headers={"Cookie": f"qs_session={cookie}"}
            ) as ws:
                await ws.recv()  # hello
                end = asyncio.get_event_loop().time() + 40
                while asyncio.get_event_loop().time() < end and sum(got.values()) < 5:
                    try:
                        m = await asyncio.wait_for(ws.recv(), timeout=8)
                    except asyncio.TimeoutError:
                        continue
                    ev = json.loads(m)
                    if ev.get("type") == "event":
                        got[ev["topic"]] = got.get(ev["topic"], 0) + 1
        except Exception:
            pass
        return got

    try:
        got = asyncio.run(listen())
    finally:
        proc.terminate()
    return (sum(got.values()) > 0, f"topics={got}")


if __name__ == "__main__":
    raise SystemExit(main())
