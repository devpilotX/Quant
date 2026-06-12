# Go-live runbook (quant.devpilotx.com)

Order is fixed and gated. Do not skip a step. Every money-touching step is
guarded in code as well as here; this document is the human procedure.

## 0. Prerequisites (one-time, blocking)

- [ ] **Rotate Angel One credentials.** The previous key/PIN/TOTP were exposed
      (see `incident` note). Regenerate the API key, change the PIN, re-enrol
      TOTP. Put them ONLY in `deploy/.env` on the VPS. Confirm the SmartAPI app
      is "live"/approved and **SEBI algo registration** is done via the broker.
- [ ] **VPS == whitelisted IP.** `deploy/scripts/preflight.py` checks the VPS
      public IP equals the Angel One whitelisted static IP (80.225.240.46 on
      record). DNS for the domain already points there.
- [ ] Connect the `vps-control` MCP (restart Claude Code so `.mcp.json` loads)
      if you want the agent to drive the VPS; otherwise run the commands below
      over SSH yourself.

## 1. Deploy the stack (paper, real cert)

```bash
cd Quant/deploy && cp .env.example .env && nano .env     # fill secrets
docker compose up -d nginx                                # serves ACME challenge
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
  -d quant.devpilotx.com --email you@example.com --agree-tos --no-eff-email
docker compose restart nginx                             # fixes the cert error
docker compose up -d postgres redis
docker compose run --rm api python -m qsdash.cli init-db
docker compose run --rm api python -m qsdash.cli create-operator --username <you>
docker compose up -d api frontend pgweb
docker compose --profile paper up -d engine-paper        # paper on LIVE data
```

Verify: `python deploy/scripts/preflight.py --remote` shows TLS OK and (on the
VPS) public IP OK. Log in at https://quant.devpilotx.com.

## 2. Paper-on-VPS validation window (the first real test of live plumbing)

Run paper mode against the **live Angel One feed** for a meaningful window (a
few full sessions). This is the first time these are exercised — none can be
tested locally:

- [ ] WebSocket 2.0 tick feed flows; bars aggregate on the session clock.
- [ ] The Angel postback webhook reaches `/api/webhooks/angelone` (signed) and
      reconciles against internal orders.
- [ ] Reconciliation loop matches the broker book each cycle; no spurious
      freezes; a deliberately induced mismatch DOES freeze (no orders).
- [ ] Heartbeat stays < 30 s; a forced feed drop raises the alert and the UI
      shows STALE; the engine takes no new risk while stale.
- [ ] Telegram alerts deliver (fills, mode, drawdown, disconnect).

## 3. The backtester gate (blocks real money until passed)

Real money is impossible until a **non-synthetic** walk-forward shows robust
OOS edge after costs. Wire the historical NSE fetcher (Angel historical REST,
cached, point-in-time), then:

```bash
docker compose run --rm engine-paper \
  python -m quantsys.backtest.runstudy --replay data/nse --folds 6 --persist
```

The dashboard Backtest Viewer shows the verdict. The engine-side `livegate`
requires OOS Sharpe ≥ 0.8, deflated Sharpe ≥ 0.95, P(SR<0) ≤ 0.10. If the run
does not clear the bar, **fix or cut strategies — do not go live.**

## 4. Tiny real-money go-live (gated, supervised)

Only after 2 and 3 pass:

```bash
# on the VPS engine host, arm out-of-band (NOT settable from the UI):
export QS_LIVE_ARMED=1
docker compose --profile live up -d engine-live   # replaces engine-paper
```

Then, in the dashboard (a human watching):
1. Settings → set a **small** deployable-capital cap (e.g. ₹25,000 or the
   minimum that trades one lot).
2. Switch to LIVE: requires re-auth (password+TOTP) + typed `GO LIVE REAL
   MONEY` + the cap + a flat book. The engine-side gate re-checks adapter +
   `QS_LIVE_ARMED` + the passing backtest before flipping.
3. Watch the first fills: confirm broker fills, fees, and reconciliation match
   the dashboard. Use KILL/flatten instantly if anything looks wrong.

## 5. Scale gradually

Raise the deployable-capital cap one tier at a time, only after the previous
tier behaved correctly across several sessions. The tier ladder re-sizes and
re-gates automatically; you are only relaxing the cap.

## Rollback / panic

- Dashboard KILL → flatten everything at market, engine stops taking risk.
- `docker compose stop engine-live` → no further orders (open orders remain;
  cancel via broker terminal if needed).
- Switch back to PAPER in Settings (flatten_first) once flat.
```
