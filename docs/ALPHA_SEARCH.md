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
