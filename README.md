# quantsys

Auto-adaptive systematic trading system for Indian markets (NSE/BSE cash + F&O)
via Angel One SmartAPI. **Complete system: decision brain + event-driven
backtester + execution layer + dashboard control plane + standalone research
kit**, deployed in paper mode on a VPS.

## Status (2026-07-02) — Forward Study 2 running on the paper VPS

- **Deployed**: https://quant.devpilotx.com — paper engine on live Angel One
  data. `QS_LIVE_ARMED=0`: real money stays OFF, and the 2026-06-11 credential
  leak means rotation is a hard precondition for ever arming live (GOLIVE §0).
- **Research verdict unchanged**: the 2017–26 alpha search is CLOSED — nothing
  cleared the deployment gate (`docs/RESEARCH_CLOSEOUT.md`). What runs now is
  the closeout's one sanctioned continuation: a **pre-registered, forward-only
  paper study** (`docs/FORWARD_STUDY_2.md`, superseding Study 1 same-day after
  a day-1 defect review — verdict appended in `docs/FORWARD_STUDY.md`) at tier
  T6 (₹15cr paper float), **6 sleeves**: trend + cointegration pairs (P1/P3),
  **12L/12S market-neutral factor momentum on a self-seeded daily panel** (P2,
  primary candidate), expiry fade (P1), **down-shock event sleeve** (P4,
  frozen z3.5/hold10 research rule, promoted from the tracker; the 20:30
  zero-risk tracker continues as control), and **turn-of-month index tilt**
  (P1b). A 7th sleeve (short-term reversal) is implemented+tested but ships
  dark: its whole parameter grid was net-negative on 2017–26 data (rejected
  arm, documented). **Learned regime-conditional Kelly tilt** adapts sleeve
  allocation to which regimes each sleeve actually earns in (bounded, walk-
  forward, audited).
- **Day-1 fixes (2026-07-02, deployed with Study 2)**: exactly ONE decision
  per 15-min bar on the full cross-section (was 3–5 partial-universe decides),
  the session close bar is now decided (was never), decisions stamped at bar
  time, paper cash/realized persist across the daily recycle (was phantom
  equity), equity-scaled dust floor (was 1-share churn), weekly backtest timer
  moved off the broker maintenance window + socket timeouts (was hanging).
- **Universe**: 48 liquid large-caps + NIFTY/BANKNIFTY near-month futures
  (TATAMOTORS → TMPV/TMCV after the 2025 demerger). Index futures trade via
  **risk-capped min-lot promotion** (a 1-lot minimum ticket is allowed iff it
  risks ≤ 0.5% of equity).
- **Ops automation** (`deploy/systemd/`): pre-open engine recycle 08:50 IST
  (cash-safe), daily self-check 09:55 IST (GREEN/RED log + loud unit failure),
  down-shock tracker 20:30 IST, weekly backtest refresh Sat 11:00 IST; the
  websocket feed **parks outside session hours** (no overnight reconnect
  churn; pinned `smartapi-python==1.5.5` + `websocket-client==0.59.0`).

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
  strategies/  base contract + edge stats + trend/meanrev/expiry/factor/
               downshock/tom (+reversal, dark) — registry: drop-in alphas;
               _dailypanel.py = shared self-seeding daily-panel base
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

## Dashboard & control plane (built 2026-06-12)

`dashboard/` + `deploy/` contain the full live control plane for this engine:
FastAPI backend (auth: argon2id + mandatory TOTP, lockout, CSRF, re-auth
window; REST snapshots; websocket hub; command queue), the engine bridge
(Recorder + PaperBroker with the real CostModel + CommandConsumer), and a
Next.js 16 dark terminal UI with 12 live views including per-trade
explainability straight from the decision audit trail. Postgres holds every
decision/order/fill/position/equity tick; Redis (or PG LISTEN/NOTIFY) fans
events to the browser in real time.

- run locally: see `docs/DEPLOY.md` §4 · deploy: `docs/DEPLOY.md`
- architecture: `docs/ARCHITECTURE.md` · every design call: `docs/DECISIONS.md`
- safety: paper is default; LIVE requires fresh re-auth + typed phrase +
  deployable-capital cap + clean book, and the engine rejects live until the
  Angel One execution adapter (roadmap 1) ships.

## Backtester, execution & options (built 2026-06-12)

- **`quantsys/backtest/`** — event-driven backtester driving the real
  `decide()`; SimBroker fills with the CostModel; walk-forward IS-vs-OOS,
  deflated Sharpe, Monte-Carlo, sensitivity sweep; `runstudy` CLI persists to
  `backtest_runs`. **This is the go-live gate** — synthetic data correctly
  yields a CLOSED verdict; real OOS edge after costs is required to open it.
- **`quantsys/execution/`** — `Broker` interface + Angel One SmartAPI adapter
  (TOTP session, instrument master, idempotent rate-limited OMS, MPP-agnostic
  fills), WS tick→bar aggregation, reconciliation (broker=truth → freeze).
  Built and unit-tested with a mock transport; **not yet run against the live
  broker** (that's the paper-on-VPS step).
- **`quantsys/options/`** + `strategies/voloptions.py` — self-contained
  Black-Scholes + defined-risk vertical-spread vol sleeve (IV-vs-RV), a
  registry drop-in, **disabled by default** pending a live option-chain feed.
- **Two-lock go-live gate**: dashboard operator chain + engine `livegate`
  (adapter + `QS_LIVE_ARMED` + passing real backtest). See `docs/GOLIVE.md`,
  `docs/DECISIONS.md` (#17–26), and `deploy/scripts/preflight.py`.

Test counts: **191 engine tests**, **60 dashboard backend tests** (1 env-skip),
all green (2026-07-02, post-Study-2).

## Research tooling (standalone) — `quantsys/research/`

Self-contained, engine-isolated research kit built during the alpha search (final
verdict: no deployable edge — see `docs/RESEARCH_CLOSEOUT.md`). It imports without
the live engine/broker and is the recommended way to run any future **forward-only,
pre-registered** cross-sectional study. Free, point-in-time, survivorship-bias-free
NSE data — no paid vendor, no VPS needed.

- `bhavcopy.py` — full NSE **cash** daily panel from the public archive CDN (both the
  legacy `cm…bhav` and 2024-07+ UDiFF formats), on-disk cached. Corporate actions are
  handled via NSE's ±20% price-band rule (intraday return on split/bonus days).
- `fno.py` — NSE **F&O** daily: BANKNIFTY index-option OI → PCR, near-month futures,
  and **point-in-time single-stock-futures membership**.
- `factors.py` + `xs_backtest.py` — price/volume cross-sectional factors (momentum,
  low-vol, reversal, illiquidity) and a monthly beta-neutral L/S backtester that
  reuses the engine's real `CostModel` and metrics.
- `validation.py` — **PBO** (Probability of Backtest Overfitting, CSCV) and **purged
  & embargoed K-fold CV** (Lopez de Prado); pairs with `backtest/metrics.py`'s
  deflated Sharpe + Monte-Carlo bootstrap.

```python
from datetime import date
from quantsys.research import bhavcopy as bc
from quantsys.research import validation as V

panel = bc.build_panel(date(2017, 1, 1), date(2024, 1, 1))   # cached, survivorship-free
prices = bc.close_panel(panel)                                # date × symbol matrix

pbo = V.pbo_cscv(returns_matrix)        # T×N config returns -> overfit probability
folds = V.purged_kfold(n, n_splits=5, embargo_pct=0.02)      # leak-free CV splits
```

Network access is confined to the fetchers' download helpers; the parsers, factors,
and validators are pure and unit-tested (`tests/test_research.py`,
`tests/test_combine.py`) with no network. Data caches under `data_cache/` (gitignored).

## Roadmap (what actually remains)

1. **Rotate the Angel One credentials** (leaked 2026-06-11; still the hard
   blocker for any live arming) and fill Telegram alert creds in `deploy/.env`.
2. **Forward Study 2 first read: 2027-01-05** (`docs/FORWARD_STUDY_2.md`) —
   observational only; the livegate criteria stand unchanged.
3. **voloptions sleeve** stays disabled until a live option-chain feed +
   OptionUniverseManager exist.
4. Off-VPS backup replication (`deploy/backups/` rsync target) + a restore
   drill; SEBI algo registration via the broker before any real order.
