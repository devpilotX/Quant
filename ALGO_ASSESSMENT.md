# quantsys — Honest Algorithm Assessment & Rating

*Author: Claude (Opus 4.8), after a deep two-session read of the full codebase, live
system, and real-data backtests. Date: 2026-06-17. This is a candid technical
review — not marketing. Everything below is grounded in code/behaviour I actually
verified, not assumptions.*

---

## TL;DR — the verdict

**Overall: 6.5 / 10.**

That single number hides two very different scores, so read the decomposition:

| You're really asking… | Score | Why |
|---|---|---|
| "Is this a well-built *trading **system***?" | **8.5 / 10** | Genuinely strong engineering, risk design, cost honesty, and validation discipline — well above typical retail/solo work. |
| "Does this *algo* have a proven, money-making **edge**?" | **2 / 10** | On real NSE data it takes **zero trades** after costs ("Gate CLOSED"). The strategies are textbook and show no validated out-of-sample edge yet. |

The blend lands at ~6.5 because a trading system's *purpose* is profit (so the missing
edge matters a lot), but you have built a genuinely excellent **chassis** — it just
doesn't yet have a proven **engine** under the hood. The good news: the chassis is the
hard, slow part to build well, and you've done that. The edge is "research," and it's
findable — just not where you're currently looking (5–15 min equity trend/pairs at full
retail costs).

> **The missing piece (proven by experiment — see §6): a signal with positive expectancy
> *before* costs.** I forced your strategy to trade on real data with fees switched off,
> and it *still* loses money (gross profit factor 0.883). So no cost/instrument/horizon
> fix can rescue it — the nearest concrete, fixable lever you already own is the **broken
> regime filter** (the NIFTY-index data isn't wired in, so the trend strategy trades in
> every market regime, including the choppy ones that kill trend-following).

---

## 1. What this system actually is

A complete, end-to-end **systematic trading platform** for Indian markets (NSE/BSE via
Angel One SmartAPI), built solo:

- A **pure, deterministic decision engine** (`quantsys`) — *the same code* runs
  backtest, paper, and live (a discipline most shops never achieve).
- Two alpha strategies: **trend** (TSMOM + Donchian breakout, hysteresis) and
  **meanrev** (Engle-Granger/OU cointegration pairs). An options/vol sleeve exists but
  is disabled.
- A **regime overlay**: an in-house diagonal-Gaussian HMM with a deterministic
  vol-percentile fallback ladder.
- A full **risk + sizing stack**: fixed-fractional risk → fractional Kelly (shrunk,
  incubated) → volatility targeting → layered exposure caps → cost-gated lot rounding.
- A **backtester** with walk-forward IS/OOS, **deflated Sharpe**, Monte-Carlo, and a
  sensitivity sweep — i.e. it tries hard *not* to fool itself.
- A production **control plane**: FastAPI + Next.js dashboard, auth (argon2id + TOTP +
  CSRF + re-auth), command queue, websocket hub, Postgres/TimescaleDB, alerts, backups,
  TLS — **deployed live in paper mode** at `quant.devpilotx.com`.
- **169 tests** (126 engine + 43 backend) and a clean CI-style green bar.

That is a *lot*, and it's coherent.

---

## 2. Scorecard (dimension by dimension)

| Dimension | Score | Reasoning |
|---|---:|---|
| **Software architecture** | 9/10 | Pure/deterministic core; one code path for backtest/paper/live; typed config tree (`config/schema.py`) where *every* tunable lives; clean module boundaries; JSON state round-trips bit-identically. This is professional-grade structure. |
| **Risk management (design)** | 8.5/10 | Fractional-Kelly (never full), zero-mean shrinkage, incubation floor, per-instrument/sector/correlation-cluster/ADV/gross/net/margin caps (all monotone, group-joint so a hedge leg is never orphaned), ATR stops + cooldowns, daily-loss & max-DD kills, **reconciliation = freeze** (broker is ground truth), two-lock go-live gate. Institutional in spirit. |
| **Cost realism & honesty** | 9/10 | Full verified Indian fee schedule + square-root market-impact + a **cost gate** that *blocks* trades whose edge doesn't clear costs. The system **honestly refuses to trade with no edge** — the single rarest and most valuable trait here. Most retail algos lie to themselves about costs; this one won't. |
| **Validation methodology** | 8/10 | Walk-forward with carried state, deflated Sharpe (penalises the number of trials), Monte-Carlo on the OOS path, no-lookahead enforced and tested. You're using the *right* tools to avoid overfitting. |
| **Operational maturity** | 7.5/10 | Live deploy, TLS, auth, monitoring/alerts, DB backups, systemd timers, self-healing market-data feed, audit trail. Strong for a solo build. |
| **Code quality / tests** | 8/10 | 169 green tests including cap-invariant and kill-switch e2e proofs; readable, well-commented, deliberate design notes. |
| **Demonstrated alpha / edge** | **2/10** | The decisive one. On real NSE 5-min **and** 15-min data the engine takes **0 trades** → "Gate CLOSED." Worse: when *forced* to trade with fees off, the signal loses money **gross of fees** (profit factor 0.883 — see §6). There is currently **no money-making signal, even before costs.** |
| **Data & market access** | 3/10 | One retail broker; 5–15 min OHLCV bars; effectively equities only (index/futures/options not wired in); no order-book/L2, no alternative data. Retail-grade inputs. |
| **Execution & latency** | 4/10 | REST/websocket via a shared VPS, seconds-scale latency, **never run against the real broker** (paper-on-live only). Fine for 15-min horizons; nowhere near low-latency. |
| **Strategy originality** | 3/10 | TSMOM/breakout and cointegration pairs are straight out of the public literature — well-implemented, but heavily competed and largely arbitraged away at retail cost levels. |

---

## 3. What's genuinely strong (and rare)

1. **It is honest.** The cost gate + deflated Sharpe mean the system tells you the *truth*
   ("no edge, gate closed") instead of a curve-fitted fantasy equity curve. 90% of retail
   "algos" fail precisely here. This is the trait that separates people who eventually
   make money from people who blow up.
2. **Backtest ≡ live by construction.** The same `decide()` runs everywhere, so a
   backtest result actually means something. Most retail backtests are fiction because the
   live path diverges.
3. **The risk architecture is real.** Kelly + vol targeting + layered caps + kills +
   reconcile-freeze is conceptually what a real desk runs. If you *did* have edge, this
   stack would let you deploy it without blowing up.
4. **Engineering discipline:** typed config, determinism, persistence, 169 tests, audit
   trail, a clean live deployment. This is the slow, unglamorous foundation that's hardest
   to build and easiest to skip.

---

## 4. What's missing or weak (the honest part)

1. **No edge (the whole point).** Two standard signals, full retail costs, liquid
   large-cap equities, 5–15 min bars → mathematically a losing combination. trend's
   `expected_edge_R = 0.12` can't clear ~20 bps round-trip (delivery-STT-gated) costs;
   meanrev finds no stable cointegrated pairs in this universe/window.
2. **Capacity/where you're fishing.** Liquid NSE large-caps at 5–15 min is one of the
   *most* efficient, most-competed corners of the market. There's little left there for a
   retail cost structure.
3. **Only equities are actually tradeable.** Index futures (`NIFTY-FUT`, `BANKNIFTY-FUT`)
   and the index itself aren't resolved from the instrument master, and the options sleeve
   is off. Futures have **far better cost economics** (≈5 bps STT vs 20 bps equity
   delivery) and leverage — yet they're the part not wired in. The regime HMM is also
   stuck in `warmup` because the index history is absent.
4. **Never run against a real broker.** Paper-on-live exercises the plumbing (now
   verified end-to-end), but fills, slippage, partial fills, and rejects against the real
   venue are untested.
5. **Two strategies, no research pipeline.** There's no systematic alpha-discovery loop —
   just two hand-picked classics.

---

## 5. The core truth: no demonstrated edge (yet)

This must be stated plainly because it's the thing that matters: **as of today the system
has never identified a positive-expectancy trade on real data.** That is not a bug — it's
the system being honest. Three things are simultaneously true:

- The **platform** to trade an edge is built and excellent.
- The **edge itself** does not exist in the current strategies/universe/timeframe.
- Finding edge is **research**, and it's the genuinely hard part — the 5% of the work that
  determines 95% of the P&L.

You've built the 95%-of-the-effort part that produces 5% of the returns, and you have not
yet started the 5%-of-the-effort part that produces 95% of the returns.

---

## 6. THE MISSING PIECE — found, with evidence

You asked me to *find* the missing piece, so I ran the decisive experiment instead of
guessing. I forced the strategy to trade on **real 15-min NSE data with the cost gate
switched OFF** (so trades actually happen), then split realized P&L into **gross (before
fees)** and **net (after fees)**. That single test separates the only two possibilities:
"good signal, costs too high" vs. "no signal at all."

**Result — 7,500 bars scored, 6,854 forced trades:**

| Metric | Value | Reading |
|---|---:|---|
| **Gross P&L (before fees)** | **−₹2,72,290** | the signal loses *before* costs |
| Total fees | −₹14,61,307 | costs then bury it |
| **Net P&L (after fees)** | **−₹17,33,598** | |
| Gross hit-rate | 52.8% | wins *slightly* more often than it loses… |
| **Gross profit factor** | **0.883** | …but losers are bigger → net-negative even gross |

### The missing piece is genuine alpha — **not** cost structure.

The signal **loses money even before a single rupee of fees** (gross profit factor 0.883
< 1). That is conclusive: futures vs. equities, intraday (MIS) vs. delivery STT, longer
horizons, more capital — **none of them can help**, because they only reduce *costs*, and
the signal is already under water *gross*. You cannot cost-optimise a negative-expectancy
bet into a positive one; you'd just lose more slowly. (This also *upgrades* my earlier,
gentler framing — the edge problem is slightly worse than "no edge"; it's mildly
*negative* edge.)

### Why it's losing — the diagnosis

A 52.8% hit-rate with a 0.883 profit factor means **frequent small wins and occasional
bigger losses** (average win ≈ 79% of average loss). For a *trend* strategy that is
**backwards** — healthy trend-following has a *low* hit-rate with *large* winners
(positive skew). Your profile is the textbook signature of a trend system **trading the
chop and getting whipsawed** — entering on noise, getting stopped, repeat.

And there's a concrete, in-system reason this is allowed to happen: **the regime overlay
that is *supposed* to prevent exactly this is non-functional.** The Gaussian-HMM that
classifies *calm-trend / calm-range / turbulent* is permanently stuck in `warmup` because
the `NIFTY` index has **no historical data wired in** (it isn't resolved from the Angel
instrument master). With the regime stuck neutral, `trend` fires in **every** regime —
including the ranging/turbulent ones where trend-following is supposed to be switched
*off*. You built the filter; you just never connected its power supply.

### So the "missing piece" is a stack — in order of how fixable it is

1. **A working regime filter (most concrete, already 90% built):** wire the `NIFTY` index
   feed so the HMM actually fits and gates `trend` to trending regimes only. Highest-
   leverage *engineering* fix and the most plausible way to nudge gross edge toward
   positive — the machinery exists; it's starved of one data series.
2. **Fix the exit asymmetry:** the win/loss size ratio (~0.79) is the real killer. Trend
   must **cut losers faster and let winners run**; the current exits do the opposite.
   Parameter/logic work in `strategies/trend.py` (exit threshold, ATR stop multiple,
   trailing).
3. **Find a genuinely differentiated edge** (event-driven, options skew, index-rebalance,
   small-cap niches, cross-asset). TSMOM/breakout on liquid large-caps is among the most
   arbitraged signals in existence and may simply have **no** exploitable edge at retail.
4. **Only then** the cost/instrument upgrades (futures, MIS) — they matter *exclusively
   after* the signal is gross-positive.

**Bottom line:** the missing piece is **a signal with positive expectancy before costs**,
and the nearest concrete lever you already own is the **dead regime filter** (NIFTY index
data → HMM → regime gating). Everything else — costs, leverage, horizon — is downstream of
first having a real edge.

---

## 7. Comparison with Jane Street

Let's be honest up front: **this isn't a fair comparison, and it shouldn't be.** Jane
Street is one of the best trading firms that has ever existed. Comparing a solo retail
project to JS is like comparing a very well-built kit car to a Formula 1 team. The right
question isn't "how do I beat them" — it's "what can I learn, and where can I play a game
they don't bother with."

| Dimension | Your quantsys | Jane Street |
|---|---|---|
| **People** | 1 (you) + an AI pair | ~3,000+, incl. hundreds of researchers/quants/devs |
| **Primary game** | Directional/stat-arb on 15-min bars | HFT **market-making**, ETF/options arbitrage, microstructure |
| **Latency** | Seconds (REST/websocket, shared VPS) | Micro/nanoseconds; colocated, FPGAs, custom networking |
| **Data** | One broker's OHLCV bars | Direct exchange feeds, full order book, proprietary datasets |
| **Transaction costs** | Full retail (the thing killing your edge) | Often **negative** — they *earn* the spread/rebates as the market maker |
| **Capital** | ₹10L paper | Tens of billions; trades ~10%+ of US ETF volume |
| **Edge source** | Public-literature signals | Liquidity provision, scale, speed, decades of proprietary research |
| **Risk framework** | Kelly + vol target + caps + kills | Firm-wide, but *philosophically the same ideas* — at vastly larger scale |
| **Annual trading P&L** | ₹0 (paper, no edge yet) | Reportedly on the order of ~$10B net trading revenue (private; press estimates) |

**Where you genuinely rhyme with them (give yourself credit):** disciplined position
sizing, volatility targeting, hard risk limits, and — most importantly — **honest
measurement** (you don't deploy what you can't prove). That *mindset* is the same one that
makes JS great. Your *scale, speed, data, and cost structure* are not, and never will be,
in the same universe — and that's fine.

**The strategic lesson from JS:** their edge is being the fastest, cheapest liquidity
provider at enormous scale. You can *never* win that game — you have the opposite cost
structure (you pay the spread + full retail fees; they collect it). So **don't try to
trade where they trade.** A retail systematic trader's only realistic edge is in places
big firms *can't or won't* go:

- **Smaller-capacity niches** (illiquid small/mid-caps, specific events) too small to move
  a multi-billion-dollar book.
- **Longer horizons** (multi-day/multi-week) where you pay costs rarely and microstructure
  speed is irrelevant — your cost gate stops hurting you.
- **Structural/positioning inefficiencies** (expiry effects, index rebalances, specific
  options-flow patterns) rather than raw price prediction.
- **Instruments with better economics** (futures: ~5 bps STT, leverage) — which is exactly
  the part you haven't wired in yet.

---

## 8. A realistic roadmap to a higher rating

Roughly in order of expected payoff:

1. **Change the battlefield, not just the timeframe.** Move to **daily/multi-day**
   horizons and/or **futures** (NIFTY/BankNifty), where your costs stop dominating. This
   alone could turn the gate from CLOSED to "has something to evaluate."
2. **Wire in the futures + index** (resolve continuous near-month contracts; fix the
   `NIFTY` index feed so the regime HMM actually fits). This is the single most impactful
   *engineering* task for getting real, economically-viable trades.
3. **Build an alpha-research loop**, not more hand-picked signals: a way to generate,
   test (with your already-excellent deflated-Sharpe/walk-forward harness), and retire
   candidate signals. The harness is built; feed it more ideas.
4. **Find one genuinely differentiated signal** in a niche (events, expiry, small-cap
   reversals, options skew). One real edge > ten textbook ones.
5. **Then, and only then,** validate non-synthetic OOS, run the paper window against real
   fills, and consider tiny live capital. Your safety chain is already built for exactly
   this.

If you do (1)+(2) and surface even one robust after-cost edge, the "demonstrated edge"
score jumps from 2 → 6 and the overall moves to ~8/10 — because the hard platform work is
already done.

---

## 9. Final word

**6.5/10 overall — and that's a genuinely good score for a solo retail build.** You've
done the part most people can't (a correct, honest, well-engineered, deployed system with
real risk controls) and not yet done the part most people *think* they've done but
haven't (find real edge). Don't measure yourself against Jane Street — measure yourself
against the 99% of retail algo traders who have a pretty equity curve and no idea their
backtest is a lie. By that bar, you're already in rare company: **your system's biggest
strength is that it told you the truth.** Now go give it something true worth trading.

---

## 10. Addendum — Phase-4 edge search: rigorous results (2026-06-17)

We ran a disciplined edge hunt: one candidate at a time, parameters pre-registered and
**not tuned**, each judged on **gross profit factor (fees-off) out-of-sample** via
walk-forward (carried state, no lookahead) plus a **deflated Sharpe** (penalising the
number of trials). Gate to even consider costs/futures: **gross PF clearly > 1.2 OOS.**

### What was tested, and the honest result

| Candidate | Method | OOS gross PF | Deflated Sharpe | Verdict |
|---|---|---:|---:|---|
| **trend** (TSMOM+breakout) | forced trades, real 15-min | **0.79** | — | No signal even *before* costs |
| **regime overlay** (NIFTY HMM gating) | in-sample vs OOS | IS 1.04 → **OOS 0.79** | **0.01** | In-sample artifact; vanished OOS |
| **expiry** (month-end MR), shallow | 16k bars, 5 folds | 1.16 | 0.73 | Looked promising… |
| **expiry**, deepened (frozen params) | **40k bars (6.5y), 8 folds** | **1.04** | **0.33** | …**borderline luck** — edge *decayed* with more data; 5/8 folds >1; net PF **0.115** (net −₹5.4M vs ₹5.5M fees) |
| **vol sleeve** (IV–RV skew) | — | **untestable** | — | No historical option-chain data obtainable (see below) |

### Two findings that matter

1. **A genuine bug was fixed and deployed (correctness, not edge):** the NIFTY index was
   resolving to a candle-less token (26000) instead of the AMXIDX token (99926000), so the
   regime HMM sat in `warmup` forever. Fixed; the regime now fits. **But** the regime gain
   did **not** survive OOS — so it improves correctness, not profitability.
2. **The deepened expiry test is the cleanest lesson:** as we added data (16k→40k bars,
   5→8 folds) with **frozen parameters**, the apparent edge *shrank* (gross PF 1.16→1.04,
   deflated Sharpe 0.73→0.33). A real edge strengthens with data; this decayed toward
   noise. And even its tiny gross edge (~₹9/trade over 11,402 trades) is ~50× too small to
   cover fees — **futures' cheaper costs would not rescue it** (≈ −₹1.3M net even at 4×
   lower fees). So the "gross robustly >1, net <0 → use futures" case did **not** arise.

### Why the vol sleeve is untestable here (data feasibility, verified)

The Angel instrument master holds **only current/future option contracts** (earliest NIFTY
expiry 07-Jul-2026 → 2030); **all expired contracts are gone**, so historical option
**chains cannot be reconstructed**, and there is no historical-chain endpoint. Free NSE
F&O bhavcopy is **daily-only** and a heavy scraping/parsing build that doesn't fit the
intraday sleeve; intraday option history is **paid-vendor only**. Per the no-faking rule,
the sleeve was **not** tested on synthetic chains.

### Phase-4 verdict

**Across trend, the regime overlay, and expiry — rigorously OOS-tested — there is no
robust, after-cost, out-of-sample edge.** This is not a wiring failure; it is the honest
economic result for public-literature signals on liquid NSE large-caps at retail costs.
The system's integrity held throughout: it refused every non-edge, and the deeper we
looked, the more honestly it reported "no."

### Realistic paths that could actually change the answer (none are quick)

- **Buy the data:** a paid intraday option-chain history → makes the vol sleeve (real
  structural edge in vol-selling) *testable* under this same gate.
- **Change frequency/asset:** lower-frequency (daily/weekly) or a different market where
  retail costs don't dominate — the cost gate stops vetoing everything.
- **External/alternative alpha:** a genuinely differentiated data source, not price-only.

What this phase did **not** do — and shouldn't — is manufacture a number. The 6.5/10
stands: an excellent, honest platform still waiting for a real edge to run on.

---

## 11. Addendum — futures / daily / vol / execution / ops mission (2026-06-17): verdict per path

Same gate throughout (walk-forward OOS, gross **and** net PF after the real cost model,
deflated Sharpe, per-fold consistency; frozen alpha params; no tuning; nothing deployed
on an unvalidated result; live gate shut).

| Path | What was done | Honest verdict |
|---|---|---|
| **Futures** | Built a roll-adjusted continuous daily-futures dataset from free NSE bhavcopy (24 series, 2024→26), futures cost model, real lot sizes. Matched-period equity-vs-futures test. | **Cost benefit CONFIRMED** (futures ~3.5× cheaper → net loss **halved**, −₹8.1M→−₹3.5M; net PF 0.72→0.85) — **but no edge**: the signal is gross-negative on the testable window, so futures can't rescue it. Cheaper costs reduce the bleed, they don't create edge. |
| **Daily / multi-day** | Resampled cached 5-min → daily; ran trend & meanrev through the gate. | trend @ 6-day/long-history = **lumpy break-even** (net PF 1.01, deflated **0.14**, 3/8 folds — carried by 2 trending years). trend @ true-daily/recent = **gross-negative**. meanrev = no edge. **A real but lumpy, regime-dependent trend *premium*, not a robust edge.** |
| **Vol-selling (options)** | Verified data feasibility. | **Blocked on data** — Angel drops expired contracts (no historical chains), no chain endpoint, free bhavcopy is daily-only, intraday option history is paid-vendor only. Not faked on synthetic chains. Untested. |
| **Real-broker execution** | Validated gating + read paths against the real venue. | **Real money is triple-locked** (verified by executing the gate: needs adapter + `QS_LIVE_ARMED=1` + a passing real backtest — all currently fail). Read paths (login/funds/positions/orders) parse the real venue cleanly. **Residual (needs a funded account):** order→fill→partial→reject→slippage lifecycle + reconcile-vs-real-fills. |
| **Ops** | See `docs/OPS_RUNBOOK.md`. | Repo **secret-free** ✓ (verified). Backup **restore drill passed** ✓. TOTP configured + e2e-verified ✓. **Pending (human steps, runbook'd):** rotate the leaked Angel creds (blocking for live), fill Telegram token, one off-VPS `rsync`. |

**Two findings worth keeping:** (1) the futures **cost mechanism is validated and material** — so *if* a gross-positive signal is ever found, futures meaningfully improve its net; (2) the system's **small-tier sizing cannot trade F&O lots** (1 NIFTY lot ≈ ₹1.5M notional; lots 65–3000 units) — futures need ₹50cr+ capital.

### Mission verdict

**Across futures, daily/multi-day, and (prior) trend/regime/expiry/meanrev — no robust,
after-cost, out-of-sample edge.** The single closest thing is a lumpy daily-trend
*premium* (break-even at retail cost, modestly improvable by futures' cheaper costs, but
not consistent). Vol-selling — the one path with a known structural premium — is
**blocked on paid data**. The platform, safety chain, and ops are in good shape; the
edge is not there yet, and the honest gate stayed shut the whole way.

### The one path most likely to change this

**Buy intraday historical option-chain data** → test the defined-risk vol-selling sleeve
(variance-risk-premium is a real, persistent structural edge) under this same gate. That
is the highest-probability route to a genuine after-cost edge — everything else here has
been honestly tested and found wanting.

---

## 12. Addendum — vol-selling sleeve on FREE daily chains (2026-06-18): the first crash-surviving lead

Built the multi-year daily index-option chain from **free NSE bhavcopy** (dual-format:
old `historical/DERIVATIVES` + UDiFF; NIFTY+BANKNIFTY, **2,083 days 2018→2026**). Tested
the dormant defined-risk vertical-spread sleeve (real `VolOptionsStrategy` signal logic;
IV via BSM; **held-to-expiry 1:1 verticals** = true defined risk; real option cost model).
A first run was corrupted by a sizing artifact (crossed EOD prices → `max_loss`→0.01 → 92k
lots); fixed (reject degenerate spreads, clip P&L to defined-risk bounds, cap lots).
Determinism verified (2× identical).

| Vol sleeve, 2,457 spreads, after real costs | Value |
|---|---:|
| Gross PF / **Net PF** | 1.156 / **1.104** (+₹6.9M) |
| Net daily Sharpe / **deflated (n_trials=8)** | 0.80 / **0.354** |
| **COVID-2020 crash window** | **net PF 2.255**, worst trade **−₹62k (contained)** |
| Fold containing COVID | net PF **1.534** |
| Folds net>1 | 4/6 |

**Verdict: MARGINAL — but the strongest lead of the entire search, and the *only* strategy
that is both net-positive after costs AND survives a crash fold.** Defined-risk caps held
through COVID (no blow-up; worst trade −₹62k), and it harvested the post-spike IV premium
(net-positive *through* the crash). It is economically grounded — the variance risk premium
is a real, persistent structural edge.

**Why it is only MARGINAL, not a confirmed edge (no overclaiming):**
- **Deflated Sharpe 0.354** (vs the 0.95 robustness bar) — not statistically robust; 4/6 folds.
- **EOD-close pricing** — daily settles, no bid/ask; the COVID profit (net 2.255) is almost
  certainly *optimistic* (real crash execution had enormous spreads). Conservative
  degenerate-spread filtering applied, but the entry credit is approximate.
- **Held-to-expiry** only (not the daily-managed/rolled version the sleeve also supports).
- **Feb-2018 fell in the 82-day RV warmup** (data starts 2018-01), so only COVID was traded
  through (it is the more severe test, and it passed).

**How to turn this marginal lead into a confirmed edge** (the honest next step for *this*
path): (1) extend chains to ~2017 to trade through Feb-2018; (2) validate on **paid intraday
option data** to confirm the post-crash profit survives realistic bid/ask; (3) test the
daily-managed version. Only then is it trustworthy — and **nothing is deployed** (research
backtest; sleeve stays disabled; live gate shut).

### Where the whole search now stands
Vol-selling is the **first and only crash-surviving, net-positive lead** — marginal but real
and structural. Everything else (trend ×3 horizons, regime, expiry, meanrev, futures) is
confirmed no-edge. The two honest forward moves: **(a)** firm up this vol lead (intraday +
2017 data), and **(b)** the one category never explored — **external / alternative non-price
alpha** (sentiment, order-flow, positioning, fundamental events) — best pursued via a small
**alpha-research loop** that batch-screens many candidates through this same gate.
