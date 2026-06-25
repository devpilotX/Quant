# Pillar 4 — Event-Driven (pre-registration + decision record)

**Status:** PRE-REGISTRATION LOCKED 2026-06-25. Branch `feature/pillar4-event-driven`.
Nothing here is enabled, live, or on paper. This document is written **before** any
combined backtest and is the binding record; per the stop-rule (§9) it is not edited
after the first gated evaluation except to append the final verdict.

This is a **fresh, independent** study. It does NOT touch, re-weight, or re-search the
closed combine-survivors work (FAILED: deflated 0.69 < 0.95, PBO 0.84) or its
2017–2026 sample. Novelty is not edge; a significant t-stat is not edge. Any sleeve
with no robust after-cost OOS edge is reported honestly and left disabled.

---

## 1. Data-reality audit (done first, on purpose) and implementability

Before writing any sleeve, I audited what event data actually exists in the repo.
**Finding: the only free data present is OHLCV cash (`data_cache/bhavcopy`) and F&O
(`data_cache/fno`: futures, option OI/prices, PIT single-stock-futures membership).
There is NO event data** — no EPS actuals/estimates, no announcement timestamps, no
SAST/insider/pledge/bulk-deal feeds, no merger terms, no index-reconstitution dates.

The brief (§3) assumed "corporate-announcement feeds already in the repo." **They are
not there.** Every candidate event is *defined by an announcement* whose timestamp and
terms are not recoverable from price/volume alone. Honest classification (brief §1):

| Sleeve | Required event data | Status on current data |
|---|---|---|
| (a) PEAD | EPS actual + expectation + **announcement datetime** | ❌ NOT IMPLEMENTABLE (no fundamentals/dates) |
| (b) Index rebalancing | NIFTY/sectoral add–delete + effective dates | ❌ NOT IMPLEMENTABLE (bhavcopy has no index membership; only a liquidity proxy) |
| (c) Merger / takeover arb | deal announcement, offer price, SAST timeline | ❌ NOT IMPLEMENTABLE (no deal feed) |
| (d) Corporate actions | announcement date (ex-date is price-detectable, but the *tradable drift* needs the announcement) | ⚠️ PARTIAL (ex-date only) |
| (e) Insider / bulk-block | disclosure + bulk/block-deal feed | ❌ NOT IMPLEMENTABLE (not in repo) |

**Consequence:** I will not write sleeve code against data that does not exist (that is
the "firing in the air" the brief forbids). Instead this increment delivers the
**data-agnostic event-study engine + the multiple-testing machinery** (reusable and
fully tested), locks this pre-registration, and treats **event-data acquisition as an
explicit, gated prerequisite** (§8). The **meanrev re-test (brief §6) is implementable
now** on existing price data and is the immediate next step (§7).

This is a feature, not a delay: when any one event feed is wired, its sleeve drops into
the already-built, already-tested engine with no re-litigation of the math.

---

## 2. Event-study core (built + tested this increment)

`src/quantsys/research/event_study.py` — market-model event study, data-agnostic
(callers pass aligned daily returns + an event index; nothing assumes an event type):

```
R_it = alpha_i + beta_i * R_mt + eps_it            (OLS, estimation window)
AR_it = R_it - (alpha_i + beta_i * R_mt)
CAR_i = sum_{t=t1..t2} AR_it ;  CAAR = (1/N) sum_i CAR_i
```

Significance (Indian single-stock ARs are fat-tailed and events inflate variance, so a
naive cross-sectional t over-rejects — three tests, must broadly agree):

- **BMP** (Boehmer–Musumeci–Poulsen 1991): standardize each CAR by its **Patell**
  forecast-error std `s_i·√(L + L²/T + (Σ_event(R_m−R̄_m))²/SS_m)`, then take the
  **cross-sectional** t of the standardized CARs — robust to event-induced variance.
- **Corrado (1989)** rank test — non-parametric, no normality assumption.
- **Generalized sign test** (binomial) — distribution-free.

No look-ahead is structural: `EventWindows` rejects any estimation window that does not
end strictly before the event window (tested). Tests: `tests/test_event_study.py`
(recovers a known injection, stays null under no-signal, blocks look-ahead, handles
insufficient data, works for announcement `(-1,+1)` and drift `(+2,+60)` windows). 7/7
green; ruff clean.

### Reused (NOT re-implemented) — non-destructive
- Deflated Sharpe + E[max SR] deflation by `n_trials`: `quantsys.backtest.metrics.deflated_sharpe` / `expected_max_sharpe` (Bailey/LdP, with skew/kurtosis).
- PBO via CSCV: `quantsys.research.validation.pbo_cscv`.
- Purged & embargoed K-fold: `quantsys.research.validation.purged_kfold`.
- Monte-Carlo P(SR<0): `quantsys.backtest.metrics.monte_carlo_resample`.
- Full Indian cost stack: `quantsys.costs.CostModel`. Data: `research.bhavcopy`, `research.fno`.

---

## 3. Pre-registered windows (locked; apply when each sleeve's data is wired)

- Estimation window: **[−250, −30]** trading days (≥120 usable obs required).
- Announcement/reaction window: **[−1, +1]** (information leakage day before + reaction).
- Drift window: **[+2, +60]** (PEAD / post-event drift).
- Index-rebalancing entry window: **[−5, −1]** before effective date; exit into rebalance-day flow **[0, +1]**.
- Event timestamp = **announcement datetime**, never the effective/ex date. An
  after-close announcement's earliest tradable bar is the **next session open**
  (enforced in the data adapter when built; the +1 in the windows reflects this).

---

## 4. IS / hold-out split (locked)

- **IS:** 2017-01-01 → 2023-12-31 (selection, walk-forward, all grid search).
- **Hold-out:** 2024-01-01 → present. **Touched exactly once**, for the single
  pre-selected config per sleeve and for the final combo.
- Survivorship-free, point-in-time membership; shorts only on PIT-eligible SSF names
  with FUTURE cost + ~1.5%/yr financing/borrow drag; longs at delivery-equity cost.

---

## 5. Hyperparameter grids and `n_trials` accounting (locked)

`n_trials` for the deflated Sharpe = the **grand total** of (sleeve × grid point)
combinations actually evaluated, summed across everything searched. Running tally:

| Sleeve | Grid (pre-declared) | Trials |
|---|---|---|
| meanrev re-test (§7) | z_entry ∈ {1.0, 1.5, 2.0, 2.5} × lookback ∈ {250, 500, 750}; all other meanrev params at current defaults | 12 |
| PEAD (when data) | SUE proxy ∈ {seasonal-RW, own-history-z} × drift window ∈ {(+2,+30),(+2,+60)} × decile cut ∈ {10%,20%} | 8 |
| index-rebal (when data) | impact model ∈ {linear-Kyle, √-impact} × entry lead ∈ {3,5,10}d | 6 |
| corp-action ex-date (when data) | event ∈ {bonus, split} × window ∈ {(-1,+1),(+2,+20)} | 4 |
| **Total searched so far** | meanrev only (others blocked on data) | **12** |

The final combined evaluation uses the cumulative total at that time. No grid is
expanded after results are seen (stop-rule §9).

---

## 6. The gate — must clear ALL FIVE, OOS, net of costs (locked)

1. OOS annualised Sharpe ≥ **0.80**
2. Deflated Sharpe ≥ **0.95** (`n_trials` = §5 grand total)
3. Monte-Carlo P(SR < 0) ≤ **0.10**
4. PBO ≤ **0.50** (CSCV)
5. Net-of-cost CAGR > **0**

Also reported (not gates): walk-forward equity, regime breakdown (trend/range/high-vol/
crash), parameter-sensitivity heatmaps, max drawdown, beta, per-sleeve correlation, and
correlation vs the existing survivors (BANKNIFTY-PCR flow, factor momentum) — combine
only low-correlation sleeves via a single pre-declared inverse-vol / risk-parity weight
(no weight search).

---

## 7. Methodological overrides (brief §10) — flagged + applied

1. **Data assumption corrected.** The repo has no announcement feeds (§1). I'm
   overriding "feeds already in the repo" with evidence and gating sleeves on data.
2. **SUE without PIT consensus.** No analyst-estimate data exists; per §3 of the brief I
   will (when PEAD data is sourced) use a **seasonal-random-walk / own-history SUE**
   proxy and disclose its upward bias (the true-surprise signal is attenuated, so a
   positive result is conservative; a null is partly a power problem). Flagged now so it
   is pre-registered, not a post-hoc excuse.
3. **Three significance tests, not one.** Brief §4.1 asked for BMP + a rank/sign test; I
   add the generalized sign test as a third, and require BMP **and** a non-parametric
   test to agree before treating an event as tradable — stricter than asked.
4. **DSR/PBO reused, not re-derived.** Re-implementing existing, tested math would be
   destructive duplication; I reuse `backtest.metrics` + `research.validation`.
5. **Meanrev re-test framed as a power/diagnosis question first** (§ below), not a
   threshold hunt — because the prior finding was ~0 qualifying cointegrated pairs, and
   **no z-threshold can fix a no-signal problem.** I verify the binding cause (absence of
   cointegration vs. wrong threshold) with evidence before reporting any grid result.

---

## 8. Data-sourcing plan (what unlocks each sleeve) — needs your go-ahead

Each sleeve is blocked only on its event feed; the engine + gate are ready. Feasibility
of *free* historical sourcing (NSE endpoints are anti-bot and shallow on history):

- **Bulk/block deals (e)** — `nseindia.com/api/historical/bulk-deals` has history; most
  feasible free source. Unlocks an informed-flow sleeve. **Recommended first.**
- **Index reconstitution (b)** — NSE press releases / archived constituent lists; semi-
  manual, moderate effort. Clean, well-identified event with a real mechanism.
- **Corporate actions (d)** — NSE/​BSE corporate-actions API gives ex-dates + some
  announcement dates; partial.
- **PEAD (a)** — needs an earnings dataset (actuals + dates); no clean free PIT source.
  Would require a paid/licensed fundamentals feed. Lowest free-data feasibility.
- **Merger arb (c)** — needs a curated deal database; no free historical source.

I will not fabricate any of these. Pick which feed(s) to wire and I build the adapter
(announcement-dated, PIT, next-open-tradable) + its pre-registered sleeve into this
locked frame.

---

## 8a. Price-shock proxy event study (implementable NOW — pre-registered before results)

Since announcement feeds are absent (§1) and an NSE scraper is fragile/low-EV, the
honest data-available test of "is there an event-drift edge in Indian equities" uses a
PRICE-SHOCK PROXY: large abnormal-return + volume days stand in for unobserved
news/earnings events (brief §3 permits a disclosed proxy). This screens whether ANY
post-event drift exists before investing in a real feed.

**Locked definitions (before looking at any CAAR):**
- Universe: the live config equities (clean, liquid, sector-labelled); market return =
  equal-weight mean daily return of that universe.
- Per stock, day t: standardized return `z_t = r_t / σ_{t-1}` with σ = 60-day rolling
  std (lagged, no look-ahead); volume ratio `v_t = vol_t / mean(vol_{t-20..t-1})`.
- **Event = |z_t| ≥ 4.0 AND v_t ≥ 2.0** (a 4σ move on ≥2× volume). Split by direction
  (up vs down). De-clustered: ≥30 days since the same stock's previous event.
- Windows: estimation [−250,−30]; reaction [−1,+1]; **drift [+1,+5] and [+1,+20]**.
- Engine: `research.event_study.run_event_study` → CAAR + BMP + Corrado + sign tests.
- **Screen rule (not the full gate — this only decides whether a sleeve is worth
  building):** a direction is a candidate only if its [+1,+20] CAAR is (a) significant
  (BMP p<0.05 AND a non-parametric test agrees, Bonferroni-aware over the ~4 tests) AND
  (b) clearly exceeds a ~30 bps round-trip cost. If neither direction qualifies →
  event-drift edge is absent on available data; STOP (no sleeve). If one qualifies →
  build that sleeve + run the full 5-criterion gate (§6).

### Result (2026-06-25, `scripts/_pillar4_event_shock.py`) — FIRST event lead
Config universe (23 eq, 2016–2026), CA-adjusted returns. Events: **143 up-shocks,
99 down-shocks** (|z|≥4, ≥2× volume, de-clustered). Reaction windows sane (+658 / −687
bps, the shock itself). Post-event DRIFT:

| Direction | drift [+1,+5] | drift [+1,+20] | verdict |
|---|---|---|---|
| UP-shock | −32 bps (ns) | −28 bps (ns) | no drift either way |
| DOWN-shock | −111 bps (BMP p .004, sign .028) | **−213 bps (BMP p .011, rank .087, sign .003), 66% negative** | **continued DOWN drift** |

**Finding: a significant DOWNSIDE-underreaction drift** — after a 4σ down move on volume,
the stock keeps drifting ~−2.1% over the next 20 days (a SHORT signal, SSF-tradable).
Up-shocks show no drift (asymmetry consistent with bad-news-travels-slowly / short-sale
friction). It **passes the pre-registered screen** (significant on BMP + sign; survives
Bonferroni over the 2 drift directions; on the sign test even over all 6 cells), and the
−213 bps swamps ~30 bps cost. **This is the first event-pillar lead that isn't dead on
arrival** and the first non-meanrev candidate this session.

**HONEST CAVEAT — screen ≠ gate.** This only says "drift exists, worth a sleeve." It is
NOT yet a deployable edge: ~9 down-shocks/yr is LOW frequency (hard to reach Sharpe≥0.8),
short-only adds SSF cost + financing + borrow risk, per-event variance is high, and the
full 5-criterion gate (§6: net Sharpe, deflated by the cumulative n_trials, PBO, MC) is a
far higher bar this project has never cleared. **Next step (locked):** build the
down-shock short sleeve (enter +1, hold ~20d, vol-target, SSF costs) and run the full
gate — expecting it to be the closest event lead but still likely sub-gate.

## 9. Binding stop-rule

Any sleeve/combo clearing ALL FIVE (§6) → candidate, kept INERT, propose forward-only
paper. Anything else → declare dead, report the honest numbers, STOP. No re-weighting,
no re-searching, no extra grid, no gate-loosening after the fact.

---

## 10. Build status

- [x] Data-reality audit + implementability classification (§1).
- [x] Event-study core engine + 3 significance tests + look-ahead guard (`event_study.py`), 7/7 tests green, ruff clean.
- [x] Multiple-testing toolbox confirmed reusable (DSR/PBO/purged-CV/MC).
- [x] Pre-registration locked (this document).
- [x] **Meanrev re-test (brief §6) — DONE. VERDICT: DEAD** (below).
- [x] **Price-shock proxy event study (§8a) — DONE. Found a DOWN-shock underreaction
  drift (first event lead, passes screen, not yet gated).**
- [ ] Down-shock short sleeve + full 5-criterion gate — NEXT (the one live lead).
- [ ] Announcement-fed sleeves (PEAD/index-rebal/merger/insider) — blocked on §8 data.

### Meanrev diagnosis result (2026-06-25, `scripts/_pillar4_meanrev_diag.py`)
Faithful port of `meanrev._fit_pair` (hedge-ratio bounds → ADF p≤0.05 → OU half-life
∈[10,200] → split-half κ-stability ≤2.5) on the **daily** survivorship-free bhavcopy
panel, config-universe same-sector pairs, IS 2017-2023, lookback 500:

- **23 equities, 35 same-sector pairs. Every one of 20 rolling windows had ≥1
  qualifying cointegrated pair (100%); mean 5, max 10; 30/35 pairs qualified at some
  point.** → **PAIRS FORM.** Dormancy is NOT a no-signal problem.
- **This corrects the brief's premise** ("~0 valid pairs over 7 years"): pairs form
  readily. It matches the prior careful 15-min finding (107 pairs formed and *traded*
  but were blocked by the **cost gate**, gross-negative). So the z-threshold change is
  meaningful, but the binding constraint is **net-of-cost edge** — a threshold alone is
  unlikely to clear the gate. (Caveat: live meanrev runs at ~45-min bars; this is a
  daily-timeframe diagnosis, a cleaner/lower-noise test that is generous to finding
  cointegration — if anything it overstates pair availability vs intraday.)
### Meanrev z×lookback grid RE-TEST result (2026-06-25, `scripts/_pillar4_meanrev_retest.py`)
Net-of-cost pairs backtest faithful to the engine (same qualification + z entry/exit/
stop/time-stop, max 5 pairs), long=delivery equity (28bps RT), short=SSF future (9bps
RT)+1.5%/yr financing. Walk-forward, IS-select → hold-out once, deflate by n_trials=12.
Daily-timeframe caveat as above. Effective trading window 2019–2026 (cache starts 2016,
minus the 750-day warmup).

- **z_entry IS a real lever** (z=1.0→103 trades @ mean|z|2.8; z=2.5→34 @ mean|z|6.3) —
  confirming the diagnosis (pairs form & trade). Per-trade net is faintly positive at
  short lookback (+24–41 bps), so there is a *whisper* of a signal.
- **But it does not survive as a portfolio.** Grid OOS: lb=250 ≈ flat (−0.27…+0.21
  Sharpe), lb=500/750 clearly negative (−0.4…−0.74 Sharpe, 25–43% drawdowns from stale
  equilibria breaking down OOS). **IS-selected winner (z=2.5, lb=500): OOS Sharpe −0.67,
  CAGR −10.2%, maxDD −25%, Deflated 0.009, PBO 0.64, P(SR<0) 0.85 → FAILS ALL FIVE.**
- **No (z, lookback) clears the gate.** The lone positive-OOS cell (z=2.5/lb=250, +0.21
  Sharpe / +0.4% CAGR) is tiny, fails the gate, and is NOT the IS-selected config —
  i.e., **changing the entry-z does NOT rescue meanrev** (the owner's hypothesis is
  tested and rejected). Honest cause: faint per-trade signal swamped by costs + negative
  tail (correlated stop-outs when cointegration breaks) + OOS instability.
- **VERDICT: DEAD** per the stop-rule §9 — no re-weight, no re-search, STOP.

**Process note (honesty):** the first run showed an absurd mean entry-z ≈ 75; a
trade-level probe caught it (the simulator double-subtracted the regression intercept,
`z=(resid−theta)/σ` instead of `resid/σ`). Fixed (`z=resid/σ`); the synthetic self-test
had masked it by using theta≈0. The numbers above are post-fix. This is logged so the
record shows the bug was found by verification, not buried.
- [ ] Portfolio correlation + combination — after ≥1 sleeve clears.
- [ ] Final gated metrics table + verdict.
