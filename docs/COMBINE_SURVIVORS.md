# Combine Survivors — S1 (BANKNIFTY-PCR) × S2 (factor momentum)

*Single pre-registered test of one hypothesis: two individually-sub-gate but
UNCORRELATED OOS-survivors may clear the deployment gate when combined. This run
tests it; it does not try to make it true. Branch `feature/combine-survivors`
(off main + merged pillar2/S2 deps). Research only; live untouched, nothing armed.*

---

## 1. PRE-REGISTRATION (locked 2026-06-22, BEFORE any combined backtest)

Everything in this section was fixed before running. It is not changed after
seeing results.

### 1.1 The gate (must clear ALL, out-of-sample, net of costs)
Verbatim from the repo's live gate (`dashboard/backend/qsdash/bridge/livegate.py`:
`MIN_OOS_SHARPE=0.8`, `MIN_DEFLATED=0.95`, `MAX_P_SHARPE_NEG=0.10`) plus the PBO
bar and net-positivity:

1. **OOS annualised Sharpe ≥ 0.80**
2. **Deflated Sharpe ≥ 0.95** (Bailey/López de Prado; `n_trials = 3` — the three
   return streams evaluated here: S1, S2, COMBO. Stated honestly; a confirmatory
   single test, not a search.)
3. **Monte-Carlo P(SR < 0) ≤ 0.10** (stationary block bootstrap)
4. **PBO ≤ 0.50** (CSCV across {S1, S2, COMBO})
5. **Net-of-cost CAGR > 0** on the hold-out

A PASS requires **all five**. Anything short is a FAIL (binding stop-rule §1.6).

### 1.2 Train / hold-out split (same hold-out as the factor work)
- **In-sample (IS):** 2017-01-01 → 2023-12-31 (premise checks, vol estimation warm-up).
- **Hold-out (OOS):** 2024-01-01 → 2026-06-19. Evaluated once.

### 1.3 Signal definitions (frozen)
- **S1 — BANKNIFTY PCR-OI contrarian.** Daily PCR = Σ(put OI) / Σ(call OI) over all
  listed BANKNIFTY index options (free NSE F&O bhavcopy). Signal = z-score of PCR
  over a trailing **L = 60** trading days. Position in the **near-month BANKNIFTY
  future**: **+1 (long) when z ≥ +1.0**, **−1 (short) when z ≤ −1.0**, else **0**
  (contrarian: fade extreme option positioning). Decided at close *d*, return
  earned *d→d+1*. Frozen params: **L=60, threshold=1.0** (the prior "live lead").
- **S2 — broad-universe factor momentum, market-neutral.** The prior standout
  config: `momentum (12-1) | long_short | top_k=20 | universe top-200 by turnover`,
  monthly rebalance, z-scored, on the survivorship-free CM bhavcopy panel.

### 1.4 Combine rule (frozen; ONE hyperparameter)
- Daily return streams r_S1(t), r_S2(t).
- **Inverse-volatility (risk-parity)** weights, recomputed at each **monthly**
  rebalance (the existing schedule): w_i ∝ 1/σ_i, σ_i = trailing **63-trading-day**
  stdev of sleeve i's daily returns, normalised to w_S1+w_S2=1, applied forward to
  the next month. **Single hyperparameter: vol_lookback = 63 days. No grid search.**
- Combined daily return = w_S1·r_S1 + w_S2·r_S2. (Sharpe is scale-invariant.)

### 1.5 Implementability (no fantasy book)
- **S2 short leg restricted to single-stock-futures names** — point-in-time SSF
  membership from the F&O bhavcopy (STF / FUTSTK underlyings present that month).
  Bottom-ranked names that are NOT SSF-eligible are dropped from the short book;
  each side scaled to equal gross (dollar-neutral).
- **Costs (full Indian stack via `quantsys.costs.CostModel`):**
  - S2 longs: delivery-equity round-trip.
  - S2 shorts: **FUTURE** cost per side **+ a conservative 1.5%/yr financing/borrow
    drag** on short gross (charged daily) to stand in for SSF roll + carry.
  - S1: **FUTURE** cost per side on position changes (incl. monthly roll).
- No look-ahead (signal at close, traded next bar / held forward). Survivorship-free
  (a name exists on a day iff it traded). Point-in-time universe only.

### 1.6 Stop-rule (binding)
- **COMBO clears all five gate criteria OOS** → report as a candidate, keep it
  **INERT (enabled=False)**, propose paper-trading next.
- **Otherwise** → declare the combination **dead**, report the honest numbers, and
  **STOP**. No re-weighting, no re-search, no other combiner, no gate-loosening.
  One pre-registered test, one verdict.

---

## 2. Correlation premise (IS)

**CONFIRMED — the two sleeves are uncorrelated.** Realized daily-return correlation
S1↔S2 = **−0.097 in-sample** (2017–2023; −0.087 full sample). The low-correlation
premise that motivates the test holds, so we proceed to the single combined test
(we do not stop). Individually (IS): S1 Sharpe +0.45 (ann. +5.7%), S2 Sharpe +1.01
(ann. +21.7%) — both positive, both sub-gate alone.

## 3. Metrics — S1 / S2 / COMBO

Hold-out 2024-01-01 → 2026-06-19, net of the implementability costs in §1.5.

| Sleeve | IS Sharpe | OOS Sharpe | OOS deflated | OOS P(SR<0) | OOS net CAGR | OOS maxDD | OOS beta |
|---|---:|---:|---:|---:|---:|---:|---:|
| **S1** — BANKNIFTY-PCR | 0.45 | 0.61 | 0.535 | 0.19 | 5.5% | 10.5% | −0.13 |
| **S2** — factor momentum (SSF shorts) | 1.01 | 0.69 | 0.583 | 0.13 | 13.5% | 28.5% | 0.46 |
| **COMBO** — inverse-vol risk-parity | 0.83 | **0.87** | **0.690** | **0.08** | 7.8% | **8.1%** | **0.01** |

PBO across {S1, S2, COMBO} = **0.841**.

**The diversification effect is real on the headline dimensions.** The combo's OOS
Sharpe (0.87) exceeds *both* sleeves standalone (0.61, 0.69) — the two uncorrelated
streams genuinely add risk-adjusted value — and the inverse-vol blend collapses max
drawdown to **8.1%** (from S2's 28.5%) at a clean **beta ≈ 0.01**. The deflated
Sharpe (0.69) is the highest the project has ever produced (prior best ≈ 0.55, and
≤0.27 on true hold-outs). This is the strongest result to date.

## 4. Verdict (vs pre-registered gate) — **FAIL**

| Gate criterion | Combo OOS | Result |
|---|---:|---|
| OOS Sharpe ≥ 0.80 | 0.87 | ✅ PASS |
| Deflated Sharpe ≥ 0.95 | 0.690 | ❌ **FAIL** |
| P(SR<0) ≤ 0.10 | 0.083 | ✅ PASS |
| PBO ≤ 0.50 | 0.841 | ❌ **FAIL** |
| Net CAGR > 0 | 7.8% | ✅ PASS |

**3 of 5 pass; the combination does NOT clear the gate.** It fails on the two
robustness criteria:
- **Deflated Sharpe 0.69 < 0.95** — even at the lenient n_trials=3, the edge is not
  separable from selection luck at the required confidence (the broader research
  program's true trial count would push it lower still).
- **PBO 0.84 > 0.50** — across combinatorial IS/OOS splits, the in-sample-best of
  these signals usually lands in the worse out-of-sample half: the relative ranking
  is unstable, i.e. high backtest-overfit probability. (With only 3 streams PBO is a
  coarse measure, but it is pre-registered and it fails; it is not reinterpreted to
  rescue a pass.)

**Per the binding stop-rule (§1.6): the combination is declared DEAD. No
re-weighting, no re-search, no other combiner, no gate loosening. One test, one
verdict.**

**Honest reading:** combining the two uncorrelated survivors got *closer than
anything before* — a genuinely market-neutral (beta 0.01), low-drawdown (8.1%) book
whose OOS Sharpe (0.87) clears the Sharpe bar — and it validates the *direction* of
the free-lunch hypothesis (the combo beats each sleeve). But it still **fails the
deployment gate** on deflation and overfit-probability, so it is **not deployable**.
The honest maximum remains "hold the index." If pursued further (a *separate*,
newly-pre-registered effort — not this test), the only legitimate next step is
**forward out-of-sample** accumulation (more years / live paper as a dress
rehearsal), never re-tuning on this sample.
