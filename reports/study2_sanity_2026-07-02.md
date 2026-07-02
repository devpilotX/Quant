# Study 2 sanity checks — 2026-07-02

Panel: 2335 sessions x 48 config equities (2017-01-03..2026-06-19), net 28 bps RT delivery.

## Short-term reversal (frozen: lookback 5d, k=8, weekly)

| lookback | k | net Sharpe | net CAGR | frozen |
|---|---|---|---|---|
| 3 | 6 | -0.94 | -10.9% |  |
| 3 | 8 | -0.76 | -7.9% |  |
| 3 | 10 | -0.70 | -6.5% |  |
| 5 | 6 | -0.94 | -11.0% |  |
| 5 | 8 | -0.78 | -8.7% |  <-- deployed |
| 5 | 10 | -0.82 | -7.9% |  |
| 10 | 6 | -0.62 | -7.4% |  |
| 10 | 8 | -0.60 | -6.2% |  |
| 10 | 10 | -0.58 | -5.5% |  |

Frozen cell: Sharpe -0.78, deflated (n_trials=9) 0.000, grid PBO 0.84.

## Turn-of-month (frozen: -2/+3 weekdays, long index)

in-window mean +12.2 bps/d (n=526) vs outside +4.1 bps/d (n=1808), t=1.53; long-in-window-only Sharpe 0.86 (costs ~2 bps RT ignored).

## Down-shock (frozen z3.5/hold10)

Not re-run (stop-rule): full-gate record stands in docs/PILLAR4_EVENT_DRIVEN.md — hold-out Sharpe 1.75, deflated 0.46 (FAILED gate), PBO 0.31, MC P(SR<0) 0.002. Forward evidence only.