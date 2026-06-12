"""Local smoke test: login -> overview -> data endpoints -> control guard.
Usage: python scripts/smoke.py <username> <password> <totp_secret>
"""

import json
import sys

import httpx
import pyotp

BASE = "http://127.0.0.1:8000"
user, pw, secret = sys.argv[1], sys.argv[2], sys.argv[3]

c = httpx.Client(base_url=BASE, timeout=20)

r = c.post("/api/auth/login", json={"username": user, "password": pw,
                                    "totp": pyotp.TOTP(secret).now()})
print("login:", r.status_code)
r.raise_for_status()
csrf = c.cookies.get("qs_csrf")

ov = c.get("/api/overview").json()
print("overview: mode=%s equity=%s engine=%s hb_age=%.1fs stale=%s" % (
    ov["mode"], ov["equity"]["value"], ov["engine"]["status"],
    ov["engine"]["heartbeat_age_s"] or -1, ov["engine"]["stale"]))

dec = c.get("/api/decisions?limit=3").json()
print("decisions: total=%d" % dec["total"])
if dec["rows"]:
    d0 = c.get(f"/api/decisions/{dec['rows'][0]['id']}").json()
    print("  last: tier=%s regime=%s audit_events=%d orders=%d" % (
        d0["tier"], d0["regime"], len(d0["audit"]), len(d0["orders"])))

pos = c.get("/api/positions?status=open").json()
print("open positions:", len(pos))
closed = c.get("/api/positions?status=closed&limit=5").json()
print("closed positions sample:", len(closed))
if closed:
    ex = c.get(f"/api/trades/{closed[0]['id']}/explain").json()
    print("  explain[%d]: entry_decision=%s fills=%d exit_reason=%r" % (
        closed[0]["id"],
        (ex["entry"]["decision"] or {}).get("id"),
        len(ex["fills"]),
        (ex["exit"]["rationale"] or {}).get("reason")))

eq = c.get("/api/equity-curve?max_points=10").json()
print("equity points (downsampled):", len(eq))
met = c.get("/api/pnl/metrics").json()
print("metrics:", json.dumps({k: met.get(k) for k in
      ("n_days", "sharpe", "max_drawdown", "hit_rate", "n_closed_trades",
       "total_fees", "cost_drag_bps")}, default=str))
attr = c.get("/api/pnl/attribution?by=strategy").json()
print("attribution buckets:", [(a["bucket"], round(a["net_pnl"])) for a in attr])
strat = c.get("/api/strategies").json()
print("strategies:", [(s["strategy"], round(s["kelly_f"], 4)) for s in strat])
reg = c.get("/api/regime/current").json()
print("regime:", reg.get("label"), reg.get("source"))
rl = c.get("/api/risk/limits").json()
print("risk limits: gross=%s audit_events=%d" % (rl["gross_exposure"],
                                                 len(rl["audit_events"])))
cap = c.get("/api/capital").json()
print("capital: tier=%s ladder_len=%d" % (cap["tier"], len(cap["tier_ladder"])))
tables = c.get("/api/db/tables").json()
print("db tables:", len(tables))
q = c.post("/api/db/query", json={"sql": "SELECT count(*) AS n FROM decisions"},
           headers={"X-CSRF-Token": csrf}).json()
print("db query decisions count:", q["rows"][0][0])

# control guard: kill without reauth MUST 403
r = c.post("/api/control/kill", json={"action": "kill"},
           headers={"X-CSRF-Token": csrf})
print("kill without reauth -> %d (expect 403)" % r.status_code)
assert r.status_code == 403

print("SMOKE OK")
