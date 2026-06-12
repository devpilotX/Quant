# Decision log — dashboard & control plane

Every non-obvious choice and every gap filled under the autonomy mandate.

1. **Live mode is rejected by the engine until a real broker adapter exists.**
   The spec demands a paper↔live switch; the execution layer (Angel One
   SmartAPI) is a later phase. Faking it would violate "the dashboard never
   invents data". The ENTIRE safety chain (re-auth → phrase → cap → clean
   book → command queue) is built and tested; `CommandConsumer` returns
   `rejected: live execution adapter not installed`. Wiring the adapter flips
   one branch — the door and its four locks are already in place.

2. **Command queue instead of direct config writes.** The API never mutates
   engine behaviour; it enqueues `commands` rows the engine acks. Engine truth
   drives the mode badge (`mode` vs `mode_requested`). This also gives a
   complete operator→engine ledger for free.

3. **Event bus with two backends.** Redis pub/sub in production; Postgres
   LISTEN/NOTIFY when Redis is absent (the Windows dev box). Same envelope,
   one interface (`bus.py`). NOTIFY is transactional → events can't outrun
   rows. The PG listener uses a sync connection on a daemon thread because
   psycopg async I/O can't run on uvicorn's Windows Proactor loop.

4. **PaperBroker fills at bar close with the engine's own CostModel** —
   brokerage/STT/exchange/SEBI/stamp/GST/slippage/impact per fill, persisted
   as a JSON breakdown. Honest paper economics, same code path that the cost
   gate uses. Equity feeds back into MarketState so sizing reacts (capital
   adaptation live, not just in tests).

5. **Attribution goes to the position's strategy, not the order's.** Exit
   order intents from pure unwind diffs carry no strategy tag; attributing
   realized P&L to the opening strategy keeps buckets meaningful. Entry fees
   are attributed too (fees hit the bucket on every fill).

6. **Operator config overrides rebuild the engine.** Engine components copy
   config values at __init__, so in-place mutation is unreliable. Overrides
   apply via: mutate AppConfig → new DecisionEngine → `load_state()` round
   trip (bit-identical by core tests). Allowlist of 9 tunable keys; anything
   else is a code change, not a knob.

7. **Synthetic data runner** (correlated GBM with a vol-regime cycle) so the
   complete stack runs before the live feed exists. Clearly labelled
   `data_source=synthetic` in the engine heartbeat; the runner takes
   `--replay DIR` for real bars with zero code change. At ₹10L the cost gate
   correctly vetoed every signal (the documented small-capital cost trap);
   dev data is generated at ₹5cr where futures clear the gate.

8. **Hand-rolled UI kit instead of shadcn/ui.** ~250 lines of Card/Table/
   Badge/Modal/Gauge we fully control vs a large dependency tree the spec's
   look doesn't need. TanStack Query + a small LiveProvider replace Zustand —
   the only true client state is the WS connection itself.

9. **Single-origin everywhere.** SameSite=Strict cookies don't survive
   cross-origin XHR, so dev mode proxies /api and /ws through Next rewrites
   (verified: Next 16 dev/start proxies websocket upgrades) and production
   routes them in nginx before Next sees them. No CORS gymnastics, no token
   in localStorage, CSRF surface minimal.

10. **Heartbeat staleness is first-class.** `engine_status.last_heartbeat`
    must be < 30 s old or every page shows engine STALE; the UI also polls
    REST every 5 s as a fallback when the stream is down ("degrade visibly,
    never silently").

11. **DB admin = pgweb container (full) + built-in read-only browser.** pgweb
    has no auth of its own → nginx IP-gates /dbadmin in addition to TLS. The
    in-app browser allows single-statement SELECT/EXPLAIN inside a read-only
    transaction with a keyword blocklist — safe to expose behind the session.

12. **Tests run on SQLite** (JSONB→JSON variant, BigInt→Integer PKs) so CI
    needs no Postgres; the NOTIFY publisher no-ops off-postgres. The
    PG-specific paths (LISTEN/NOTIFY, JSONB) are covered by the live smoke
    scripts (`scripts/smoke*.py`) which ran green against Postgres 18.

13. **Timestamps are naive IST everywhere** (engine contract). One clock in
    the DB beats mixed-zone correctness theatre; the VPS must run with TZ
    set appropriately and the live data layer normalises at the boundary.

14. **timescale/timescaledb image, conditional hypertable.** Local Postgres 18
    (no extension) runs the identical migration chain; on the VPS,
    `market_bars` becomes a hypertable. `equity_curve` keeps a plain id PK
    (hypertables require the partition column in unique indexes).

15. **Angel One postbacks are HMAC-verified.** Angel's native postback doesn't
    sign payloads, so the deployment fronts it with a secret path + relay
    that adds `X-QS-Signature`; the endpoint enforces the signature whenever
    `ANGEL_WEBHOOK_SECRET` is set and dedupes on (order id, status, filled).
    Unknown broker orders are recorded and flagged as reconciliation events,
    never silently dropped.

16. **Operator bootstrap via CLI, not a signup page.**
    `python -m qsdash.cli create-operator` prints the TOTP otpauth URI once;
    there is no registration endpoint, no password reset over HTTP.

## Known gaps (deliberate, next phases)

- Angel One execution adapter + live data feed (engine roadmap phase 1) —
  after it lands: flip `set_mode` rejection, wire postback ordertag to
  client_order_id, refresh lot sizes/ADV daily from the instrument master.
- Backtest harness writes `backtest_runs` (roadmap phase 2); the viewer is
  ready and waiting.
- Watchdog container that alerts when the engine heartbeat goes stale at the
  infra level (UI + alert exists; an out-of-band cron belongs on the VPS).
- Candle chart with trade markers is built (`CandleChart`) but not yet placed
  on a page — positions/explain views link by data; add a /chart view when
  the live feed gives it real bars worth watching.
