# quantsys

Auto-adaptive systematic trading system for Indian markets (NSE/BSE cash + F&O),
targeting execution via Angel One SmartAPI. **This repository currently contains
the complete, tested CORE — the decision brain.** Execution, backtest harness and
runners are the next phases (see Roadmap).

```
MarketState (bars, equity, positions)            <- built identically by backtest & live
        |
        v
DecisionEngine.decide()  — one deterministic pass per decision bar:
  1  risk pre-pass        kill switches, drawdown throttle          risk/engine.py
  2  capital tier         gates + interpolated params, hysteresis   portfolio/tiers.py
  3  regime               Gaussian HMM -> P(calm_trend/range/turb)  regime/
  4  gating               universe size, strategy count (tier)      engine/decision.py
  5  signals              trend + cointegration pairs ensemble      strategies/
  6  stop vetoes          hard/trailing stops, cooldowns            risk/engine.py
  7  Kelly allocation     f = clip(k*mu/var, 0, cap) * regime_w     portfolio/allocation.py
  8  sizing pipeline      risk-frac qty -> vol target -> exposure   portfolio/, risk/rules.py
                          caps -> lots -> cost gate
  9  order diff           anti-churn bands, urgency, exec style     engine/orders.py
        |
        v
Decision (targets, orders, full audit trail)     -> OMS (next phase)
```

## What is implemented and proven by tests (73 passing)

- **Sizing math** (`portfolio/sizing.py`): `qty = E * risk_frac_eff * f_s * share /
  (stop_distance * point_value)`; multi-leg groups derive hedge legs from the
  parent notional so pair ratios are exact by construction and survive every cap.
- **Fractional Kelly** with EWMA edge stats shrunk toward a zero-mean prior,
  incubation floor for young strategies, per-strategy and gross-f caps,
  regime re-weighting (`portfolio/allocation.py`, `strategies/base.py`).
- **Volatility targeting** of the proposed book against EWMA instrument
  covariance with diagonal shrinkage, clipped scaler (`allocation.py`).
- **Capital-tier ladder** T1 (Rs 1L) -> T6 (Rs 20cr): discrete gates step with
  10% hysteresis, continuous params interpolate in log-equity
  (`portfolio/tiers.py`). Changing only E re-sizes and re-gates the whole book —
  proven end-to-end in `tests/test_engine_e2e.py::test_capital_adaptation_only_E_changes`.
- **Risk stack, every veto tested**: per-instrument / sector / correlation-cluster
  / ADV / gross / net / margin caps (all monotone-shrink, group-joint);
  ATR hard stops with trailing for trend; stop cooldowns; daily-loss kill
  (auto re-arms next session); max-drawdown kill (manual re-arm); continuous
  drawdown throttle `risk_frac * (1 - dd/dd_max)`; reconciliation halt.
- **Regime overlay**: in-repo diagonal-Gaussian HMM (log-domain EM, seeded
  restarts, degeneracy guards) with probability-blended risk scalers — no
  cliff-edge regime flips — and a deterministic vol-percentile fallback ladder.
- **Strategies**: TSMOM+breakout trend (hysteresis entries/exits) and
  Engle-Granger/OU pairs (ADF gate, half-life band, split-half kappa stability,
  episode-frozen parameters, z-entry/exit/stop, time stop, re-arm latch).
- **Cost model** with the verified June-2026 Indian fee schedule and a
  square-root market-impact term; the tier cost gate blocks trades whose
  expected edge does not clear `min_cost_multiple x` round-trip cost.
- **Persistence**: every stateful component serialises to JSON; engine state
  round-trips with bit-identical subsequent decisions (tested).

## Key design decisions (autonomy mandate — gaps filled, with reasons)

1. **Decision clock defaults to 5-min bars** (not 1-min). At retail fee levels
   the cost gate would veto nearly everything signal-able at 1-min; 5-min keeps
   the same architecture (it is one config value) with honest economics.
2. **Vol targeting uses instrument covariance of the proposed book**, not
   strategy-return covariance as sketched in the spec: it is observable from
   day one, well-conditioned, and measures the actual quantity being capped.
   Strategy covariance still enters through per-strategy Kelly stats.
3. **Online-Kelly cold start**: a strategy cannot earn statistics without
   capital, so strategies with `n_eff < ramp_obs` get a small incubation floor,
   withdrawn early if evidence is already clearly negative.
4. **Edge stats run on virtual unit books** (f=1 sizing, proportional costs
   only) — removes the feedback loop between allocation and measured returns,
   and keeps flat Rs-20 fees (scale-dependent) out of a scale-free estimate.
5. **Two distinct halt semantics**: kill (market risk; our state trusted) =>
   flatten everything at market; reconciliation halt (state NOT trusted) =>
   freeze, no orders at all — flattening on top of a wrong book could double
   the error. Broker is always ground truth.
6. **Multi-leg trades are one Signal with legs**; every risk scaling operates
   group-jointly, so no cap can ever orphan one leg of a hedge. If any leg
   rounds to zero lots, the whole group is dropped.
7. **Pair parameters freeze per trade episode** (no mid-trade re-estimation
   drift); after a stop-out or time-stop the pair is latched until the spread
   revisits |z| < z_entry (prevents instant re-entry into a stuck dislocation —
   bug found and fixed by the time-stop test).
8. **Cost gate applies to NEW positions only** — gating a held position would
   force-pay the exit cost the gate exists to avoid. Equity trades are gated at
   delivery STT (worst case). The gate genuinely blocks tight-stop equity
   trading at small tiers: that is the Rs-1L cost trap enforced, not a bug.
9. **Custom ~150-line HMM instead of hmmlearn**: deterministic seeding,
   explicit degeneracy reporting, zero exotic dependencies. A degenerate or
   unconverged fit is refused; the detector falls back to a deterministic
   vol-percentile classifier, and to a neutral warmup state before that.
10. **Tier hysteresis (10%)** so an account oscillating around a threshold does
    not flap its configuration; demotion cascades properly after a crash.
11. **Anti-churn**: rebalance band (tier-dependent), min order notional, and
    conviction floors in trend holds; full exits and risk-reducing orders always
    pass.
12. **Timestamps are IST-naive** by contract; the live data layer normalises
    before the core sees anything, so backtest and live traverse identical code.
13. **Equity wipeout (E <= 0) is an immediate hard kill.**

## Verified market constants (June 2026 — re-verify quarterly)

- STT (Budget 2026, effective 2026-04-01): futures sell 0.05%, options sell
  0.15% of premium, equity delivery 0.1% both sides, intraday 0.025% sell.
- NSE transaction charges: equity ~0.00307%, futures ~0.00183%, options
  ~0.0355% of premium. GST 18% on (brokerage + exchange + SEBI); SEBI Rs 10/cr;
  stamp duty buy-side (delivery 0.015%, intraday 0.003%, futures 0.002%).
- Angel One: equity delivery Rs 0; otherwise min(Rs 20, 0.25%) per order.
- Lot sizes (NSE revision effective Jan 2026): NIFTY 65, BANKNIFTY 30.
  **Config lot sizes/ADV are warm-start fallbacks — the execution layer must
  refresh them daily from the Angel One instrument master.**

## Layout

```
src/quantsys/
  core/        types, calendar constants, MarketState
  config/      pydantic schema (ALL tunables) + YAML loader
  data/        BarHistory ring buffer, feature kernels (EMA/ATR/EWMA vol+cov)
  costs.py     Indian fee schedule + sqrt impact model
  regime/      Gaussian HMM + regime detector (fallback ladder)
  strategies/  base contract + edge stats + trend + meanrev (registry: drop-in alphas)
  portfolio/   TargetBook, Kelly, vol targeting, tier ladder, sizing engine
  risk/        exposure rules, kill switches, stops, reconciliation
  engine/      DecisionEngine orchestrator + order diffing
  persistence.py  atomic JSON state snapshots
config/base.yaml   baseline config + sample NSE universe
tests/             73 tests incl. cap-invariant and kill-switch e2e proofs
```

## Running

```powershell
# venv lives OUTSIDE OneDrive (sync churn corrupts venvs):
C:\Users\Dipan\.venvs\quant\Scripts\python.exe -m pytest tests -q
```

Secrets: real credentials belong in `.env` (gitignored), never in
`.env.example` or YAML.

## Roadmap (next phases, in order)

1. **Execution layer**: SmartAPI adapter behind a `Broker` interface, WebSocket
   2.0 tick streaming -> bar aggregation, OMS with token-bucket rate limiting
   (~10 orders/s, 3 hist req/s), order lifecycle + MPP handling, idempotent
   client order IDs, reconciliation loop wired to `RiskEngine.reconcile`.
2. **Backtester**: event loop that feeds `MarketState` into THIS engine
   unchanged; fills with the cost model; walk-forward harness + validation
   report (deflated Sharpe, regime-segmented stats, Monte Carlo on trade order).
3. **Paper runner** against live data; then guarded live runner (VPS deploy).
4. **Options/vol strategy** (defined-risk spreads, IV-vs-RV) as a registry
   drop-in; needs option-chain data infra first.
5. **Monitoring**: structured JSONL decision/audit logs (the audit trail is
   already produced), Telegram alerts, dashboard. SEBI algo registration via
   broker before any live order.
