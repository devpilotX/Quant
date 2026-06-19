# quantsys — Maximum Honest Potential Audit

*Generated 2026-06-19. Research / read-only: the live engine stayed **PAPER**, untouched
(`QS_LIVE_ARMED=0`, mode=paper, paper_capital=₹10L, Up 39h+ — every run used throwaway
`docker compose run --rm` containers). Gate **ON**, real costs, walk-forward OOS + a true
hold-out, deflated Sharpe with **trial count = configs tried**. No gate loosening (except a
clearly-labelled forced diagnostic), no forced trades in any reported result, no synthetic data,
no look-ahead. Key runs persisted to the dashboard Backtest viewer (`backtest_runs` ids 43–46).*

> **Verdict: (c) NO EDGE.** Across **121 honest configurations** (every strategy, instrument
> basket, capital tier, and a guarded trend parameter sweep), the **maximum honest potential of the
> existing machinery is a weak, non-robust, microscopic positive drift**. The single best config on
> a true 2024→2026 hold-out reached **Sharpe ≤ 0.85 but deflated Sharpe ≤ 0.11** (gate needs ≥ 0.95)
> and **CAGR ~0.1–0.3% vs buy-and-hold 4–7%**. **Nothing clears the gate; nothing beats
> buy-and-hold.** `meanrev` and `expiry` take **0 trades** in every cell — the entire potential
> rests on daily `trend`, and even that is overfit/regime-dependent (it shrinks sharply on the
> hold-out).

---

## PART 0 — Ground truth & protocol

1. **Harness / safety.** All sweeps ran in throwaway containers against the `quant_histdata` data
   volume, reusing `walk_forward` / `compute_metrics` / `deflated_sharpe` / the real `CostModel`.
   Live engine verified untouched throughout (paper, `QS_LIVE_ARMED=0`).
2. **Baseline anchor.** Prior best honest result: daily trend on Top-10/NIFTY — net PF > 1 but
   deflated ≤ 0.55, CAGR ~0.3% ≪ buy-and-hold. This audit tests whether *any* safe lever beats it.
3. **Anti-overfitting protocol (pre-registered before running):**
   - **Nested hold-out.** Select **only** on the **2017→2023** walk-forward OOS; evaluate the single
     winner **once** on the untouched **2024→2026** hold-out (`run_backtest(score_from=2024-01-01)`
     → pre-hold-out bars are warmup-only, no look-ahead).
   - **Selection rule:** maximise 2017→2023 walk-forward OOS **net profit factor** among cells with
     **n_trades ≥ 30**; tie-break Sharpe. Chosen before seeing any hold-out number.
   - **Deflated Sharpe trial count = total configs tried = 121.** Reported honestly.

---

## PART 1 — The search (121 configs)

| Stage | What | Cells |
|---|---|---:|
| A | {trend, meanrev, expiry, trend+meanrev, all-3} × {NIFTY, Top-10, Top-20, Full-25} × {₹10L, ₹1cr}, default params | 40 |
| B | trend param sweep — tf∈{1,6}, ema_slow∈{40,80,120}, donchian∈{20,55,100}, entry∈{.30,.45,.60} — Top-10 @₹1cr (54) + Full-25 tf=6 region @₹1cr (27) | 81 |
| **Total honest trials** | | **121** |

**Coverage notes (honest scoping):** voloptions is covered separately in `VOL_SLEEVE_RESEARCH.md`
(no edge — mid-price illusion, fails gate even cost-free). Timeframes: this sweep is **daily** —
prior missions (`BACKTEST_PL_REPORT.md`, `NIFTY_BANKNIFTY_BACKTEST_REPORT.md`) already proved
**15-min = honest 0 trades / forced gross-negative** and **5-min worse**, and the HMM cost makes an
intraday param sweep impractical; **1-min was not fetched** (intraday is already dominated by cost
drag — negative expected value of the exercise). **Futures** were excluded from the sweep: the
roll-adjusted series is only ~2.4 y (2024→2026), too short for the trend warmup, and prior work
showed 0 honest trades / net-negative.

### What the search found
- **`meanrev`: 0 trades in all 8 cells** (NIFTY/Top-10/Top-20/Full-25, both capitals) — never finds
  a tradeable cointegrated pair even on the full 25-name universe over 7 years.
- **`expiry`: 0 trades in all 8 cells** on daily equities.
- ⇒ **the ensemble = trend alone**; all multi-strategy cells are identical to trend-only.
- **`trend` (only contributor):** best 2017→2023 search-OOS cells — Top-10 @₹10L default
  (netPF 3.21, Sharpe 1.47, deflated 0.52, but **63 trades / +₹10.8k = +0.1% over 7 y**);
  Top-10 @₹1cr tuned tf6/ema40/donchian100/entry0.6 (netPF 2.16); Full-25 @₹1cr tf6/ema80/dc55/en45
  (netPF 1.77, deflated 0.20). **tf=1 (true-daily) is uniformly net-negative**; trend only works on
  the ~6-day swing horizon.

---

## PART 2 — The ceiling (true hold-out, 2024→2026, deflated n_trials=121)

| Config (selected on 2017→2023) | n_tr | net PF | Sharpe | **deflated** | CAGR | net P&L | **vs Buy&Hold** |
|---|---:|---:|---:|---:|---:|---:|---|
| **WINNER: Top-10 trend, default, ₹10L** | 62 | 1.26 | 0.33 | **0.018** | 0.10% | +₹2.2k | **B&H +10.9% (4.3% CAGR)** |
| Top-10 trend, default, ₹1cr | 297 | 1.23 | 0.49 | 0.035 | 0.14% | +₹20k | B&H +10.9% |
| Top-10 trend, tuned, ₹1cr | 283 | 1.71 | **0.85** | **0.111** | 0.27% | +₹53k | B&H +10.9% |
| Full-25 trend, tuned, ₹1cr | 524 | 1.05 | 0.25 | 0.014 | 0.06% | +₹5k | B&H +17.3% (6.7% CAGR) |

**Tier-ladder on the hold-out (does more capital/leverage unlock anything?) — no:**

| Config | ₹10L | ₹50L | ₹1cr | ₹5cr |
|---|---|---|---|---|
| Top-10 trend tuned | PF 0.87 / defl 0.008 | PF 1.21 / 0.024 | PF 1.71 / **0.111** | PF 1.64 / 0.101 |
| Full-25 trend tuned | PF 1.45 / 0.031 | PF 1.12 / 0.022 | PF 1.05 / 0.014 | PF 1.01 / 0.008 |

More capital climbs the tier ladder (more names / trades) but **CAGR stays 0.03–0.27%** and
**deflated Sharpe never exceeds 0.11** — the ladder does not create an edge.

### Classification: **(c) No edge**
- The **pre-registered winner** shrinks from search netPF 3.21 / Sharpe 1.47 to **hold-out netPF
  1.26 / Sharpe 0.33 / deflated 0.018** — textbook overfit decay — and returns **+0.1% vs B&H
  +10.9%**.
- The single best-behaving hold-out cell (Top-10 tuned @₹1cr) touches **Sharpe 0.85** but has
  **deflated 0.11 ≪ 0.95** and **CAGR 0.27% vs B&H 4.3%** — and picking it *post-hoc on the
  hold-out* would itself be overfitting. It is the best of 121 tries showing residual luck, not a
  real edge.
- **No configuration clears the gate (Sharpe ≥ 0.8 AND deflated ≥ 0.95 AND P(SR<0) ≤ 0.10) and none
  beats buy-and-hold.**

**Maximum honest potential =** a weak, regime-dependent, microscopic positive drift on daily trend
over high-cap equity baskets (net PF ~1.2–1.7, CAGR ~0.1–0.3%), **dominated by buy-and-hold and
failing the robustness gate by a wide margin.**

---

## PART 3 — Decisive questions

### 3.1 Why is meanrev dormant?
`MeanRevStrategy` (`strategies/meanrev.py`) emits a signal only when `_rescan` finds a **same-sector,
same-kind tradeable pair** (among the tier's `max_instruments`) that passes **Engle-Granger ADF
p ≤ 0.05**, OU **half-life ∈ [10, 200]** strategy-bars, **split-half κ ratio ≤ 2.5**; then it enters
when **|z| ≥ z_entry (2.0)** (and < z_stop 3.5), not in cooldown/rearm; and the sized group must
clear the **cost gate (edge_R 0.15 × risk ≥ 4× round-trip cost at T2)**. **Live now:** `strategy_stats`
shows meanrev **mu = 0 exactly, var = 1e-10 (floor), kelly_f = 0.055 = pure explore floor, 0 signals
ever** — it has never found a qualifying pair, so its online edge estimator has no data. In this
audit it took **0 trades on the full 25-name universe over 7 years**. For it to ever trade live:
two same-sector names in the active universe must become genuinely cointegrated (ADF p ≤ 0.05,
stable κ, half-life 10–200), the spread must stretch to |z| ≥ 2, **and** the trade must clear 4×
cost — which has not happened at the current universe/costs. (Warmup is also large: `timeframe_bars
3 × (lookback 500 + 10) = 1530` bars.)

### 3.2 Gate strictness vs absence of edge — PROVEN
Forced (gate OFF + explore floor 0.35) vs honest, 2017→2023 search window:

| Target | Honest n / netPF / net | Forced n / grossPF / netPF / net |
|---|---|---|
| Top-10 @₹10L | 63 / 3.21 / +₹10.8k | 73 / 4.15 / 3.54 / **+₹13.8k** |
| Top-10 @₹1cr | 376 / 1.51 / +₹52.2k | 309 / 2.00 / 1.84 / **+₹69.4k** |
| Full-25 @₹1cr | 436 / 1.77 / +₹57.2k | 564 / 2.09 / 1.85 / **+₹84.2k** |

On **daily equity trend**, turning the gate fully OFF *raises* net P&L modestly (the gate is mildly
over-conservative for this profile) — so the few-trades behaviour is **partly** gate-driven here.
**BUT the magnitude is decisive: even with the gate removed, the best is +₹84k on ₹1cr over 7 years
= +0.8% total (~0.1%/yr), still failing deflation and losing to buy-and-hold ~10×.** And on the
horizons where the gate currently blocks most trades (15-min, futures — prior missions) forcing
unlocks **gross-NEGATIVE** P&L (losses). **Conclusion: the low exposure / few trades is driven by the
ABSENCE OF MEANINGFUL EDGE, not by gate strictness. Loosening the gate would unlock a trickle
(daily equity) or losses (intraday/futures) — never real, robust profit.** The Kelly sizer keeps
exposure tiny because the measured per-strategy edge is ~0 or negative (live trend mu = −1.2e-5,
Sharpe_ann −2.56) — exactly as designed.

---

## Conclusion

Searching the full honest space — every strategy, the trend parameter grid, all baskets, all tiers
₹10L→₹5cr, with a true hold-out and a 121-trial-deflated Sharpe — **does not unlock an edge.** The
machinery's ceiling is a weak, overfit, regime-dependent daily-trend drift (CAGR ~0.1–0.3%) that
**fails the go-live gate (best hold-out deflated 0.11 ≪ 0.95) and is beaten by buy-and-hold by
~10–16×.** meanrev and expiry contribute nothing; intraday and futures are worse (prior missions);
the vol sleeve is a separately-disproven mid-price illusion. **Verdict (c): no real potential found.
The honest maximum is "hold the index."** The GO-LIVE gate stays correctly CLOSED; stay paper.

*Reproducibility: `deploy/_audit/search.py` (+ `matrix.py` builders); 121 search trials + 4 hold-out
finalists + tier ladder; deterministic harness (fixed seeds, verified earlier this session); no
`quantsys`/`qsdash` package code changed (engine suite remains 129×3 green). Hold-out finalists
persisted to `backtest_runs` ids 43–46.*
