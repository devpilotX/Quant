# quantsys — Deploy Decision (Close-out)

*Generated 2026-06-19. Consolidated go/no-go after five strategy missions + a 121-config
anti-overfit search + cross-sectional momentum + option-flow probes. Research only; engine stayed
**PAPER**, untouched; `QS_LIVE_ARMED=0`; no real-money order; no safety/gate code weakened.*

## DEPLOY LIVE REAL MONEY? — **NO.**

Three independent, sufficient reasons:

1. **No edge.** Over the last 2 years (2024-01→2026, real costs), the best deployable algo config
   (trend TOP-10 tuned) returned **+0.27%/yr** vs **buy-and-hold +4.3%/yr** — every equity config
   loses to simply holding; meanrev takes 0 trades. The most profitable "through" is **buy-and-hold.**
2. **Gate-blocked, correctly.** **0** non-synthetic backtests pass the live-gate (deflated Sharpe
   ≥ 0.95 + OOS Sharpe ≥ 0.8). Best deflated Sharpe ever = **0.55** (collapsed to 0.018 on its
   hold-out). The live lead (BANKNIFTY-PCR) tops out at hold-out deflated **≈ 0.27**.
3. **Credentials compromised.** Angel One creds were exposed on GitHub (2026-06-11) and are **not
   rotated** — GOLIVE treats this as blocking. (Plus no live execution adapter is attached.)

## Measured ceiling ("maximum potential")
- Best OOS **deflated Sharpe ever ≈ 0.27** (historically 0.11–0.55 across missions) vs **0.95**
  required by the gate.
- Best config **CAGR ~0.1–0.3%/yr** vs **buy-and-hold ~4–7%/yr** (window) / **~12%/yr** (NIFTH
  long-run).
- One marginal, **decaying** lead: **BANKNIFTY-PCR** (option-flow) — survives real costs and is
  param-robust, but fails the gate and went **negative in 2026**.

## Safety chain — verified working (all locks correctly BLOCK live)
| Lock | Status |
|---|---|
| (a) passing non-synthetic backtest (deflated ≥ 0.95) | **PASS — 0 exist; live refused** |
| (b) live execution adapter / engine-live attached | **PASS — none attached** |
| (c) `QS_LIVE_ARMED=1` + rotated creds | **PASS — armed=0; creds unrotated** |
| dashboard chain (re-auth + typed phrase + cap + flat book) | intact |

## Paper-capital control — verified working (reversible)
`paper_capital` changed ₹10L → ₹50L → restored ₹10L via the sanctioned control path
(`scripts/set_paper_capital.py` / `POST /api/control/paper-capital`); engine consumes it
(`runner.reset_paper_capital`), with a safety guard that **rejects changes while positions are
open** (`commands.py:117`). Live state restored to ₹10L.

## Tests — green
Engine **129 passed**, dashboard **44 passed / 1 skipped** (the lone `test_alerts` failure is
`.env` telegram leakage into the test container, not a regression). Backtest harness deterministic
(identical SHA over repeated runs).

## The only honest deployment
- **For capital → a NIFTY index SIP / buy-and-hold** (~12%/yr long-run, zero model risk). This
  *is* the owner's 12–15% target, achieved without an algo.
- **For the dream → continue flow-alpha research** (resolve the BANKNIFTY-PCR decay with real
  futures costs + forward data; broaden flow/positioning signals) until something **clears the gate
  over years of OOS** — *then* paper-rehearse 3–4 months as the operational dress check — *then*,
  with **rotated creds + SEBI algo registration**, go live. Paper-validation is the final ops gate,
  **never** the edge-discovery or selection step.

**Status: CLOSED — stay paper. No live deployment is justified by the evidence.**
