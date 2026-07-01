# quantsys — Forward Study 1 (pre-registered, paper-only)

*Registered: **2026-07-02**, before any forward results existed. Status: RUNNING on the
paper engine at quant.devpilotx.com. Nothing in this file may be tuned while the study
runs; a parameter change ends this study and requires a fresh registration.*

## Why this study is legitimate

`RESEARCH_CLOSEOUT.md` (2026-06-22) closed the historical alpha search: **no sleeve
cleared the deployment gate** on 2017–2026 data, and further tuning on that sample is
statistically invalid (PBO 0.84 — that path manufactures false edges). The closeout
names exactly one legitimate continuation: *a separate, newly pre-registered,
FORWARD-only out-of-sample effort on unseen data.* This is that study. Forward data
cannot be overfit because it does not exist yet.

**Honest prior, stated up front:** the historical evidence says these sleeves have ~no
edge. This study (a) measures the full execution stack end-to-end at realistic scale,
and (b) collects untouched forward evidence for the two OOS-survivor ideas. Paper
profit is an *outcome to be measured*, never a promise — and paper results, good or
bad, do NOT arm live trading by themselves (see Gate below).

## Frozen setup (2026-07-02)

| Item | Value |
|---|---|
| Venue | paper engine on live Angel One data; every fill priced by the full Indian cost model (brokerage/STT/exchange/SEBI/stamp/GST/slippage/impact) |
| Paper float | **₹15,00,00,000 (₹15cr)** → tier **T6**: 40-instrument view, all 4 strategy slots, 2.5× gross cap. Chosen so the tier ladder, index-futures lots and factor breadth are actually exercised; per-trade risk stays a fixed *fraction* of equity, so the float is a scale parameter, not a cheat |
| Universe | 48 liquid large-caps + NIFTY-FUT/BANKNIFTY-FUT near-month + NIFTY index (regime source) — `config/base.yaml` |
| Decision clock | 15-min bars, IST session |
| Paper exploration | Kelly floor 0.05 per sleeve, cost gate bypassed (paper-labeled, live/backtest unaffected) |
| Index futures | min-lot promotion ON: a single-leg futures signal that rounds below 1 lot takes exactly 1 lot iff that lot risks ≤ 0.5% of equity |
| Risk stack (unchanged) | 0.6% base risk/trade, 13% vol target, per-name/sector/net caps, daily −2.5% kill, 18% DD throttle, 20% hard kill |

### Sleeves under study

| Sleeve | Pillar | Historical verdict (prior) | Role here |
|---|---|---|---|
| trend | P1 | no edge (deflated ≤ 0.11) | plumbing + forward log |
| meanrev pairs | P3 | dead (0 tradeable pairs 7y) | plumbing + forward log |
| expiry fade | P1 | borderline-luck (deflated 0.33) | forward log |
| **factor** (12L/12S market-neutral momentum+low-vol, monthly, daily-panel) | P2 | OOS-survivor, sub-gate (deflated 0.074→0.583 in combo) | **primary candidate** |
| down-shock tracker | P4 | 4/5 gate, failed deflation | signal-only, zero-risk daily log (`quant-downshock.timer`) |

Factor study params frozen: `top_k=12`, `min_universe=34`, monthly (21 session days),
12-1 momentum + low-vol z-composite, EQUITY-only, daily panel seeded from broker
daily candles. *This is an implementability-constrained variant (40-name view), not a
re-run of the broad-universe research config — treated as a NEW hypothesis.*

## Evaluation (pre-registered)

- **First read: 2027-01-02** (6 months), then quarterly. Reads are observational;
  any parameter change = new study.
- Metrics, per sleeve and total, computed from recorded paper fills: net Sharpe
  (daily), **deflated Sharpe (N=5 sleeves)**, max drawdown, beta to NIFTY, net CAGR.
- **Gate to even discuss live deployment** (unchanged from livegate): forward net
  Sharpe ≥ 0.80, deflated ≥ 0.95, P(SR<0) ≤ 0.10, net CAGR > 0 on ≥ 6 months of
  forward data — **plus** rotated Angel One credentials (2026-06-11 leak) and SEBI
  algo registration. `QS_LIVE_ARMED=0` throughout the study.
- **Study-level stop:** total paper equity drawdown > 12% → operator pauses the
  engine and the study ends early with verdict "risk stack insufficient at scale".
  (The engine's own kills fire long before this in normal operation.)

## Capital event log

| Date | Event |
|---|---|
| 2026-07-02 | durable `paper_capital` 1,000,000 → **150,000,000** (study start; the recorded equity series shows a deliberate step here) |

## Operations attached to this study

- 08:50 IST (Mon–Fri) `quant-recycle.timer`: engine restart pre-open — fresh broker
  session/instrument master, near-month futures re-roll, factor panel re-seed, clean
  memory baseline.
- 09:55 IST daily `quant-selfcheck.timer`: health endpoint, engine heartbeat age,
  restart count, disk — GREEN/RED line to `deploy/_audit/selfcheck.log`, RED fails
  the unit loudly.
- 20:30 IST daily `quant-downshock.timer`: Pillar-4 zero-risk forward log.
- Feed parks outside session hours (no overnight reconnect churn); websocket stack
  pinned (`smartapi-python==1.5.5`, `websocket-client==0.59.0`).
