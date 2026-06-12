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

## Phase 1-4 decisions (backtester → execution → options → deploy)

17. **Backtester drives the real `decide()`** — SimBroker fills at bar close
    with the engine's CostModel (slippage+impact are already explicit cost
    lines, so close-fill doesn't double-count). The equity==cash+MTM identity
    is asserted every bar. No strategy logic is re-implemented anywhere.

18. **Walk-forward for an ONLINE engine.** There is no separate fit step to
    freeze, so "train" windows are in-sample warm-up (decisions run, estimators
    converge, NO execution) and the following window executes + scores OOS, with
    engine state carried forward exactly as live. The IS reference is a shadow
    run on the train window (optimistic by construction — the gap IS the
    degradation we report).

19. **Anti-self-deception is mandatory, not optional.** Deflated Sharpe
    (Bailey/LdP) with `n_trials` fed from the sensitivity sweep, plus
    block-bootstrap Monte Carlo on returns. The verdict is computed, not
    editorialised: synthetic data ALWAYS yields "GATE CLOSED" because synthetic
    GBM has no real edge (and indeed shows negative post-cost Sharpe).

20. **Two independent live locks.** The dashboard enforces the operator chain
    (re-auth + phrase + cap + clean book). The engine `livegate` independently
    requires: a connected adapter, `QS_LIVE_ARMED` set out-of-band (never from
    UI), AND a non-synthetic backtest clearing the robustness bar. The paper
    runner reports `supports_live=False`, so real money is impossible on it.
    This replaces the Phase-0 unconditional "live not installed" rejection.

21. **Idempotent compact order ids.** `Q{MMDDHHMM}{symidx}{seq}` (+`-s{slice}`)
    is ≤20 chars so it is Angel's `ordertag` verbatim — postback correlation
    never breaks. Deterministic in (bar, symbol, seq) so a retried decision
    re-derives the same id and can't double-submit; the adapter also keeps a
    client→broker id map and refuses (not truncates) an oversized tag.

22. **Reconcile-first, freeze-not-flatten.** The live step reconciles against
    the broker (ground truth) BEFORE deciding; a mismatch calls
    `RiskEngine.reconcile` → halt (no orders). Flattening on an untrusted book
    could double the error — same kill-vs-halt semantics as the core.

23. **TWAP/AC slice one-per-pump**, slices never exceed available lots, lot-
    aligned, remainder spread across the first slices. Urgency (KILL/risk-
    reducing) orders sort first and cross the spread; new risk uses the tier's
    style. Any place error fails safe (leave unplaced) rather than spam.

24. **Options as a registry drop-in, never naked.** Defined-risk verticals
    only; a spread is ONE 2-leg Signal with `notional_ratio=-(p_long/p_short)`
    so the existing sizer yields equal-contract opposite-sign legs, and
    `stop_distance` = the spread's true max loss so risk-fraction sizing sizes
    by defined risk. DISABLED by default: needs a live chain feed in
    `state.extra['option_chains']` + `OptionUniverseManager` to register legs
    as instruments. Self-contained Black-Scholes (no SciPy).

25. **Instrument master wins, config is fallback.** `LiveRunner` merges the
    Angel master (lot/tick/token) over config values, which become labelled
    warm-start fallbacks — the README's daily-refresh requirement, enforced.

26. **Engine images by compose profile** (`demo`=synthetic, `paper`=live data
    no orders, `live`=real money). One image, three commands; `live` also needs
    `QS_LIVE_ARMED` in the env. Keeps a dashboard crash off the trading path.

## Known gaps (deliberate, next phases)

- **Historical NSE data fetcher** is the one remaining code gap before a real
  (non-synthetic) backtest can run: an Angel historical-REST puller (≤3/s,
  cached, point-in-time, expiry/roll/corporate-action aware) feeding
  `replay_bars`. The backtester, walk-forward, metrics, viewer, and the
  live-gate that consumes the result are all built and tested.
- **Live execution has been BUILT and unit-tested with a mock transport but
  never run against the real broker.** The real-broker shakedown is the
  paper-on-VPS window (GOLIVE.md step 2) — feed, postback, reconciliation,
  rate limits can only be validated against live Angel One.
- VPS deploy itself is pending: the `vps-control` MCP isn't connected this
  session (restart Claude Code to load `.mcp.json`), and live trading needs the
  rotated credentials. `deploy/scripts/preflight.py` + `docs/GOLIVE.md` make it
  a mechanical sequence once those two are in place.
- Watchdog container for infra-level heartbeat-stale alerting (UI + in-app
  alert exist; an out-of-band cron belongs on the VPS).
- Candle chart with trade markers (`CandleChart`) is built but not yet placed
  on a page — add a /chart view once the live feed provides real bars.
