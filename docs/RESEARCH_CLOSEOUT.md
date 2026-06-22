# quantsys — Research Closeout (Final Decision Record)

*2026-06-22. The alpha search is OVER. This records the final verdict and banks the
reusable infrastructure. The live engine stayed PAPER throughout, `QS_LIVE_ARMED=0`;
nothing is enabled live or in paper as a result of this work.*

## Verdict

**No durable price/volume edge clears the deployment gate. The honest maximum is
"hold the index." There is nothing here to deploy with real money or to sell.**

The gate (repo `livegate.py` + PBO + net-positivity): OOS Sharpe ≥ 0.80, **deflated
Sharpe ≥ 0.95**, P(SR<0) ≤ 0.10, **PBO ≤ 0.50**, net CAGR > 0. Nothing cleared it.

## What was tested (every honest avenue)

| Strategy / pillar | Result | Where |
|---|---|---|
| **Trend** (TS-momentum + breakout) | weak, overfit, regime-dependent; best hold-out deflated ≤ 0.11; loses to buy-&-hold | `MAX_POTENTIAL_AUDIT.md` |
| **Mean-reversion = cointegration pairs** | no edge; ~0 tradeable pairs clear ADF + cost gate over 7y | `MEANREV_REALITY_CHECK.md` |
| **Event-driven** (monthly-expiry MR; vol-carry sleeve) | expiry borderline-luck (deflated 0.33); vol sleeve a mid-price illusion (dead under real spreads) | `VOL_SLEEVE_RESEARCH.md` |
| **Equity factor / market-neutral (Pillar 2)** | breadth revived momentum vs the failed 25-name probe; market-neutral momentum is an OOS-survivor but **sub-gate** (OOS deflated 0.074) | `PILLAR2_FACTOR_RESEARCH.md` |
| **Flow: BANKNIFTY PCR-OI** | OOS-survivor, marginal, decaying; sub-gate alone | `ALPHA_SEARCH.md` |
| **Combine-survivors (S1 × S2)** | **closest ever, still FAIL** (below) | `COMBINE_SURVIVORS.md` |

## The final test — combine the two uncorrelated survivors

Pre-registered, single test. S1 (BANKNIFTY-PCR) and S2 (broad-universe market-neutral
momentum) are uncorrelated (IS corr **−0.10**), so a risk-parity blend was tested
against the gate. Implementability honored (S2 shorts restricted to point-in-time
single-stock-futures names with futures + financing costs; full Indian cost stack;
no look-ahead; survivorship-free). Hold-out 2024-01 → 2026-06:

| Sleeve | IS Sharpe | OOS Sharpe | OOS deflated | OOS P(SR<0) | net CAGR | maxDD | beta |
|---|---:|---:|---:|---:|---:|---:|---:|
| S1 BANKNIFTY-PCR | 0.45 | 0.61 | 0.535 | 0.19 | 5.5% | 10.5% | −0.13 |
| S2 factor momentum | 1.01 | 0.69 | 0.583 | 0.13 | 13.5% | 28.5% | 0.46 |
| **COMBO** (inverse-vol) | 0.83 | **0.87** | **0.690** | **0.08** | 7.8% | **8.1%** | **0.01** |

PBO across {S1, S2, COMBO} = **0.84**.

**Gate: 3/5 PASS → FAIL.** OOS Sharpe 0.87 ✅, P(SR<0) 0.08 ✅, net CAGR 7.8% ✅; but
**deflated 0.69 < 0.95 ❌** and **PBO 0.84 > 0.50 ❌**. The diversification lift is
*real* (combo beats both sleeves; clean beta ≈ 0, drawdown 8.1%, the highest deflated
Sharpe the project ever produced) — but it does **not** clear the robustness bar.

## Conclusion

The platform stayed honest end to end: it refused every non-edge. Combining
uncorrelated survivors got the closest of anything, validating the *direction* of the
free-lunch idea, but still failed deflation and overfit-probability. **Stay paper.
The honest deployment for capital is a NIFTH index SIP / buy-and-hold (~12%/yr
long-run) — the owner's 12–15% target, achieved without an algo and without model
risk.** No further tuning is legitimate on this 2017–2026 sample (PBO 0.84 means that
path manufactures a false edge). The only valid continuation is a **separate, newly
pre-registered, FORWARD-only** out-of-sample effort on unseen data.

## Reusable assets kept (banked, inert)

These are genuinely valuable and survive the closeout — engine-isolated, documented,
importable without the live engine (verified):

- **Free, survivorship-bias-free NSE data tooling**
  - `quantsys/research/bhavcopy.py` — full NSE **cash** market daily panel (both
    archive formats; corporate-action handling via the ±20% price-band rule).
  - `quantsys/research/fno.py` — NSE **F&O** daily: index-option OI → PCR, near-month
    futures, and **point-in-time single-stock-futures membership**.
- **Validation harness** (Lopez de Prado / Bailey)
  - `quantsys/backtest/metrics.py` — deflated Sharpe + Monte-Carlo block bootstrap.
  - `quantsys/research/validation.py` — **PBO (CSCV)** + **purged & embargoed K-fold CV**.
- **Cross-sectional research kit** — `quantsys/research/factors.py` (price/volume
  factors) + `xs_backtest.py` (beta-neutral monthly backtester reusing the real
  `CostModel`).

See README → "Research tooling (standalone)" for usage. These let any future
**forward-OOS** study run honestly without re-deriving data or metrics.

## Status of the engine

Unchanged and inert. `factor.enabled=False`; trend/meanrev remain the only enabled
strategies (proven no-edge, sized to ~0 by Kelly); the combine sleeves are research
modules never wired to the engine. Live gate stays CLOSED; `QS_LIVE_ARMED=0`.

**CLOSED. Stay paper. No live deployment is justified by the evidence.**
