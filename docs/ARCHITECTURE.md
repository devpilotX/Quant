# quantsys control plane — architecture

```
                       ┌────────────────────────── VPS (quant.devpilotx.com) ─────────────────────────┐
                       │                                                                              │
  Browser (operator)   │   nginx :443 (TLS, HSTS, rate limits)                                        │
  ───────────────────► │   ├── /            → frontend  (Next.js 16, dark dense SPA)                  │
   session cookie      │   ├── /api/…       → api       (FastAPI: auth, REST snapshots, control)      │
   httpOnly+Strict     │   ├── /ws          → api       (websocket hub)                               │
   + CSRF header       │   └── /dbadmin     → pgweb     (IP-gated DB admin)                           │
                       │                                                                              │
                       │   api ──reads──────────► PostgreSQL (TimescaleDB) ◄──────writes── engine     │
                       │    │                       18 tables: decisions, orders, fills,   │          │
                       │    │                       positions, equity_curve, risk_events,  │          │
                       │    │                       regime_history, audit_log, commands…   │          │
                       │    │                                                              │          │
                       │    └─subscribe── Redis pub/sub (or PG LISTEN/NOTIFY) ◄──publish───┘          │
                       │                  same envelope either way                                    │
                       │                                                                              │
                       │   engine = quantsys DecisionEngine + qsdash bridge:                          │
                       │     Recorder      every decide() pass → decisions row (full audit) + events  │
                       │     PaperBroker   fills at bar close with the REAL CostModel fee breakdown   │
                       │     CommandConsumer  polls `commands` between bars; acks/rejects             │
                       │     (Angel One adapter = next phase; live mode rejected until it ships)      │
                       └──────────────────────────────────────────────────────────────────────────────┘
```

## Trust boundaries & invariants

1. **The engine is the only writer of trading truth.** The API writes only
   auth/audit/config/command rows. The dashboard renders DB rows verbatim —
   `decisions.audit` is the engine's own AuditEvent trail, never re-derived.
2. **Control actions are commands, not writes.** API → `commands` table →
   engine consumes, acts, acks with a result. `runtime_config['mode']` is
   flipped only by the engine, so the mode badge always shows engine truth.
   `mode_requested` shows the pending transition.
3. **Process isolation.** engine, api, frontend, postgres, redis, nginx are
   separate supervised containers; any dashboard component can die without
   touching trading.
4. **Events commit with rows.** With the PG bus, `pg_notify` fires inside the
   recorder's transaction — a client can never see an event for a row that
   rolled back. With Redis, publish happens after commit (at-least-once via
   client snapshot refetch on reconnect).
5. **Reconnect = resync.** The frontend invalidates every query on WS
   (re)connect; deltas are only trusted on top of a fresh snapshot. The
   connection state and engine heartbeat age are always visible; stale is
   rendered as stale.

## Auth model (single operator, real money)

- argon2id password + **mandatory TOTP** (pyotp, ±1 step skew).
- Sessions: 48-byte token, sha256-hashed at rest, httpOnly + Secure +
  SameSite=Strict cookie; sliding idle timeout (60 min) + absolute cap (12 h).
- CSRF: double-submit cookie → `X-CSRF-Token` header on every mutation.
- Lockout: 5 failures → 15 min; identical error for wrong user/pass/TOTP.
- **High-risk actions** (mode, capital, kill, flatten, config, strategy
  toggle) additionally require re-auth (password+TOTP) within a 5-minute
  freshness window — enforced server-side by `require_fresh_reauth`.
- Going LIVE additionally requires the typed phrase `GO LIVE REAL MONEY`,
  an explicit deployable-capital cap, and a flat (or explicitly flattened)
  paper book. All four checks are server-side.
- Every login/control action lands in `audit_log` with IP.

## Real-time pipeline

- Topics: decisions, orders, fills, positions, equity, regime, risk, alerts,
  engine (heartbeat), config, commands.
- Bus backends behind one interface (`qsdash/bus.py`): Redis pub/sub in
  production, Postgres LISTEN/NOTIFY when Redis is absent (Windows dev).
  The PG listener runs on a dedicated thread (sync psycopg) bridged into the
  event loop — immune to ProactorEventLoop limitations.
- WS hub (`qsdash/ws.py`): cookie-authenticated, topic-filtered fan-out,
  20 s ping. Oversized NOTIFY payloads degrade to `{ref:…}` and clients
  refetch — nothing is truncated.

## Mode & capital semantics

- `mode` ∈ {paper, live}; every trading row carries it; the two universes are
  never aggregated.
- Paper: `E(t)` = PaperBroker equity (cash + MTM), seeded from operator-set
  `paper_capital` (₹1L–₹20cr) — simulate any tier on demand.
- Live (when the adapter ships): `E(t)` from broker balance, clamped by
  `min(E, E×deployable_cap_frac, deployable_cap_abs)` in
  `Runner.effective_equity` — the algo cannot size beyond the cap.
- Kill semantics mirror the engine core: operator kill arms the
  max-drawdown latch (flatten everything, manual re-arm); reconciliation
  halt freezes with no orders at all (state not trusted).

## Repo layout (dashboard additions)

```
dashboard/backend/qsdash/
  config.py security.py db.py models.py deps.py bus.py audit.py notify.py
  main.py ws.py
  api/ auth control data dbadmin webhooks
  bridge/ recorder paper commands runner
  alembic/ (migrations; conditional Timescale hypertable)
  tests/ scripts/
dashboard/frontend/src/
  lib/ api format live
  components/ ui shell charts audit reauth
  app/ login + (dash)/{overview,positions,pnl,explain,strategies,regime,
                       risk,capital,backtests,logs,db,settings}
deploy/ docker-compose.yml nginx/ scripts/ .env.example
```
