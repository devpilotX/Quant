# quantsys — NIFTY / BANKNIFTY + Top-10 / Top-20 Backtest Report

*Generated 2026-06-19. Every number below traces to a real walk-forward run on real NSE
history, persisted to the dashboard **Backtest viewer** (`backtest_runs` ids **3–32**, label
prefix `MATRIX …`). **No real money** — the live engine is in **PAPER** (`QS_LIVE_ARMED=0`),
nothing was armed, no config/capital/risk was changed.*

> **Honest contract (Part 5).** Cost gate **ON**, normal fractional Kelly, walk-forward
> **OOS**, real Indian costs (STT/GST/exchange/SEBI/stamp + slippage + Rs20/0.25% brokerage),
> **no forced trades, no gate loosening, no synthetic data, no look-ahead.** Where the honest
> gate takes **0 trades**, that is reported as the result; a separate, clearly-labelled
> **FORCED** diagnostic (gate OFF + explore floor) is shown only to measure the *gross pre-cost
> signal* — it is **never** a tradeable number. Runs are **deterministic** (verified, identical
> SHA over two full passes). Test suite: **engine 129 tests ×3 green**, dashboard **44 green**.

---

## 0. TL;DR verdict

**No robust, after-cost, out-of-sample edge on NIFTY, BANKNIFTY (index or futures), or the
Top-10 / Top-20 NIFTY-50 equity baskets — at any tested horizon (daily / 15-min) or capital
(₹10L / ₹50L / ₹1cr).** The trend strategy produces a *weak positive after-cost signal* on the
equity baskets (net profit-factor > 1), but:

1. its **deflated Sharpe never approaches the 0.95 go-live bar** (best ≈ 0.55);
2. **CAGR is negligible (~0.1–0.3%)** because the honest Kelly + cost gate deploy almost no
   capital (edge is unproven, so `f` sits at the incubation floor — see exposure analysis);
3. **simple buy-and-hold beats it by 50–60 percentage points** over the window.

`meanrev` takes **0 trades** everywhere; **15-min** is honest-0-trades / forced-gross-negative;
**futures** are data-limited (2.4 y) and not meaningfully tradeable at retail capital. **The
GO-LIVE gate stays correctly CLOSED. Stay paper.** This is consistent with — and extends — the
prior `docs/BACKTEST_PL_REPORT.md`.

---

## 1. Data coverage (real NSE history, `quant_histdata` volume)

| Series | Symbols | Granularity | Window | Source |
|---|---|---|---|---|
| `daily/` | 22 orig + **ITC, LT, BHARTIARTL** (fetched for this report) + NIFTY | daily | **2017-01-02 → 2026-06-19** (~9.5 y); NIFTY index 2021→2026 | Angel 5-min → resampled |
| `nse15/` | 25 eq + NIFTY | 15-min | 2017 → 2026 | Angel 5-min → resampled |
| `nse/` | 25 eq | 5-min | 2017 → 2026 | Angel `getCandleData` |
| `fut_daily/` | NIFTY, BANKNIFTY + 22 stock futures | daily, roll-adjusted | **2024-01-01 → 2026-06-15 (~2.4 y only)** | NSE F&O bhavcopy |
| `optchain/` | NIFTY, BANKNIFTY | daily chains | 2018 → 2026 | (vol sleeve, prior report) |

**Coverage caveats (read every row against its window):**
- **Equities & NIFTY-index have ~9.5 y** → highest confidence.
- **Futures (NIFTY/BANKNIFTY and all stock futures) have only ~2.4 y** → **LOW confidence**; a
  2.4-y window cannot support a robust verdict and is too short for the trend warmup (below).
- **BANKNIFTY has NO spot series** — it exists only as roll-adjusted **futures** (2.4 y) + option
  chains. BANKNIFTY is therefore the **lowest-confidence** target here.
- **Data venue:** the equity OHLCV resolves to the **BSE** listing (so bar *volume* is BSE, a
  fraction of NSE); for these mega-caps **prices track NSE within paise**, and the engine sizes
  off the configured `adv`, not bar volume — so this does not affect the verdicts, but it is
  stated for honesty. The 3 new names (ITC/LT/BHARTIARTL) were fetched the *same* way for
  consistency with the existing 22.

**Baskets (NIFTY-50 by approx index weight, NSE Jan-2026):**
- **Top-10:** HDFCBANK, RELIANCE, ICICIBANK, INFY, ITC, TCS, LT, BHARTIARTL, AXISBANK, SBIN.
- **Top-20:** Top-10 + KOTAKBANK, HCLTECH, MARUTI, SUNPHARMA, NTPC, POWERGRID, ONGC, TATASTEEL,
  BAJAJ-AUTO, WIPRO.

---

## 2. Headline matrix — HONEST (gate ON), at ₹10 lakh

One row per target × strategy × horizon. `gPF`/`nPF` = gross/net profit factor; `defl` = deflated
Sharpe (n_trials=20); `folds` = OOS folds with net PF > 1; **B&H** = equal-weight buy-and-hold
over the same OOS window. *(daily clock = `decision_bar_minutes 375`; strategies at shipped
horizons: trend 6-bar, meanrev 3-bar.)*

| Target | Strat | Horizon | Coverage | n_trades | CAGR | Sharpe | defl | maxDD | hit | gPF | **nPF** | folds | net P&L | **B&H total / CAGR** | Verdict |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| NIFTY index | trend | daily | 2021→26 | 23 | 0.18% | 0.57 | 0.17 | 0.6% | 0.57 | 2.98 | **2.15** | 3/3 | +₹4,732 | **+22.4% / 7.7%** | no edge (trivial size) |
| NIFTY index | meanrev | daily | 2021→26 | **0** | — | — | — | — | — | — | — | — | +22.4% / 7.7% | no trades |
| NIFTY fut | trend | daily | 2024→26 | **0** | — | — | — | — | — | — | — | — | −5.3% / −4.3% | not testable (2.4y) |
| NIFTY fut | meanrev | daily | 2024→26 | **0** | — | — | — | — | — | — | — | — | −5.3% / −4.3% | not testable |
| BANKNIFTY fut | trend | daily | 2024→26 | **0** | — | — | — | — | — | — | — | — | +4.5% / 3.6% | not testable (2.4y, no spot) |
| BANKNIFTY fut | meanrev | daily | 2024→26 | **0** | — | — | — | — | — | — | — | — | +4.5% / 3.6% | not testable |
| **Top-10** | trend | daily | 2017→26 | 95 | 0.30% | **0.94** | **0.55** | 0.4% | 0.48 | 2.59 | **2.26** | 4/6 | +₹13,808 | **+52.7% / 9.4%** | **least-bad, still no robust edge** |
| Top-10 | meanrev | daily | 2017→26 | **0** | — | — | — | — | — | — | — | — | +52.7% / 9.4% | no trades |
| Top-20 | trend | daily | 2017→26 | 63 | ~0% | −0.02 | 0.03 | 0.4% | 0.41 | 1.22 | **0.98** | 4/5 | −₹171 | +63.3% / 10.9% | no edge |
| Top-20 | meanrev | daily | 2017→26 | **0** | — | — | — | — | — | — | — | — | +63.3% / 10.9% | no trades |

**Read this carefully:** NIFTY-index and Top-10 trend show *net PF > 2*, which looks great — but
that is an artifact of trading **tiny size, rarely**: total net P&L is ₹4.7k–₹13.8k on ₹10L over
5.5–9.5 years (CAGR 0.18–0.30%), while just **holding** returned **+22%–+53%**. High PF + near-zero
CAGR = "the few trades it takes are fine, but it barely deploys capital." Deflated Sharpe
(0.17–0.55) is far below the 0.95 robustness bar. **Not tradeable.**

---

## 3. 15-minute intraday (recent ~0.65 y window; capped for HMM cost)

Honest gate vs forced-gross diagnostic. (Futures excluded — no intraday futures data.)

| Target | Strat | Honest n_trades | Forced n_trades | Forced gPF | Forced nPF | Forced net | B&H |
|---|---|---:|---:|---:|---:|---:|---:|
| NIFTY index | trend | **0** | 39 | 0.85 | 0.42 | −₹8,389 | −5.9% |
| Top-10 | trend | **0** | 151 | **0.64** | 0.47 | −₹20,573 | −10.2% |
| Top-20 | trend | **0** | 200 | **0.70** | 0.49 | −₹21,594 | −8.6% |
| Top-10 / Top-20 | meanrev | **0** | 0 | — | — | — | — |

**Daily vs intraday (side-by-side):** lower frequency is unambiguously better. Daily trend at
least produces net-PF-positive trades (the cost gate lets a few through); **15-min trend is
gross-negative before costs even start** (PF 0.64–0.85) and the honest gate correctly takes **0
trades**. Higher frequency = more cost drag, no edge to pay for it. This matches the prior
report's full-window 15-min finding (gross-negative / Gate CLOSED).

---

## 4. Capital sensitivity (daily trend, ₹10L / ₹50L / ₹1cr)

| Target | ₹10L | ₹50L | ₹1cr |
|---|---|---|---|
| **Top-10** | 95 tr · nPF **2.26** · +₹13.8k · defl 0.55 | 360 tr · nPF 1.07 · +₹7.5k · defl 0.08 | 535 tr · nPF 1.41 · +₹63.6k · defl 0.26 |
| **Top-20** | 63 tr · nPF 0.98 · −₹0.2k · defl 0.03 | 351 tr · nPF 1.42 · +₹34.6k · defl 0.29 | 778 tr · nPF 1.33 · +₹57.7k · defl 0.27 |
| NIFTY/BANKNIFTY fut | **0 trades** | **0 trades** | **0 trades** |

**What capital changes:** more capital climbs the tier ladder (₹10L = **T2**, 3 instruments / 2
strategies → ₹1cr = **T4**, 20 instruments / 4 strategies), so the book deploys more names and
absolute net P&L grows (Top-10 +₹63.6k, Top-20 +₹57.7k at ₹1cr). **What capital does NOT change:**
CAGR stays ~0.1–0.2% and **deflated Sharpe stays 0.03–0.55 — never near 0.95.** Buy-and-hold
(+53% / +63%) wins at every capital. More capital ≠ edge.

**Futures take 0 honest trades at every capital** because the trend warmup (ema_slow 80 × 6-bar
≈ 480 daily bars) exceeds what the 603-bar (2.4 y) futures series leaves for OOS, and at ₹10L one
lot (~₹15.6L) is unaffordable anyway. Forcing **true-daily (tf=1)** at ₹1cr still produced only 4
degenerate, all-losing NIFTY-fut trades (net −₹36.7k, deflated 0.0003) and 0 BANKNIFTY-fut trades
— **not a testable edge.** The broader 24-instrument futures universe was net-negative (PF 0.854)
in the prior report; cheaper futures costs help but cannot rescue a gross-negative signal.

---

## 5. Cost-stress (best honest cases, slippage ×1 / ×2 / ×3)

| Case | ×1 | ×2 | ×3 |
|---|---|---|---|
| Top-10 trend @₹10L | nPF 2.26 · +₹13.8k · defl 0.55 | nPF 2.29 · +₹13.7k · defl 0.54 | nPF 2.58 · +₹13.6k · defl 0.61 |
| Top-20 trend @₹1cr | nPF 1.33 · +₹57.7k · defl 0.27 | nPF 1.28 · +₹50.5k · defl 0.23 | nPF 1.26 · +₹46.7k · defl 0.20 |

The **cost gate is self-protecting**: under punitive slippage the small book (Top-10 @₹10L) simply
trades *less* (95 → 71 trades) and per-trade net PF holds/rises; the larger book (Top-20 @₹1cr)
degrades gracefully but stays net-PF > 1. So the (weak) signal is *robust to cost-stress* — but it
was never tradeable to begin with (deflated < 0.95, CAGR ≈ 0). Cost-stress does not change the
verdict.

---

## 6. Best-instrument / least-bad scan (OOS)

- **Any single target with gross PF > 1 OOS?** Yes — **NIFTY-index trend** (gPF 2.98, nPF 2.15)
  and **Top-10 trend** (gPF 2.59, nPF 2.26) on the daily clock. Ranked least-bad → worst:
  **Top-10 daily trend** (Sharpe 0.94, defl 0.55, P(SR<0)=0.03) > NIFTY-index daily trend
  (Sharpe 0.57, defl 0.17) > Top-20 @scale > everything else (≤ break-even or 0 trades).
- **Where the strategies behave least-badly:** **daily trend on a tight, high-weight basket
  (Top-10)** — concentrated mega-cap momentum, low turnover. Even there, deflated Sharpe (0.55)
  says the result is **indistinguishable from selection luck** at the go-live bar.
- **meanrev: no edge anywhere** — 0 trades at every target/horizon/capital. Single instruments
  can't form pairs; on the baskets the 500-bar cointegration lookback × 3-bar horizon needs a
  ~1,500-day warmup that consumes most of the history, leaving nothing to trade OOS honestly.

---

## 7. Per-target verdict

| Target | Confidence | Verdict |
|---|---|---|
| **NIFTY (index, daily)** | high (~5.5 y) | **NO EDGE.** Net PF 2.15 is a small-size mirage; CAGR 0.18% vs B&H 7.7%; deflated 0.17. |
| **NIFTY (index, 15-min)** | medium (~0.65 y) | **NO EDGE.** Honest 0 trades; forced gross-negative (0.85). |
| **NIFTY (futures, daily)** | **low (2.4 y)** | **NOT TESTABLE → NO EDGE.** 0 honest trades; true-daily forced = degenerate/negative. |
| **BANKNIFTY (futures, daily)** | **lowest (2.4 y, no spot)** | **NOT TESTABLE → NO EDGE.** 0 honest & 0 forced trades. |
| **Top-10 equities (daily trend)** | high (~9.5 y) | **NO ROBUST EDGE (least-bad).** Sharpe 0.94 but deflated 0.55 < 0.95; CAGR 0.30% ≪ B&H 9.4%. |
| **Top-20 equities (daily trend)** | high (~9.5 y) | **NO EDGE @₹10L** (nPF 0.98); only marginal-positive at ₹50L–₹1cr (nPF ~1.3), deflated ~0.27, CAGR ≪ B&H. |
| **Any target, meanrev** | high | **NO EDGE.** 0 trades everywhere. |
| **Any target, 15-min** | medium | **NO EDGE.** Honest 0 trades; forced gross-negative. |

---

## 8. Overall conclusion

Across **NIFTY and BANKNIFTY (index and futures)** and the **Top-10 / Top-20 NIFTY-50 baskets**,
on **daily and 15-min** horizons, at **₹10L / ₹50L / ₹1cr**, with the honest cost gate ON and real
Indian costs: **there is no robust, after-cost, out-of-sample edge.** The trend strategy on
high-weight equity baskets is the only thing that is even mildly net-positive (net PF > 1 on
Top-10), but its **deflated Sharpe (≤ 0.55) is far below the 0.95 go-live bar**, its **CAGR
(~0.1–0.3%) is negligible** (the honest Kelly/cost gate barely deploys capital because the edge is
unproven), and **buy-and-hold beats it by 50–60 points** over the window. `meanrev` and `15-min`
add nothing. Futures cannot be judged on 2.4 years and are not tradeable at retail capital.

**The GO-LIVE gate is correctly CLOSED** (no non-synthetic run clears OOS Sharpe ≥ 0.8 / deflated
≥ 0.95 / P(SR<0) ≤ 0.10). **Recommendation: stay in PAPER.** The only previously-identified lead
worth more research remains the **vol-selling sleeve** (`docs/BACKTEST_PL_REPORT.md`, marginal,
needs intraday option pricing + the Feb-2018 window) — out of scope here.

---

## 9. Reproducibility

- **Driver:** `deploy/_audit/matrix.py` on the VPS (runs in a one-off `engine-paper` container with
  the `quant_histdata` volume mounted). Suites: `daily`, `intraday`, `capital`, `coststress`,
  `determinism`.
- **Persisted:** `backtest_runs` ids **3–32** (label `MATRIX …`), visible in the dashboard
  **Backtest viewer**. Forced diagnostics carry `metrics.forced_diagnostic=true`.
- **Determinism:** two full daily-suite passes produced an identical SHA (`e76516e8…`).
- **Tests:** engine suite **129 passed ×3**; dashboard suite **44 passed / 1 skipped** (the
  earlier `test_alerts` failure was env leakage from the deployment `.env`, not a code regression).
- **Discipline:** PAPER only; `QS_LIVE_ARMED=0`; no config/capital/risk changed; nothing armed.
