# quantsys — Honest Three-Way Comparison

*Retail (A) vs **quantsys** (B) vs industry/bank-grade (C). Generated 2026-06-18. Every
claim in column **B** is cited to a file, config value, or a measured run. Columns A and C
are general industry knowledge (qualitative; firm figures are public estimates, not
measured here). Where a quantsys number isn't measured, it says **"not measured."***

| # | Dimension | (A) Typical retail algo | (B) **quantsys** — with evidence | (C) Industry (Jane Street / Citadel / RenTech-class) |
|---|---|---|---|---|
| 1 | **Speed / latency** | Seconds (broker REST, home internet) | **Decision-to-order latency: NOT MEASURED.** By design: 15-min decision clock (`config/base.yaml` `decision_bar_minutes: 15`), REST + websocket order path (`execution/angelone.py`, `execution/marketdata.py`) — **never run against a live broker** (paper only). Realistically seconds *when* it places an order; unbenchmarked. | Micro/nanoseconds; colocated, kernel-bypass / FPGA, direct cross-connects |
| 2 | **Data** | One broker's OHLCV bars | One broker's bars (Angel One; 26-symbol universe in `config/base.yaml`) **+** free **daily** NSE F&O bhavcopy (verified reachable; `_audit/fetch_bhav_futures.py`, `build_optchain.py`) **+** reconstructed **daily** option chains (**2,083 days** NIFTY+BANKNIFTY 2018→2026, `/app/data/optchain`). **No order book, no direct exchange feed, no alt-data.** | Full L2/L3 order book, direct exchange feeds, proprietary + alternative datasets |
| 3 | **Compute** | A laptop | **4 vCPU, 23 GiB RAM, 193 GB disk, Ubuntu 24.04** (Oracle Cloud VPS, **shared** with ~13 other sites per `VPS_DEPLOYMENT_REPORT.md`; CPU model not exposed) | Thousands of cores, GPU/FPGA farms, dedicated datacenters |
| 4 | **Strategy sophistication** | Usually one unvalidated indicator | Public-literature signals, **rigorously validated**: trend TSMOM+Donchian (`strategies/trend.py`), Engle-Granger pairs (`strategies/meanrev.py`), Gaussian-HMM regime (`regime/`), month-end mean-reversion (`strategies/expiry.py`), defined-risk vol spreads (`strategies/voloptions.py`). Not original alpha — but honestly tested. | Decades of proprietary research, ML, microstructure, cross-asset |
| 5 | **Accuracy / edge** | Claimed high; usually curve-fit | **No robust after-cost edge.** Demonstrated-edge score **2/10** (`ALGO_ASSESSMENT.md`). Honest real-data backtests took **0 trades** ("Gate CLOSED" — `backtest_runs` id=1 5-min, id=2 15-min). Best lead = vol-selling: **net PF 1.104, deflated Sharpe 0.354** (marginal, not robust). See `BACKTEST_PL_REPORT.md`. | Consistent, capacity-scaled, risk-adjusted edge (billions in annual trading revenue, public estimates) |
| 6 | **Risk management** | Often none | Institutional-grade *design*: fractional Kelly (`portfolio/allocation.py`), vol targeting (`VolTargeter`), layered caps — instrument/sector/correlation/ADV/gross/net/margin (`risk/rules.py`), kill-switches + drawdown throttle + **reconcile=freeze** (`risk/engine.py`), two-lock **livegate** (`bridge/livegate.py`). | Firm-wide real-time risk, same concepts at vastly larger scale |
| 7 | **Validation honesty** | Curve-fit to one in-sample backtest | Walk-forward OOS (`backtest/walkforward.py`), **deflated Sharpe** counting trials (`backtest/metrics.py`), **cost gate** that vetoes negative-edge trades (`portfolio/sizing.py`), real fee model (`costs.py`). The system **refuses to trade with no edge** and says so. | Rigorous OOS, deflation, capacity & regime analysis |
| 8 | **Engineering quality** | Notebook / script | **129 engine tests + 43 dashboard tests** (pytest, all green ×2 on 2026-06-18); **one `decide()` path** for backtest=paper=live (`engine/decision.py`); deterministic + JSON state round-trip; deployed (docker-compose, host-nginx TLS, VPS at commit on `origin/main`). | Large eng orgs, formal CI/CD, but same discipline |
| 9 | **Capital** | ₹ thousands–lakhs | **₹10,00,000 paper** (`runtime_config.paper_capital = 1,000,000`; mode=paper). Backtests used ₹5cr–₹100cr *sizing bases* (see P&L report) — none is real money. | ₹ billions (USD tens of billions AUM/buying power) |
| 10 | **Cost structure** | Full retail fees | **Full retail** (`costs.py`: STT, exchange, GST, stamp, slippage, sqrt-impact — verified June-2026 schedule). Equity gated at *delivery* STT (~20bps RT); futures ~5bps STT (≈3.5× cheaper, confirmed in tests). | Often **negative** — earns the spread / exchange rebates as a market maker |

---

## Conclusion — the blunt truth

**Where quantsys genuinely beats typical retail (real, evidenced):**
- **Engineering & validation honesty.** 172 passing tests, one code path, deterministic, deployed — and a validation gate (walk-forward + deflated Sharpe + cost gate) that **rejected every signal without a real after-cost edge** instead of curve-fitting a pretty equity curve. This is the rarest and most valuable trait here, and it is real.
- **Risk architecture.** Fractional Kelly, vol targeting, layered caps, kill-switches, reconcile-freeze, and a triple-locked live gate — institutional in *design*, verified by executing the gate (it blocks live even with a simulated adapter).

**Where it is stuck at retail tier and structurally cannot compete with industry:**
- **Speed** (15-min bars, REST/websocket, shared VPS) vs micro/nanosecond colocated/FPGA.
- **Data** (one broker's bars + free *daily* bhavcopy) vs full order book + direct feeds + alt-data.
- **Compute** (4 vCPU shared VM) vs core/GPU/FPGA farms.
- **Capital & costs** (₹10L paper, full retail fees) vs ₹billions with negative/rebate cost structures.
None of these gaps is closable by a solo retail build. They are firepower gaps, not effort gaps.

**Where it only *rhymes* with the big firms — mindset, not capability:**
Disciplined sizing, hard risk limits, and **honest measurement** (don't deploy what you can't prove). That philosophy is shared; the scale, speed, data, and cost structure are not, and never will be.

**On the "world top-3 quant" goal — straight answer:**
**Not reachable on raw firepower for a solo retail build.** You cannot out-speed, out-data, out-compute, or out-capital Jane Street / Citadel / RenTech — they win precisely on the dimensions you can't touch, and they pay *negative* costs while you pay full retail. The *only* realistic edge for this setup is **small-capacity structural niches the giants ignore** (positioning/flow effects, specific options-VRP corners, illiquid mid/small-cap inefficiencies) — capacity too small to move a multi-billion-dollar book. Today, even that is unproven: the one marginal lead (vol-selling VRP) is net PF 1.104 / deflated 0.354 and needs intraday data to confirm. The honest standard to measure against is not Jane Street — it's the 99% of retail algos whose backtest is a lie. By *that* bar, this system is in rare company because it tells the truth. By the top-3 bar, it is not, and pretending otherwise would be the exact dishonesty this system was built to avoid.
