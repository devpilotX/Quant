# quantsys — Consolidated Backtest P&L (all segments)

*Generated 2026-06-18. Every number traces to a real run: DB `backtest_runs` (id 1–2) or
this project's `_audit/` run logs / measured tool outputs. **No real money** — all figures
are backtest/research at the stated capital base. Where a value wasn't computed by that run,
it says **"not measured."***

## ⚠️ Read this first — what the numbers are (and are NOT)
Three **run types**, never conflate them:
- **HONEST** = the system's real gate (cost-gate ON, normal Kelly). This is what the live
  engine would actually do. On real NSE equities it takes **0 trades** ("Gate CLOSED").
- **FORCED** = `explore_floor` + cost-gate **OFF**, used only to *measure the gross signal*.
  These trades are ones the honest gate **rejects**; their net P&L is hypothetical
  ("if you traded everything"), **not** tradeable profit.
- **RESEARCH-SIM** = the vol sleeve's custom held-to-expiry simulator (EOD-priced).

Capital bases differ per run (₹5cr / ₹100cr / ₹1cr) — stated in every row. **The LIVE paper
engine runs at ₹10,00,000 and is essentially flat** (the honest gate trades ~nothing; a
couple of paper-exploration validation trades only). **No real capital is deployed.**

---

## Summary — every run, one row

| # | Segment / Strategy | Type | Cap base | Range · bars · folds | Trades | Gross PF | Net PF | Defl. Sharpe | Net P&L (₹, at cap) | Verdict |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | Equity trend 5-min (`id=1`) | HONEST | ₹5cr | recent · ~2.5k · wf | **0** | — | — | — | **₹0** | no-edge (Gate CLOSED) |
| 2 | Equity trend 15-min (`id=2`) | HONEST | ₹5cr | →2026-06 · 10k · wf | **0** | — | — | — | **₹0** | no-edge (Gate CLOSED) |
| 3 | Equity trend 15-min (regime warmup) | FORCED | ₹5cr | 7.5k scored | 6,854 | **0.883** | n/m | n/m | −1,733,598 | gross-NEGATIVE |
| 4 | Equity trend 15-min (regime ON, IS) | FORCED | ₹5cr | 5k | 6,568 | **1.044** | n/m | n/m | −1,357,645 | in-sample only |
| 5 | Equity trend 15-min (regime, **OOS**) | FORCED | ₹5cr | 8k · 4 folds | 5,316 | **0.787** | n/m | **0.012** | n/m | regime vanishes OOS |
| 6 | Equity trend daily-6d | FORCED | ₹5cr | 2017–26 · 6 folds | 2,186 | 1.254 | 1.045 | g0.854 / n0.222 | +80,162 | lumpy, marginal |
| 7 | Equity meanrev daily-6d | FORCED | ₹5cr | 2017–26 · 6 folds | 150 | 0.902 | 0.778 | 0.035 | −303,192 | no-edge |
| 8 | Equity trend daily-6d | **HONEST** | ₹5cr | 2017–26 · 8 folds | 2,188 | 1.212 | **1.010** | g0.759 / **n0.137** | **+18,924** | break-even, not robust |
| 9 | Equity trend true-daily | FORCED | ₹5cr | 2024–26 · 6 folds | 772 | 0.917 | 0.726 | 0.031/0.000 | −59,753 | gross-NEGATIVE |
| 10 | Equity trend true-daily | FORCED | ₹100cr | 2024–26 · 6 folds | 1,008 | 0.904 | 0.723 | 0.023/0.000 | −8,146,612 | gross-NEGATIVE |
| 11 | **Futures** trend true-daily (roll-adj) | FORCED | ₹100cr | 2024–26 · 6 folds | 934 | 0.922 | **0.854** | 0.048/0.022 | −3,541,225 | cost helps, still net-neg |
| 12 | Expiry (month-end MR), shallow | FORCED | ₹5cr | 16k · 5 folds | 4,587 | **1.158** | n/m | **0.728** | n/m | promising (shallow) |
| 13 | Expiry, **deep frozen** | FORCED | ₹5cr | 6.5y · 8 folds | 11,402 | 1.041 | **0.115** | **0.332** | −5,415,878 | borderline luck / decayed |
| 14 | **Vol-selling** verticals (NIFTY+BNF) | RESEARCH-SIM | ₹1cr | 2018–26 · 6 folds | 2,457 | **1.156** | **1.104** | **0.354** | **+6,872,004** | **MARGINAL — survives COVID** |

*(n/m = not measured by that run; "wf" = walk-forward; "daily-6d" = daily clock with
`timeframe_bars=6` ≈ 6-day bars; "true-daily" = `timeframe_bars=1`.)*

---

## Per-segment detail

### Equity (the system's primary universe — 22 NSE large-caps)
- **Honest runs take zero trades.** `backtest_runs` id=1 (5-min, ₹5cr) and id=2 (15-min,
  ₹5cr) both returned **0 trades / "Insufficient OOS data — Gate CLOSED"**: the cost gate
  vetoes every signal (trend's ~0.12R edge can't clear ~20bps delivery-STT round-trip).
- **Forced gross tests** (to see if the *signal* has raw edge): 15-min trend is
  **gross-negative** (PF 0.883, net −₹1.73M at ₹5cr). With the regime filter wired in, the
  full-sample gross nudged to 1.044 — but **vanished out-of-sample** (OOS gross PF 0.787,
  deflated Sharpe **0.012**). The regime fix is a *correctness* fix, not edge.
- **Daily-6d trend** is the least-bad equity result: HONEST (cost-gate on) net PF **1.010**
  (+₹18,924 at ₹5cr) but **break-even and not robust** (net deflated 0.137; only 3/8 folds
  net>1 — carried by 2 trending years). True-daily on 2024–26 is **gross-negative** (0.90).
- meanrev (pairs) daily: no-edge (net PF 0.778).

### Futures (F&O) — roll-adjusted continuous daily, futures cost model
- Built 24 continuous series from free NSE bhavcopy (2024–26). The **futures cost benefit is
  real and measured**: same true-daily trend signal, matched 2024–26 window, ₹100cr base —
  **equity net PF 0.723 (−₹8.1M) → futures net PF 0.854 (−₹3.5M)**: cheaper costs roughly
  **halved the loss**. But the signal is gross-negative on the testable window, so futures
  **cannot rescue it** (still net-negative). (A ₹5cr run took 0 trades — F&O lot sizes
  unaffordable at ₹5cr; needs ₹50cr+.)

### Daily / multi-day horizon
- Covered above (rows 6–11). Best honest result is daily-6d trend at net PF **1.010**
  (break-even). Lower frequency reduces cost drag but does not create edge.

### Vol-selling options (the only crash-surviving lead)
> **⚠️ SUPERSEDED (2026-06-19) — see `docs/VOL_SLEEVE_RESEARCH.md`.** Firm-up research downgraded
> this from "MARGINAL" to **no robust edge**: the result is a mid-price illusion (break-even at ~1%
> entry half-spread / ~2% full bid/ask; net-negative beyond), it **fails the go-live gate even at
> zero cost** (deflated 0.37 ≪ 0.95), it was **net-negative through a second crash (Feb-2018)**, and
> it is really a **long-gamma** (not premium-selling) book. The numbers below stand as the
> zero-cost / mid-price baseline only.
- Free daily NIFTY+BANKNIFTY chains, **2,083 days 2018→2026**; defined-risk vertical spreads,
  held-to-expiry, real `VolOptionsStrategy` signal + real option cost model, ₹1cr base.
- **Gross +₹10,015,366 · fees ₹3,143,363 · Net +₹6,872,004 · Gross PF 1.156 · Net PF 1.104 ·
  net deflated Sharpe 0.354 · 2,457 spreads · 4/6 folds net>1.**
- Per-fold net PF: `1.086, 1.534, 0.845, 1.247, 1.134, 0.844`.
- **Crash windows:**
  - **COVID-2020** (entries 2020-02-20→04-30): **65 trades, net PF 2.255, net +₹1,354,659,
    worst single trade −₹62,691 (contained — defined-risk held, no blow-up).** It **survived
    the crash** and was net-positive through it (sells the IV spike, harvests mean-reversion).
  - **Feb-2018:** not traded — fell inside the strategy's 82-day RV warmup (data starts
    2018-01; first signal ~May-2018). So only COVID (the more severe crash) was tested.
- **Caveats (why MARGINAL, not confirmed):** deflated Sharpe 0.354 < 0.95 robustness bar;
  EOD-close pricing (no bid/ask) → COVID profit almost certainly optimistic; held-to-expiry
  only. A first run was corrupted by a sizing artifact (₹27B) and **fixed** before these
  numbers (reject degenerate spreads, clip to defined-risk bounds, cap lots); deterministic
  on re-run.

---

## Total fees / costs paid (per run, at stated cap)
| Run | Fees (₹) | Gross (₹) | Net (₹) |
|---|---:|---:|---:|
| 3 — eq trend 15m forced | 1,461,307 | −272,290 | −1,733,598 |
| 6 — eq trend daily-6d forced | 316,861 | +397,023 | +80,162 |
| 7 — eq meanrev daily forced | 180,135 | −123,057 | −303,192 |
| 8 — eq trend daily-6d HONEST | ~320,550 | +339,474 | +18,924 |
| 13 — expiry deep | 5,518,135 | +102,258 | −5,415,878 |
| 14 — vol-selling | 3,143,363 | +10,015,366 | +6,872,004 |

*(Other runs reported gross PF + totals but not a separate fee line → "not measured" there.)*

---

## What this means
**Across every segment — equity (5-min/15-min/daily), futures, expiry, meanrev — there is no
robust, after-cost, out-of-sample edge.** The honest equity backtests take **0 trades**; the
forced gross tests are negative or in-sample-only; daily trend is break-even; expiry decayed
to borderline-luck with more data; futures' cheaper costs help but can't rescue a
gross-negative signal. **The single exception is the vol-selling sleeve** — the only run that
is net-positive after costs *and* survives a crash fold — but it is **MARGINAL** (net PF
1.104, deflated 0.354) and needs intraday data + the Feb-2018 window to be trusted.

**Capital-base reminder so no figure is misread as real profit:** every P&L above is
backtest/research at ₹5cr / ₹100cr / ₹1cr sizing bases; the **live paper account is ₹10L and
flat**; **nothing is deployed live; `QS_LIVE_ARMED=0`.** The only number that is both
net-positive and from a crash-surviving run is the vol sleeve's **+₹6,872,004 on a ₹1cr base
(research simulator, EOD-priced, marginal)** — promising, not proven, not profit.
