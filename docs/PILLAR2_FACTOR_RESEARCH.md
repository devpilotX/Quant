# Pillar 2 — Broad-Universe Equity Factors (Research Report)

*Generated 2026-06-22. Research only: the live engine stayed **PAPER**, untouched
(`QS_LIVE_ARMED=0`, mode=paper). No safety/gate code weakened, no forced trades, no
synthetic data, no look-ahead. All new code lives in `quantsys.research.*` (isolated
from the engine path) on branch `feature/pillar2-equity-factor`. The engine-side
`factor` strategy is shipped **DISABLED**.*

This closes the one genuine structural gap from the Part-A/B audit: **Pillar 2
(equity factor / market-neutral) had no module**, and the only prior touch — a
cross-sectional momentum *probe* on the 25-name mega-cap universe (`ALPHA_SEARCH.md`
Probe 1) — found no edge but flagged a real, unresolved caveat: *25 names is too
narrow for cross-sectional factors; a proper test needs breadth.* This report
supplies that breadth.

---

## 1. Data — survivorship-bias-free, point-in-time

Source: **NSE daily cash-market bhavcopy**, fetched directly from the public
archive CDN (`nsearchives.nseindia.com`) — free and official. Adapter:
`quantsys/research/bhavcopy.py` (handles both the legacy `cm<DDMONYYYY>bhav` and the
2024-07+ UDiFF formats; on-disk cached under `data_cache/`).

- **Coverage:** 2016-01-01 → 2026-06-19, **4.39 M rows, 3,454 distinct EQ symbols**,
  ~2,600 trading days.
- **Point-in-time / survivorship-free by construction:** a symbol appears on a day
  iff it actually traded; delisted names are present in their era and simply stop
  appearing. The tradeable universe each month is rebuilt from data available *then*
  (top-N by trailing ₹-turnover), so no forward-looking constituent list is used.
- **Corporate-action handling (verified):** bhavcopy prices are *unadjusted*, so a
  split/bonus injects a spurious one-day jump (e.g. RELIANCE's 1:1 bonus on
  2024-10-28 shows close 2655→1334). NSE enforces ~±20% intraday price bands, so any
  *overnight gap* beyond the band must be a corporate action, not a return. The
  return builder therefore uses the **intraday** `close/open−1` on CA days and
  `close/prevclose−1` otherwise, winsorized at the band. Verified on the RELIANCE
  bonus: the −49.7% gap is removed; the true ~−0.2% intraday return is kept
  (`tests/test_research.py::test_ca_adjusted_return_uses_intraday_on_bonus_day`).

**Honest data limits:** free bhavcopy gives price/volume only — **no point-in-time
fundamentals**, so *value* (P/B, P/E), *quality* (ROE, accruals) and true *size*
(market cap) factors are **not implementable without look-ahead/survivorship risk**
and are left as documented stubs (`factors.UNAVAILABLE_FACTORS`), **not fabricated**.
The honestly-testable factors are price/volume based: **momentum, low-volatility,
short-term reversal, Amihud illiquidity**.

---

## 2. Method

- **Universe (PIT):** top-N by trailing 60-day median ₹-turnover, price ≥ ₹20, ≥ 252
  days of history (`factors.eligible_universe`).
- **Factors** (`factors.py`), z-scored cross-sectionally each month; higher = long:
  - `momentum` 12-1 (skip the last month), `lowvol` = −trailing daily vol,
    `reversal` = −last-month return, `illiquidity` = Amihud |ret|/turnover.
- **Portfolios** (`xs_backtest.py`), monthly rebalance:
  - `long_only` — equal-weight top-K (carries market beta),
  - `long_short` — equal-weight top-K long / bottom-K short, dollar-neutral
    (the pure-alpha / market-neutral test).
- **Costs:** the engine's own `quantsys.costs.CostModel` (delivery-equity round-trip),
  charged on rebalance turnover — never a softer parallel cost model.
- **Benchmark:** equal-weight buy-and-hold of the same PIT universe (a factor must
  beat *holding the basket*, not just be positive).

---

## 3. Anti-overfit protocol (pre-registered, Part D)

Fixed before reading any hold-out number, mirroring the prior `MAX_POTENTIAL_AUDIT`
discipline:

- **Grid:** factor ∈ {momentum, lowvol, reversal, illiquidity, momentum+lowvol} ×
  side ∈ {long_only, long_short} × top_k ∈ {20,30,50} × universe ∈ {100,200}.
- **In-sample** = ≤ 2023-12-31 (selection only); **hold-out** = 2024-01-01 → 2026-06
  (evaluated **once** for the IS-selected winner).
- **Selection rule:** best IS annualised Sharpe among configs with ≥ 24 rebalances.
- **Deflated Sharpe** (Bailey/LdP) trial count = number of configs tried.
- **PBO** (Probability of Backtest Overfitting, CSCV) across the whole grid.
- **Purged & embargoed K-fold** Sharpe stability on the winner.
- **Gate to clear (same as live):** OOS Sharpe ≥ 0.8 **and** deflated ≥ 0.95 **and**
  P(SR<0) ≤ 0.10 — **and** beat buy-and-hold.

New Part-D machinery added (the two pieces the engine harness lacked):
`quantsys/research/validation.py` — `pbo_cscv` and `purged_kfold` /
`cv_sharpe_stability`, unit-tested on synthetic data.

---

## 4. Results

**Pre-registered winner (best IS Sharpe, chosen before any hold-out was read):**
`lowvol | long_only | K20 | N200`.

| Window | Sharpe | CAGR | maxDD | beta |
|---|---:|---:|---:|---:|
| In-sample (≤2023) | **1.28** | 17.1% | — | — |
| **Hold-out (2024→2026)** | **0.33** | 3.0% | 20.1% | 0.38 |
| Buy-&-hold same universe (hold-out) | **0.77** | 13.7% | — | 1.0 |

- **OOS deflated Sharpe (n_trials=60): 0.034** — gate needs ≥ 0.95.
- **OOS P(SR<0): 0.341** — gate needs ≤ 0.10.
- Purged 5-fold Sharpe: mean 0.07, min −0.00, 80% folds positive (weak, not robust).
- **PBO across the 60-config grid: 0.11** (low — see note below).

The pre-registered winner shows textbook overfit decay (IS 1.28 → OOS 0.33) and is
**beaten by buy-and-hold** (0.33 vs 0.77 Sharpe; 3.0% vs 13.7% CAGR). **It fails the
gate by a wide margin.**

**Leaderboard — IS → OOS decay (top configs by IS Sharpe), with realized beta:**

| Config | IS Sh | OOS Sh | OOS CAGR | beta | note |
|---|---:|---:|---:|---:|---|
| lowvol · LO · K20 · N200 | 1.28 | 0.33 | 3.0% | 0.38 | **winner — fails, < B&H** |
| momentum+lowvol · LO · K50 · N200 | 1.27 | 0.70 | 11.2% | 0.63 | beta-heavy |
| momentum · L/S · K20 · N200 | 1.20 | **0.57** | 10.5% | **0.07** | **market-neutral standout** |
| momentum · L/S · K30 · N200 | 1.15 | 0.43 | 6.7% | 0.06 | market-neutral |
| momentum · LO · K20 · N200 | 1.05 | 0.80 | 21.6% | **1.11** | just leveraged beta |
| illiquidity · L/S · K20 · N200 | 0.32 | 0.73 | 13.3% | 0.58 | impact-fragile |
| reversal · L/S (all) | ≤0.2 | **< 0** | <0 | — | no edge |
| lowvol · L/S (all) | ≤0.4 | **< 0** | <0 | — | no edge |

**Full OOS stats for the decision-relevant configs:**

| Config | OOS Sharpe | CAGR | maxDD | beta | deflated | P(SR<0) | turnover/yr |
|---|---:|---:|---:|---:|---:|---:|---:|
| momentum · L/S · K20 · N200 (market-neutral) | 0.57 | 10.5% | 29.5% | 0.07 | **0.074** | 0.16 | 15.6× |
| momentum · LO · K20 · N200 (beta) | 0.80 | 21.6% | 39.0% | 1.11 | 0.141 | 0.10 | 8.1× |
| illiquidity · L/S · K20 · N200 | 0.73 | 13.3% | 29.3% | 0.58 | 0.115 | 0.16 | 13.5× |

**What this says, honestly:**
1. **No config clears the gate.** Best deflated Sharpe anywhere = **0.141** (and that
   config is just market beta); best *market-neutral* deflated = **0.074**. Gate needs
   0.95. Not close.
2. **Nothing beats buy-and-hold in a capturable, risk-adjusted way.** The only
   high-CAGR config (momentum long-only, 21.6%) has **beta 1.11** — it is leveraged
   market exposure, not alpha (its Sharpe 0.80 ≈ B&H 0.77, at a worse 39% drawdown).
3. **The genuinely new finding:** broad-universe **market-neutral momentum** (beta
   0.07) is **positive out-of-sample** (Sharpe 0.57, CAGR 10.5%). That is a real
   *qualitative* improvement over the 25-name probe, which **inverted** OOS (Sharpe
   −1.65, −28% CAGR). **Breadth revived momentum.** The low **PBO (0.11)** corroborates
   that the IS config ranking carries genuine OOS persistence — the selection is *not*
   pure noise (unlike the prior probes). But it remains **marginal and sub-gate**.

---

## 5. Implementability caveats (India-specific, material)

These constrain how much of any measured L/S edge is *capturable*:

1. **Shorting cash equity is not feasible** beyond intraday in India — no naked short
   delivery. A monthly-held short book is only possible via **single-stock futures**
   (~190 F&O names) or thin/expensive SLB. So the dollar-neutral `long_short` result
   is only *partially* implementable (the shorts must sit in the F&O subset), and the
   delivery-equity cost model **understates** the true futures roll/financing cost of
   the short leg. The `long_only` book is fully implementable but **carries market
   beta** (it is beta + tilt, not pure alpha).
2. **Mid-cap impact** beyond the top ~100 names is larger than a flat bps cost model
   captures; turnover-heavy factors (reversal, illiquidity) are most exposed.
3. **Residual CA noise** after the band rule is bounded and diversified across K
   names, but not zero.

---

## 6. Engine integration & rollback

- `strategies/factor.py` (`@register("factor")`) + `schema.py::FactorConfig`
  (`enabled=False`) implement Pillar 2 in the standard `Strategy` interface so it
  exposes the same metrics (Kelly f, Edge/bar, VaR, Sharpe, N_eff) as the others.
- **Safe by design:** it needs ≥ `min_universe` (40) names, so it is a pure no-op on
  the live tier-capped intraday book; and it is disabled by default.
- **Rollback:** the whole pillar is the feature branch — `git checkout main` removes
  it entirely; or leave merged and it stays inert (`factor.enabled=False`). No live
  behaviour changes either way (verified: full engine suite 129 passed).

---

## 7. Verdict

**No gate-clearing, buy-and-hold-beating edge — but the first equity-factor signal in
the project that survives the hold-out POSITIVE and market-neutral.**

- The **pre-registered winner fails** (OOS deflated 0.034, loses to buy-and-hold). On
  the strict, honest bar the project uses everywhere, **Pillar 2 does not clear the
  gate** and is **not deployable**.
- **But breadth changed the qualitative result.** On 25 mega-caps the factor probe had
  *no* edge and inverted OOS; on the full ~3,450-name survivorship-free universe,
  **market-neutral momentum is positive OOS (Sharpe ≈ 0.5, beta ≈ 0.07)** with low PBO
  (0.11). This is the **second OOS-survivor in the whole project** (after the
  BANKNIFTY-PCR option-flow lead) — a real, economically-grounded, breadth-dependent
  momentum premium — that nonetheless remains **marginal**: deflated 0.07 ≪ 0.95,
  Sharpe < 0.8, ~30% drawdown, ~16× annual turnover, and the short leg needs single-
  stock futures (the ~190 F&O names), whose true cost the delivery-equity model
  understates.
- **Bottom line:** consistent with the rest of the program — *the honest maximum is
  still "hold the index."* Pillar 2 is now **built, validated, and shipped DISABLED**,
  with the market-neutral-momentum result logged as the most promising sub-gate lead
  to pursue (alongside BANKNIFTY-PCR) **if** future work resolves the cost/short-
  access realism on the F&O subset and it ever clears the gate over years of OOS.

**Status: research complete; stay paper; factor pillar inert. No live deployment is
justified by the evidence.**
