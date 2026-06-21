# quantsys — "Fully Unlocked?" Audit + Pillar Coverage (Parts A & B)

*Generated 2026-06-22. Read-only audit of the live system; engine stayed PAPER,
untouched. Every claim ties to a file:line or a reproducible result. Verified
firsthand this session: full config load, tier resolution, and 113/113 core engine
tests (the 16 broker-execution tests need the optional `pyotp` extra — environment,
not a regression).*

## Definitions confirmed in code

- **enable/disable** (hard): `cfg.<name>.enabled` — trend ✓, meanrev ✓, voloptions ✗,
  expiry ✗, factor ✗ (`config/schema.py`).
- **"incubating" badge** (soft, statistical): `bridge/recorder.py:111` →
  `incubating = stats.n_eff < kelly.ramp_obs (750)`. Pure maturity flag, **not** an
  on/off switch. While incubating the Kelly allocator caps a strategy at `ramp_floor
  0.08`, withdrawn to 0 if evidence is clearly negative (`allocation.py:50-52`);
  promotion to full weight is *earned* as `n_eff` passes 750 with positive realized
  `mu/var`.
- **paper exploration** (`bridge/live.py:126-134`): in PAPER mode the runner forces
  `kelly.explore_floor = 0.05` and `sizing.enforce_cost_gate = False`. So the live
  dashboard force-trades both strategies edge-agnostically with the cost gate OFF —
  which is **why** trend's Sharpe is measurable and **negative**: it is the honest
  after-cost cost of trading without an edge, not a throttle or bug.
- **Live tier:** equity ₹10.2L → **T2** (verified) → `max_instruments=3,
  max_strategies=2`. The full 25-name / 4-strategy universe only unlocks at **T4
  (≥₹1cr)**. So at live capital only trend+meanrev run, on 3 names.

## Part A — Unlock Matrix (6 criteria)

| # | Criterion | trend | meanrev |
|---|---|---|---|
| 1 | Clean PIT data on full intended universe | ⚠️ data clean/PIT, but tier-capped to 3 names at ₹10.2L | ⚠️ same; pairs need same-sector breadth the 3-name cap denies |
| 2 | Signals every bar (no NaN/stall) | ✅ emits every bar while positioned | ❌ 0 signals ever live / 0 trades in 7-yr backtests (no qualifying cointegrated pair) |
| 3 | Sized & routed to execution | ✅ Kelly→sizing→voltarget→exposure→cost-gate→OMS | ✅ mechanically (multi-leg hedge sizing correct); never exercised |
| 4 | Not throttled by incubating/flags/capital | ⚠️ correctly defunded by negative measured edge; tier-capped; paper-forced 0.05 | ⚠️ ~0 because it produces no signals (mu=0, kelly_f=explore floor) |
| 5 | Passing unit + integration tests | ✅ 113 core green | ✅ |
| 6 | Contributes PnL in backtest **and** paper | ❌ negative/microscopic (live Sharpe −1.67; best honest backtest deflated 0.018–0.11) | ❌ zero (0 trades → flat → Sharpe ~0) |

**Diagnosis (evidence, not guesses).** trend's negative Sharpe is **not** look-ahead
(engine is pure), inverted signal, or data gaps — it is force-paid costs (paper
explore) on a 3-name universe with no robust after-cost momentum edge (121-config
prior search: best hold-out deflated 0.11; CAGR 0.1–0.3% vs B&H 4–7%). meanrev is
**dormant**: large-cap same-sector pairs are not reliably cointegrated at tradeable z
with edge > 4× cost; the 3-name cap + 1530-bar warmup + strict filters make it
structurally silent. Decisive cross-check: forcing the gate OFF unlocks only a
trickle (daily equity) or losses (intraday/futures) — **the binding constraint is
absence of edge, not gate strictness.** The Kelly sizer keeping exposure tiny is the
system working correctly.

## Part B — Pillar Coverage Matrix

| Pillar | Exists? | Module | Notes |
|---|---|---|---|
| 1 Trend / TS-momentum | ✅ yes | `strategies/trend.py` | complete; edge weak/absent after cost (not an engineering gap) |
| 2 Equity factor / market-neutral | ❌ was missing → **now built** | `strategies/factor.py` + `research/` | the one true structural gap; built & validated this session — see `PILLAR2_FACTOR_RESEARCH.md` |
| 3 Stat-arb / pairs | ✅ yes | `strategies/meanrev.py` | **classification: this is true cointegration pairs, not single-name MR**; dormant (no qualifying pairs) |
| 4 Event-driven | ⚠️ partial | `strategies/expiry.py` (+`voloptions.py`) | one disabled/unvalidated monthly-expiry hypothesis; vol-carry sleeve disproven. Missing: earnings/PEAD, index-rebalance, corporate actions + their feeds |

**Pillar 2 outcome (this session):** built on a survivorship-free ~3,450-name NSE
panel; **no config clears the gate** (best OOS deflated 0.141, and that one is just
market beta), but **market-neutral momentum survives the hold-out positive** (OOS
Sharpe ~0.57, beta 0.07, PBO 0.11) — the second OOS-survivor in the project after
BANKNIFTY-PCR, still marginal/sub-gate. Full detail: `docs/PILLAR2_FACTOR_RESEARCH.md`.
