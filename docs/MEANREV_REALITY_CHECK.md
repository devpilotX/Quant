# quantsys — Meanrev Reality Check

*Generated 2026-06-19. Research / read-only — live engine untouched (PAPER, `QS_LIVE_ARMED=0`,
all runs in throwaway containers). The sensitivity ladder below is a **clearly-labelled
DIAGNOSTIC** (gate relaxed / forced allocation), never a tradeable result. Diagnostic runs
persisted to the dashboard Backtest viewer (`backtest_runs` ids 47–49).*

> **Verdict: (i) HONEST NO-EDGE — but the earlier *daily* "0 trades in 7 years" reasoning was an
> unfair (starved) test and is RETRACTED.** Given a fair runway (15-min, where meanrev is alive
> 97% of the window), the strategy forms **107 cointegrated pairs**, fires **12,759 signals / 131
> entries**, and — once the cost gate is removed — **does take 258 trades**. But those trades are
> **gross ~break-even at best (gross PF 0.87–1.09) and net-negative after real pair-trading costs
> (net PF 0.11–0.74)**. The cost gate was correctly refusing unprofitable trades. **No edge — now
> proven properly, not assumed.** No code/structural bug found.

---

## PART 1 — Warmup audit (the daily test WAS starved)

Warmup (code: `meanrev.py:41-42`, `warmup_bars = timeframe_bars × (lookback + 10) = 3 × 510 =`
**1,530 decision bars**). Bars alive *after* warmup, on the real data:

| Timeframe | Warmup (calendar) | Window | Alive after warmup | **% of window alive** |
|---|---|---|---|---:|
| **Daily** | ~1,530 days ≈ **6.12 y** | 2017–2023 (audit selection) | 203 bars / 0.81 y | **11.7%** |
| Daily | ~6.12 y | 2017–2026 (full) | 811 bars / 3.24 y | 34.6% |
| **15-min** | ~61 days ≈ 0.24 y | 2017–2026 | 56,783 bars | **97.4%** |
| **5-min** | ~20 days ≈ 0.08 y | 2017–2026 | 173,126 bars | **99.1%** |

⇒ On the audit's **daily** selection window meanrev was alive only **~10 months of 7 years** — that
is **not a fair test** (confirms the owner's suspicion). **Intraday is the fair runway.** (Note: the
**live engine runs 15-min**, so live meanrev warms up in ~61 days and is *not* warmup-starved.)

---

## PART 2 — Pair-pool audit (can it form a pair? yes)

Instrumented candidate-pair funnel on 15-min FULL-25 (~20k bars, 2023→2026), by tier:

| Tier (capital) | active names | candidate same-sector pairs | pass ADF ≤ 0.05 | final pairs kept | honest trades |
|---|---:|---:|---:|---:|---:|
| T2 (₹10L) | 3 | 54 | 7 | 6 | 0 |
| T4 (₹1cr) | 20 | **1,106** | 146 | **107** | 0 |
| T5 (₹50L) | 30 | 1,782 | 243 | 177 | 34 |

The pair pool is **not** the fundamental blocker: even at T2 (3 names) it finds 6 cointegrated
pairs; at T4/T5 it finds 100–180. So meanrev is **not structurally pair-starved** at scale. (At T2
the pool is thin — top-3 by liquidity are often different sectors — which *compounds* the daily
starvation at small tiers, but it can still form some pairs.)

---

## PART 3 — The funnel & sensitivity ladder (DIAGNOSTIC ONLY)

### Funnel on the fair runway (15-min, FULL-25, ₹1cr, gate ON — honest)
`1,106 candidate pairs → 146 pass ADF ≤ 0.05 → 107 pass ALL filters (ADF + half-life + κ) →
12,759 signals emitted → 131 entries (|z| up to 14.3) → **0 trades**.`
Audit-reason tally (meanrev-only run): **0 `rounds_to_zero`, 0 `dust`, 612 `cost_gate` vetoes.**
⇒ Pairs form, signals fire, positions size fine — **every new entry is vetoed by the cost gate.**

### Ladder — relax one lever at a time (real costs unless noted)

| Config | final pairs | trades | gross PF | **net PF** | net P&L |
|---|---:|---:|---:|---:|---:|
| Default (gate ON, T4 2×) | 107 | **0** | — | — | — |
| **Gate OFF** | 107 | 14 | 0.57 | 0.40 | −₹59k |
| cost-gate 1× (gate ON) | 107 | 4 | 0.28 | 0.22 | −₹21k |
| Gate OFF + forced steady alloc | 107 | 258 | **1.09** | **0.74** | −₹366k |
| Gate OFF + forced + ADF 0.10, z 1.5 | 168 | 416 | 0.84 | 0.57 | −₹569k |
| Gate OFF + forced + ADF 0.20, z 1.5, κ 5, hl 5–300 | 332 | 793 | 1.01 | 0.69 | −₹486k |
| **5-min** gate OFF + forced | 166 | 330 | 0.87 | 0.33 | −₹992k |
| T5 honest (gate ON, 1.5×) | 177 | 34 | 0.26 | 0.11 | −₹46k |

**Reading it:** the moment meanrev is *allowed* to trade, **gross PF sits at ~0.87–1.09 (no
meaningful edge before costs)** and **net PF is always < 1** (real two-leg round-trip costs sink it).
Relaxing ADF / z-entry / κ / half-life only adds **more** trades that are **equally unprofitable**
(net PF stays 0.1–0.7). Higher frequency (5-min) is worse (more cost drag). Even the 34 trades that
*pass* the honest gate at T5 are **gross-negative (0.26)** — the gate's static `expected_edge_R=0.15`
assumption simply doesn't hold out-of-sample. Deflated Sharpe is **0.0** in every case.
(Diagnostic trial count: 14 relaxation configs.)

---

## PART 4 — Blunt verdict

**Primary: (i) HONEST NO-EDGE.** On a fair runway (intraday, alive ~97–99%, abundant cointegrated
pairs, signals firing, trades executing when permitted) meanrev's trades are **gross ~break-even and
net-negative after costs**. The cost gate's 0-trades was it *correctly* refusing unprofitable trades.

Secondary classifications, for honesty:
- **(ii) TEST ARTIFACT — applies to the DAILY evidence only.** The ~6-year warmup left meanrev alive
  just 11.7% of the 2017–2023 window, so the daily "0 trades" could not have meant anything. **That
  specific reasoning is retracted.** The verdict needed the fair intraday test — which now confirms
  no-edge.
- **(iii) STRUCTURAL / CONFIG BUG — RULED OUT.** No code bug: pairs form (107), signals fire
  (12,759), and trades execute (258) the instant the gate is lifted. The only *config* caveat is a
  **test-design mismatch** — meanrev's 1,530-bar warmup makes the **daily** clock an unfair runway
  (warmup ≈ all available daily history). This is **not** a defect to fix and **I changed nothing**;
  the live engine runs 15-min, where meanrev warms up in ~61 days and is genuinely alive — its live
  dormancy is the (i) no-edge + thin-T2-pair-pool + cost gate, not warmup.

**Explicit retraction/confirmation:** I **retract** the claim "meanrev = 0 trades in 7 years ⇒
no edge" *as argued from the daily test* (that test was starved). I **confirm** the **conclusion**
that meanrev has **no after-cost edge** — now proven on the fair intraday runway, where it trades
freely and loses (gross ≤ ~1.1, net < 1, deflated 0).

---

## One-line status

**meanrev — HONEST NO-EDGE (confirmed on a fair intraday runway): forms 100+ cointegrated pairs and
trades when unleashed, but is gross-break-even / net-negative after costs; the cost gate is doing its
job, and the prior daily "0 trades" was a warmup-starved artifact. Stays dormant; no bug; no change.**

*Reproducibility: `deploy/_audit/meanrev_diag.py` (instruments the real `MeanRevStrategy` via
adfuller/_fit_pair/generate_signals spies; reuses the walk-forward + CostModel harness); diagnostic
runs persisted `backtest_runs` ids 47–49; no `quantsys`/`qsdash` package code changed.*
