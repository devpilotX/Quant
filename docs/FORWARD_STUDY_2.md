# quantsys — Forward Study 2 (pre-registered, paper-only)

*Registered: **2026-07-02 (evening, after market close)**. SUPERSEDES Forward Study 1
(same-day: Study 1 ran exactly one session, 2026-07-02, and NO evaluation read of it
ever happened — its registration forbade in-flight changes and named a fresh
registration as the only sanctioned path to change anything, which is this document).
Status: deployed to the paper engine at quant.devpilotx.com effective the 2026-07-03
pre-open recycle. Nothing in this file may be tuned while the study runs; a parameter
change ends this study and requires a fresh registration.*

## Why a same-day supersession is legitimate

Study 1 was registered before the 2026-07-02 open. During its first live session the
operator review found four **implementation defects** (not strategy results) and the
owner directed a strategy-surface expansion:

1. **3–5 decisions per 15-min bar**, each on a partial cross-section (the live loop
   stepped on every drain; bars complete per-symbol on next tick). Also: **no decision
   ever ran on the session's close bar**, and decisions were stamped at wall-clock
   instead of bar time.
2. **1-share order churn** (MARUTI buy/sell/buy on consecutive bars): the dust floor
   was a flat ₹5k regardless of equity.
3. **Phantom equity at every daily recycle**: paper cash/realized were not persisted;
   the durable ₹15cr float was re-applied on top of the carried book at each 08:50
   restart — from day 2 the equity series would have compounded the open book's cost
   basis daily. **Study 1's registered equity metrics were unmeasurable as designed.**
4. The weekly backtest-refresh unit hung its 2026-06-28 run (no HTTP timeouts in the
   broker SDK; Sunday-02:30 maintenance window).

Defect 3 alone invalidates Study 1's registered evaluation plan, and fixing 1–3
changes fill timing and order flow — i.e. the study as registered could not be
salvaged by "keep running it". Because **zero reads had occurred** (the study was
hours old) there is nothing to peek at: superseding today has the same evidentiary
status as registering correctly yesterday. Forward data still cannot be overfit
because it still does not exist.

**Honest prior, restated:** the 2017–26 alpha search is CLOSED (no sleeve cleared the
gate; `docs/RESEARCH_CLOSEOUT.md`). This study measures the execution stack at scale
and collects untouched forward evidence. Paper profit is an outcome to be measured,
never a promise. Paper results do NOT arm live trading (Gate below).

## Frozen setup (2026-07-02) — deltas from Study 1 marked ★

| Item | Value |
|---|---|
| Venue | paper engine on live Angel One data; every fill priced by the full Indian cost model |
| Paper float | ₹15cr → tier T6 (40-instrument view, ★ 7 strategy slots, 2.5× gross cap). ★ Cash/realized now persist across restarts (`paper_broker_state`); the float re-applies only on operator CHANGE, and never over an open book |
| Universe | 48 liquid large-caps + NIFTY-FUT/BANKNIFTY-FUT near-month + NIFTY index (regime source) — unchanged |
| Decision clock | 15-min bars, IST; ★ exactly ONE decision per bar, on the full cross-section, stamped at bar time (backtest convention); ★ the session close bar (15:15) is decided; ★ time-based aggregator flush |
| Paper exploration | Kelly floor 0.05/sleeve, cost gate bypassed (paper-labeled) — unchanged |
| Index futures | min-lot promotion unchanged (1 lot iff risk ≤ 0.5% E) |
| Risk stack | unchanged: 0.6% risk/trade, 13% vol target, per-name/sector/net caps, daily −2.5% kill, 18% DD throttle, 20% hard kill |
| ★ Dust floor | effective min order notional = max(₹5k, 1bp of equity) — kills sub-₹15k rebalance dribbles at ₹15cr |
| ★ Meta-allocation | learned regime-conditional Kelly tilt: per-(sleeve × regime-label) EWMA edge buckets (soft-assigned by regime probability, walk-forward by construction) tilt f by clip(1 + 0.4·Σp·tanh(t/2), 0.5, 1.5). Config priors per regime label extended to all sleeves (base.yaml) |

### Sleeves under study (6 active)

| Sleeve | Pillar | Prior (frozen record) | Role |
|---|---|---|---|
| trend | P1 | no edge (deflated ≤ 0.11) | plumbing + forward log |
| meanrev pairs | P3 | dead (all five gate criteria failed on re-test) | plumbing + forward log |
| expiry fade | P1 | borderline-luck (deflated 0.33) | forward log |
| factor 12L/12S momentum+low-vol, monthly | P2 | OOS-survivor, sub-gate | **primary candidate** (params identical to Study 1) |
| ★ **downshock** (z ≤ −3.5 on ≥2× volume, de-clustered, enter +1, hold 10 sessions, ≤5 concurrent, ATR stops) | P4 | **project-best lead**: hold-out Sharpe 1.75, MC 0.002, PBO 0.31, **deflated 0.46 → historically DEAD** (`docs/PILLAR4_EVENT_DRIVEN.md`) | promoted from the zero-risk tracker to a PAPER sleeve — the closeout's sanctioned forward test. Disclosed deviation: unhedged short (one hedge lot exceeds the explore-floor group budget); net-exposure caps bound residual beta. The 20:30 IST zero-risk tracker keeps running unchanged as the parallel control |
| ★ **tom** (long NIFTY-FUT + BANKNIFTY-FUT, last 2 → first 3 weekdays of month) | P1b | suggestive, NOT significant: +12.2 bps/d in-window vs +4.1 outside on our universe 2017–26, t = 1.53 (`reports/study2_sanity_2026-07-02.md`) | new hypothesis; trivial cost, tiny surface |

### Rejected arm (documented for n_trials honesty)

★ **reversal** (5-day cross-sectional, 8L/8S weekly) was implemented, tested, and
**refused deployment**: the pre-deployment sanity check on 2017–26 daily data showed
the ENTIRE 3×3 parameter neighborhood net-negative (Sharpe −0.58…−0.94, deployed cell
−0.78 / −8.7% CAGR, grid PBO 0.84). Deploying a known cost-sink to "see what happens
forward" would burn study risk budget, so the sleeve ships dark (`enabled: false`).
The 9 evaluated configs are added to the cumulative n_trials ledger (12 meanrev + 9
downshock + 9 reversal = 30 to date).

## Evaluation (pre-registered)

- **First read: 2027-01-05** (first session ≥ 6 months from start), then quarterly.
  Reads are observational; any parameter change = new study + registration.
- Metrics per sleeve and total, from recorded paper fills: net Sharpe (daily),
  **deflated Sharpe (N = 6 sleeves)**, max drawdown, beta to NIFTY, net CAGR.
- **Gate to even DISCUSS live deployment** (unchanged): forward net Sharpe ≥ 0.80,
  deflated ≥ 0.95, P(SR<0) ≤ 0.10, net CAGR > 0 on ≥ 6 months — plus rotated
  Angel One credentials (2026-06-11 leak) and SEBI algo registration.
  `QS_LIVE_ARMED=0` throughout.
- **Study-level stop:** total paper drawdown > 12% → operator pauses, study ends
  early, verdict "risk stack insufficient at scale".
- The learned regime tilt is PART of the system under test (its parameters are
  frozen above); its audit trail (`kelly/regime_tilt` events) is part of the record.

## Capital event log

| Date | Event |
|---|---|
| 2026-07-02 | `paper_capital` step 1,000,000 → 150,000,000 (Study 1 start, carried) |
| 2026-07-02 | Study 2 supersedes Study 1 after one session; equity series CONTINUES (no reset) — the recorded curve is continuous across the supersession |

## Operations attached to this study

Unchanged from Study 1: 08:50 IST engine recycle (now cash-safe), 09:55 self-check,
20:30 down-shock tracker (control), weekly backtest refresh — ★ moved to Saturday
11:00 IST with socket timeouts (the Sunday-02:30 run hung on the broker's maintenance
window, root-caused 2026-07-02).

## FINAL VERDICT (appended 2026-07-26, per the registration's append-only rule)

**TERMINATED � CONTAMINATED. No strategy conclusion may be drawn from this run.**

The study is void on data integrity, not on results. `is_session_open()` tested the
wall clock only � no weekday test, no holiday calendar � so 09:15�15:30 on any
Saturday, Sunday or exchange holiday counted as an open session. The engine therefore
decided and filled against the snapshot ticks Angel One replays into a shut market
(identical price, unchanged cumulative volume ? a flat zero-volume "bar" every bucket).

Measured over 2026-07-02..25 on the recorded book:

| Evidence | Value |
|---|---|
| Zero-volume flat fabricated bars | **10,497 / 32,561 = 32.2%** of all bars |
| Non-trading days traded | 4 Saturdays, 3 Sundays, + Muharram (Fri 2026-06-26) |
| Fills on days NSE never opened | **769** (~Rs 1.03 lakh of modelled fees) |
| Post-15:15 buckets that were fabrications | 1,252 / 2,069 = 61% |

Those weekend positions were carried into the following weekday sessions, so the
contamination cannot be subtracted out � hence termination rather than cleaning.

### Secondary defects found in the same review (all fixed)

1. **`kelly.explore_floor` overrode the allocator's own verdict 1,774 times.** The
   allocator correctly cut trend and expiry to `f=0.000` on clearly negative evidence
   (trend forward Sharpe **-4.16**) and the floor forced them back to 0.050 every
   time. It is edge-agnostic by design ("NOT withdrawn by negative evidence"), so no
   amount of adverse forward evidence could ever switch it off.
2. **Min-lot promotion was manufacturing size, not rounding.** All 852 promotions
   inflated the risk target ~10x (BANKNIFTY-FUT median 0.097 of a lot, NIFTY-FUT
   0.113). Those two symbols produced -Rs 1.24 lakh gross from 14 trades = **71% of
   the study's entire gross loss** � on a sleeve the allocator wanted at f=0.
3. **Reported P&L was gross.** The dashboard showed -Rs 1,73,884 realized against a
   true net of -Rs 9,15,037: a **5.3x understatement**. Hit rate and profit factor
   were gross too (36.8% vs **13.7%** net; PF 0.560 vs **0.139** net).
4. Structural, NOT fixed here (needs a research decision, not a patch): `trend`
   declares `expected_edge_R = 0.12` � 12 bps of notional against a measured ~28 bps
   round-trip cost in the cash-equity delivery segment. Recomputing the actual fills
   under a single-stock-futures cost structure gives 14.14 ? 7.33 bps
   (-Rs 3,38,920, 48%) � which halves the bleed but does not create edge, because
   gross P&L was already negative.

### Disposition

- Rows retagged `mode='paper'` ? **`paper_s2`** (9,815 rows across 7 tables). Nothing
  deleted: the evidence above stays queryable and is the justification for the fixes.
- Paper broker re-baselined to the full Rs 15cr float, `realized_total=0`,
  `fees_total=0`. The reset is mandatory, not cosmetic: the stored -173,883.91 was
  GROSS and the accumulator is net from this commit on.
- Pre-fix DB snapshot: `deploy/backups/pre-fix-20260726/quantsys-pre-fix.sql`.
- `market_bars` deliberately left intact � it feeds only dashboard display and the
  position price fallback, no decision path, and the fabricated rows are the evidence.

### Precondition for any Study 3

A fresh pre-registration. It must state up front that the alpha search is CLOSED
(`docs/RESEARCH_CLOSEOUT.md`: gate 3/5, deflated Sharpe 0.69 < 0.95, PBO 0.84 > 0.50)
and that item 4 above � declared edge below round-trip cost � is unresolved. A forward
study cannot fix a negative-expectancy specification; it can only measure it honestly,
which is now what it will do.
