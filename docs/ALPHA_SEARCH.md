# quantsys — Real-Alpha Search (living log)

*Goal set by owner: a strategy earning **12–15%/yr net**, paper-validated then taken live. The
existing machinery (trend / meanrev / expiry / futures / daily vol-sleeve) is proven **no-edge**
(see the other `docs/*REPORT*.md` / `*AUDIT*.md`). This log hunts for **genuinely new signal
sources** under the same honest bar: real costs, IS vs **true hold-out (2024→2026)**, deflated
Sharpe with trial count = #variants tried, **beaten only if it clears the gate AND beats
buy-and-hold.** Research only; PAPER; `QS_LIVE_ARMED=0`; nothing deployed.*

> **Status after Probe 1: still no edge.** Reality check on the target — **12–15%/yr is roughly
> NIFTY's own long-run return (~12%)**, i.e. it is historically achievable by *holding the index*,
> not yet by any algo here. Beating the index with genuine alpha remains unproven.

---

## Probe 1 — Cross-sectional (rank-based) momentum — **NO EDGE**

A new signal class vs the engine's per-instrument time-series trend: each month, rank the 25-name
daily universe by past momentum, hold top-K (long-only) or long top-K / short bottom-K (market-
neutral). Real delivery costs (14.1 bps/side via `CostModel`), daily equity curve, **36 variants**
(lookback {252,126,63} × skip {21,0} × K {3,5,8} × {LO, LS}), deflated at n_trials=36.

| Variant | IS 2017–2023 | **Hold-out 2024–2026** |
|---|---|---|
| Best **long-only** (lb63/sk21/K5) | CAGR 19.7%, Sharpe 0.99, defl 0.67 | **CAGR −10.5%, Sharpe −0.52, defl 0.0015** |
| Best **long-short** (market-neutral, pure alpha) | CAGR 2.5%, Sharpe 0.22, defl 0.06 | **CAGR −28.3%, Sharpe −1.65, defl 0.0** |
| **Equal-weight buy & hold (benchmark)** | **CAGR 19.3%, Sharpe 1.14** | **CAGR +7.1%, Sharpe 0.58** |

**Verdict — NO EDGE.** The long-only "19.7%" is **market beta, not alpha**: it doesn't beat
equal-weight buy-and-hold even in-sample (Sharpe 0.99 < 1.14) and **inverts to −10.5% on the
hold-out**. The **market-neutral** version (beta stripped — the true alpha test) has **no in-sample
edge (Sharpe 0.22) and −28% out-of-sample**. Deflated Sharpe ≈ 0 on the hold-out. Fails the gate.

**Caveat (important):** 25 mega-cap index heavyweights is a **narrow** universe for cross-sectional
factors (low dispersion, all highly correlated). This result is decisive *for the tradeable
universe we have*, but not a final verdict on the factor in general — a proper factor test needs
**breadth** (NIFTY-200/500), which would require fetching a broad daily universe (free via NSE
bhavcopy).

---

## Probe 2 — Option OI / PCR positioning (non-price flow signal) — **MARGINAL (first OOS-survivor)**

Built daily Put/Call **open-interest ratio** (PCR) for NIFTY & BANKNIFTY from the stored chains
(2017→2026) and tested it as a long/flat/short index-futures **timing** signal (contrarian &
momentum, lookback {20,60,120}, z-threshold {0.5,1.0}; 24 variants, deflated at n_trials=24; ~3 bps/side
futures cost). Signal at close *d*, return earned *d+1* (no look-ahead).

| Signal | IS 2018–2023 | **Hold-out 2024–2026** | Buy&Hold (hold-out) |
|---|---|---|---|
| **NIFTY** PCR (best) | Sharpe 0.48, defl 0.21 | Sharpe 0.06 | 3.7% / 0.33 — **no edge** |
| **BANKNIFTY** PCR-contrarian L60 | **Sharpe 1.145, CAGR 15.6%, defl 0.79, maxDD 10%** | **Sharpe 0.481, CAGR 4.3%, defl 0.11** | 7.0% / 0.486 |

**BANKNIFTY PCR-contrarian is the first signal in the whole project that survives the hold-out
positive** and is param-robust (multiple nearby configs Sharpe 0.7–1.1 IS), economically motivated
(option positioning = real flow), with low drawdown. **But it is MARGINAL, not an edge:**
- **Fails the gate** (hold-out deflated **0.11** ≪ 0.95; IS 0.79 < 0.95).
- **Cost-fragile** — full-sample Sharpe 0.98 @3 bps → 0.45 @10 bps → **−0.31 @20 bps**. Real retail
  BANKNIFTY-futures round-trip (slippage+impact) is plausibly 5–15 bps, i.e. marginal-to-dead.
- **Regime-dependent / decaying** — strong 2020–2024 (Sharpe 1.0–1.8), **fading 2025 (0.32) and
  2026 (−0.74)**.
- **Doesn't beat buy-and-hold on hold-out return** (4.3% vs 7.0%), though similar Sharpe at lower DD.
- Only 1 of 24 variants (BANKNIFTY, not NIFTY) shone — multiple-testing risk is real.

**Verdict: MARGINAL — the best lead found, worth deeper validation, NOT deployable.**

### Probe 2 — DEEP VALIDATION (real cost, robustness, decay)
Re-ran with the **real BANKNIFTY-futures cost from `CostModel` = 4.5 bps/side** (flat ₹20 brokerage +
Budget-2026 STT 0.05% sell + 1.5 bps slippage; 1-lot impact omitted), across a 12-config robustness
grid (L∈{40,60,90,120} × thr∈{0.75,1.0,1.25}, contrarian).

- **Survives real cost & is param-robust:** **all 12 configs are positive on the hold-out**
  (Sharpe 0.33–0.89) — not one lucky spike. Best hold-out: L120/thr1.0 Sharpe **0.885** (defl 0.27),
  L90/thr0.75 0.816, L90/thr1.0 0.759. Several **beat BANKNIFTY buy-and-hold risk-adjusted**
  (hold-out Sharpe ~0.8 vs B&H 0.486) and can go short (downside protection).
- **NIFTY: confirmed NO edge** (IS Sharpe <0.5, hold-out mixed/negative) → the signal is
  **BANKNIFTY-specific** (banking-sector leverage/vol, or single-market overfit).
- **Still fails the gate:** best hold-out **deflated Sharpe ≈ 0.27 ≪ 0.95**.
- **DECAYING (the key red flag):** per-year (L60/thr1.0, real cost) 2020 **1.69**, 2021 **1.69**,
  2022 1.03, 2023 0.85, 2024 0.83 → **2025 0.19, 2026 −0.84**; trailing-1y Sharpe is now **negative**.
  ~1.5 years of fade after 5 strong years — plausibly crowding/regime; **unresolved and concerning.**

**Net: a real, robust-across-params, cost-surviving, OOS-positive signal — the only one in the
project — but MARGINAL (fails the strict gate) and apparently DECAYING. Not deployable, but the
first candidate that legitimately earns forward paper-validation IF one accepts the decay risk.**
Productionizing it is a *build* (new registry strategy + a live option-OI feed the engine doesn't
have today) — owner decision, not done.

---

## Remaining honest avenues (highest-value first)

1. **Breadth + factors** — fetch a broad liquid universe (NIFTY-200/500 daily, free bhavcopy) and
   retest cross-sectional momentum / short-term reversal / low-volatility with real dispersion.
2. **Positioning/flow signals from data already on hand** — we stored option **OI (call/put OI,
   PCR)** in the chains but have never used it; plus free **FII/DII flows** and **India VIX**. These
   are *non-price* signals, structurally different from everything tested.
3. **Intraday vol with paid quotes** — the daily vol-sleeve was a mid-price illusion; only real
   intraday bid/ask could revive it (paid data).

**Honest expectation:** none is guaranteed to clear 12–15% net — most retail systematic effort
does not beat the index after costs (this project keeps re-proving that). The disciplined path
stays: find edge that passes the gate on **years** of OOS first; paper-trade only as the final
operational dress-rehearsal; never select on a few good months.
