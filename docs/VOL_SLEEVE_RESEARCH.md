# quantsys — Vol-Selling Sleeve: Firm-Up Research

*Generated 2026-06-19. Research backtests only — `QS_LIVE_ARMED=0`, sleeve stays **disabled** in
the live engine, no live config/capital/risk changed, nothing armed/deployed. All runs reuse the
real `VolOptionsStrategy` signal + real option `CostModel` on **free daily NSE option chains**;
key runs persisted to the dashboard Backtest viewer (`backtest_runs` ids **34, 35, 37, 38**).*

> **Bottom line (blunt): the lead is DEAD under realistic costs — and fails the robustness gate
> even at ZERO cost.** The prior "MARGINAL / +₹6.87M, net PF 1.104" result reproduced exactly, but
> it is a **mid-price (EOD-close) illusion**: a **~1% entry half-spread (~2% full bid/ask) takes it
> to break-even**, and anything beyond that is net-negative. Even with *zero* transaction cost its
> deflated Sharpe is **0.37** and Sharpe **0.79** — both below the go-live bar (deflated ≥ 0.95,
> Sharpe ≥ 0.8). It also **failed a second, independent crash (Feb-2018)**, and **active management
> made it worse**. Recommendation: **do not pursue further without live intraday option quotes**;
> the sleeve stays disabled.

---

## PART 0 — Ground truth (verified before any new run)

1. **Sleeve wiring / safety.** `src/quantsys/strategies/voloptions.py` (`VolOptionsStrategy`,
   registry `@register("voloptions")`). **Disabled live:** no `voloptions` block in
   `/opt/quant/config/base.yaml` → schema default `enabled=False`; no `runtime_config` override;
   **0 voloptions entries in `decisions.kelly` ever**. The live `LiveRunner` never populates
   `state.extra['option_chains']` (no `OptionUniverseManager`), so the strategy is a guaranteed
   no-op live. Research ran only in throwaway `docker compose run --rm` containers against the
   `quant_histdata` data volume — it cannot reach the live engine.
2. **Prior result reproduced (deterministic).** `sim_voloptions.py`, ₹1cr base, held-to-expiry:
   **2457 spreads, gross PF 1.156, net PF 1.104, net +₹6,872,004, fees ₹3,143,363, deflated
   Sharpe 0.354 (n_days 465), folds net>1 = 4/6** (1.086 / 1.534 / **0.845** / 1.247 / 1.134 /
   **0.844**), **COVID-2020 net PF 2.255** (worst −₹62,691), **Feb-2018 untraded** (inside the
   80-bar RV warmup). Identical to `BACKTEST_PL_REPORT.md §14`. ✔
3. **Data inventory.** Free daily chains (NIFTY + BANKNIFTY) from NSE F&O bhavcopy:
   originally **2018-01 → 2026-06** (2083 days). Extended for this study back to **2017-01**
   (now **2331 days with chains**) so Feb-2018 clears the warmup. Structure: per day, underlying +
   nearest-2 expiries, strikes ±15%, CE/PE close + OI.
4. Hypotheses are stated inline per step below (**expected → actual**).

---

## PART 1 — Improvement steps (expected vs actual)

### Step 1 — Cost-stress (free; entry bid/ask half-spread, regime-widened)
Model: cross the bid/ask on **both legs at entry** (held-to-expiry has no exit cross), half-spread
`h` of premium; "regime-widened" multiplies `h` by ×3 when the entry's realized vol is in the top
quintile. (Existing CostModel slippage/fees still applied on top.)

**Expected:** optimistic COVID profit shrinks; test = does net PF stay > 1 and deflated hold?
**Actual — net PF collapses with tiny spreads; break-even at ~1% half-spread:**

| Entry half-spread | net PF | net P&L | Sharpe | deflated | folds net>1 |
|---|---:|---:|---:|---:|---:|
| **0% (mid, baseline)** | **1.104** | +₹6.87M | 0.80 | **0.354** | 4/6 |
| 0.5% | 1.051 | +₹3.35M | 0.40 | 0.179 | 4/6 |
| 0.75% | 1.021 | +₹1.39M | 0.17 | 0.109 | 4/6 |
| **1.0%** | **0.995** | −₹0.30M | −0.04 | 0.066 | 4/6 |
| 1.5% | 0.948 | −₹3.45M | −0.43 | 0.021 | 2/6 |
| 2.0% | 0.905 | −₹6.26M | −0.80 | 0.006 | 2/6 |
| 2.5% | 0.860 | −₹9.18M | −1.19 | 0.001 | 2/6 |
| 5.0% | 0.692 | −₹20.1M | −2.84 | 0.000 | 0/6 |

**Verdict:** **FAILS.** The edge survives only if the all-in option transaction cost is **< ~2% of
premium** (1% half-spread/leg at entry). Real NIFTH index ATM spreads are ~1–2% full (≈ break-even),
and BANKNIFTY + the OTM wings this strategy trades are wider — so at realistic costs it is
net-negative. The COVID window is more resilient (net PF still > 1 up to ~2% half-spread, because
the IV-spike credits are large) but cannot carry the rest of the book.

### Step 2 — Diagnose the losing recent fold (2024-12 → 2026-06, net PF 0.844)
**Expected:** either a fixable logic issue or a genuinely hard (calm/low-vol) period.
**Actual — genuinely hard period + a structural reframe:**
- **By leg type in the fold:** credit spreads net PF **1.164** (+₹0.93M, fine); **debit spreads net
  PF 0.497 (−₹2.62M)** — the loss is almost entirely long-premium **debit** spreads.
- **By symbol:** NIFTY net PF 0.701 (−₹1.86M); BANKNIFTY 1.034 (≈flat).
- **Whole-sample reframe (the important one):** **debit (long-vol) spreads made ALL the lifetime
  profit (net PF 1.19, +₹8.7M); credit (premium-*selling*) spreads net LOST (net PF 0.91, −₹1.8M).**
  Per-year profit concentrates in **2019 (1.42) and 2020 (1.55, COVID)** and bleeds in calm years
  (**2021 0.83, 2025 0.77**).
- **Conclusion:** this is **not really a vol-*selling* / VRP strategy — it is a long-volatility /
  long-gamma strategy** whose P&L comes from buying cheap vol that pays off in crashes/trends and
  bleeds theta in calm regimes. 2024–26 was a low-vol grind → debit spreads decayed. **Not a
  fixable bug** — a structural weakness. (And long-gamma with many small theta losses + rare big
  wins is precisely the profile most destroyed by per-trade transaction costs — explaining Step 1.)

### Step 3 — Trade the Feb-2018 crash (chains extended to 2017)
**Expected:** if genuinely crash-robust, the sleeve should survive a *second, independent* crash.
**Actual — FAILED.** With the warmup now satisfied, Feb-2018 trades (52 spreads) but is
**net-NEGATIVE: net PF 0.894, −₹97,807** (worst −₹61,106) — even at zero cost. COVID-2020 still
survives (net PF 2.255). Extended full sample (2017→2026, 2694 spreads, s=0): net PF 1.103,
deflated **0.368** — essentially unchanged. **Verdict:** the "crash-surviving" property is
**COVID-specific, not general.** Feb-2018 was a sharp vol *spike + snap-back* (whipsaw) rather than
a sustained directional move; a long-gamma book needs follow-through, so it lost.

### Step 4 — Daily-managed / rolled variant vs held-to-expiry
Managed = mark each spread daily against the chain's later-day quotes; exit at +50% of max profit,
or −2× max-loss stop, or roll at DTE ≤ 1.
**Expected:** active management *might* cut tail losses and improve robustness.
**Actual — WORSE.** (extended data)

| Variant | net PF | net P&L | deflated |
|---|---:|---:|---:|
| Held-to-expiry, s=0 | **1.103** | +₹7.26M | 0.368 |
| Managed pt50/stop2, s=0 | **0.926** | −₹4.99M | 0.010 |
| Held-to-expiry, s=0.5% | 1.051 | +₹3.62M | 0.188 |
| Managed pt50/stop2, s=0.5% | 0.884 | −₹7.83M | 0.002 |

**Verdict:** active management **degrades** it — early exits pay the option bid/ask a *second* time
and forfeit the theta that held-to-expiry harvests on defined-risk verticals. Held-to-expiry is the
better (less-bad) policy.

### Step 5 — Re-judge against the go-live gate
Gate: OOS Sharpe ≥ 0.8 **and** deflated ≥ 0.95 **and** P(SR<0) ≤ 0.10.

| Scenario | Sharpe | deflated | Pass? |
|---|---:|---:|---|
| **Best case (zero cost, mid-price, held)** | 0.79 | 0.37 | **NO** (fails both) |
| Realistic spread (≥1% half) | < 0 | < 0.07 | **NO** (net-negative) |
| Feb-2018 second crash | — | — | **NO** (net PF 0.89) |
| Managed variant | < 0 | 0.01 | **NO** |

**Blunt verdict: WEAKER than the prior "marginal" call — effectively DEAD.** It fails the
robustness gate *even with zero transaction costs*, is net-negative at realistic option spreads,
and its crash-survival was a one-off (COVID, not Feb-2018). The prior +₹6.87M was a mid-price
artifact, not a tradeable edge.

---

## PART 2 — Will meanrev or F&O ever auto-trade in live paper?

**meanrev** (`MeanRevConfig`, `strategies/meanrev.py`): at ₹10L the tier is **T2** (`max_strategies=2`)
so meanrev *is* in the active set, but it only emits a signal when it finds a **cointegrated pair**
among the tier's `max_instruments` (T2 = 3) liquidity-top names: ADF p < 0.05, half-life ∈
[10, 200] bars, split-half κ ratio ≤ 2.5, then entry at |z| ≥ 2.0, and the trade must clear the
cost gate (edge ≥ **4×** round-trip cost at T2). In every backtest this session and prior it found
**0 tradeable pairs** in the 22-equity universe at these costs. The paper `explore_floor` sets
`f > 0` but cannot manufacture a *signal* — **no pair ⇒ no trade.** So meanrev will auto-trade live
only if two of the top-3 liquidity names become genuinely cointegrated *and* the spread reaches
|z| ≥ 2 *and* the edge clears 4× cost — rare-to-never at the current universe/costs.

**Futures** (NIFTY-FUT / BANKNIFTY-FUT): two gates. (1) **Token resolution** — they currently miss
the Angel instrument master, so they aren't in the live universe at all. (2) **Affordability under
the 25%/name cap** — one lot must satisfy `0.25 × equity ≥ lot notional`:
- NIFTY: 65 × ₹23,854 ≈ **₹15.5L/lot** ⇒ equity ≥ **₹62.0L**.
- BANKNIFTY: 30 × ₹57,199 ≈ **₹17.2L/lot** ⇒ equity ≥ **₹68.6L**.

So futures become sizeable only around **₹62–69L+** (T5 tier, ≥ ₹50L) — *and* only after the
token-master fix. **At ₹10L today, futures can NEVER trade** (one lot ₹15.5L exceeds total equity,
let alone the ₹2.5L per-name cap). **Options/the vol sleeve can never trade live** as built (no
chain feed wired into `LiveRunner`, and `voloptions` disabled). **At ₹10L today, only equities
(trend, plus meanrev if a qualifying pair ever appears) can trade.**

**Confirmation — no live disturbance:** every run used throwaway `docker compose run --rm`
containers; the live `quantsys-engine-paper-1` container was **never modified** (Up 36 h, not
restarted). Post-research state verified unchanged: `mode=paper`, `paper_capital=₹1,000,000`,
`QS_LIVE_ARMED=0`, heartbeat fresh, `voloptions` disabled. The only writes were research data
(2017 chain extension) into the data volume and research rows into `backtest_runs` (the viewer's
purpose). No config / capital / risk / universe change; nothing armed.

---

## Conclusion

The vol sleeve — the only previously-promising lead — does **not** firm up. Reframed honestly it is
a **long-gamma** strategy whose entire edge is (a) a **mid-price illusion** that dies at ~2% all-in
option spread, (b) **concentrated in 2019–2020** and bleeding in calm years, (c) **not robust to a
second crash** (Feb-2018 net-negative), and (d) **failing the go-live gate even at zero cost**
(deflated 0.37 ≪ 0.95). Active management makes it worse. **Verdict: no robust, tradeable edge —
the sleeve stays disabled.** The only thing that could revive the question is **live intraday option
quotes** (real bid/ask, intraday management) — a paid-data exercise — and even then the cost-stress
break-even (~2% spread) sets a high bar. The GO-LIVE gate stays correctly CLOSED; stay paper.

*Reproducibility: drivers `deploy/_audit/sim_voloptions.py` (baseline) and `sim_vol2.py`
(cost-stress / diagnostics / managed) + `build_optchain_2017.py` (data extension) on the VPS;
runs persisted to `backtest_runs` ids 34/35/37/38; no `quantsys`/`qsdash` package code changed
(engine suite remains 129×3 green this session).*
