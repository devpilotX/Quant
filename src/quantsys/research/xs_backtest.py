"""Cross-sectional, monthly-rebalanced factor backtester.

Honest by construction:
  * point-in-time universe (survivorship-free, from bhavcopy),
  * factor at month-end t uses only data <= t; returns earned AFTER t,
  * real costs via the engine's own `quantsys.costs.CostModel` (delivery equity),
  * daily equity curve so metrics reuse `quantsys.backtest.metrics` unchanged.

Portfolios:
  * long_only  — equal-weight top-K by factor (carries market beta),
  * long_short — equal-weight top-K long / bottom-K short, dollar-neutral
                 (gross = leverage, net = 0): the pure-alpha / market-neutral test.

The benchmark is the equal-weight return of the eligible universe (buy-and-hold)
— the same bar the prior probe used: a factor must beat *holding the basket*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantsys.backtest.metrics import compute_metrics, deflated_sharpe, monte_carlo_resample
from quantsys.config.schema import CostConfig
from quantsys.core.types import Instrument, InstrumentKind
from quantsys.costs import CostModel

from quantsys.research import factors as F


@dataclass(frozen=True)
class BTConfig:
    factor: tuple[str, ...] = ("momentum",)   # composite = mean of z-scores
    side: str = "long_short"                  # "long_short" | "long_only"
    top_k: int = 30
    top_n_universe: int = 200
    adv_window: int = 60
    leverage: float = 1.0
    min_price: float = 20.0


@dataclass
class BTResult:
    equity: pd.Series                          # daily
    bench: pd.Series                           # daily equal-weight universe
    metrics: dict = field(default_factory=dict)
    bench_metrics: dict = field(default_factory=dict)
    n_rebalances: int = 0
    avg_names: float = 0.0
    realized_beta: float | None = None
    turnover_ann: float | None = None


def prepare_matrices(panel: pd.DataFrame) -> dict:
    """Precompute the wide matrices once so a config grid reuses them."""
    rets = F.daily_returns(panel)
    return {
        "rets": rets,
        "adjp": F.adjusted_prices(rets),
        "turnover": F.wide(panel, "turnover"),
        "close": F.wide(panel, "close"),
    }


def _equity_one_way_cost_frac(cost_model: CostModel) -> float:
    """One-way delivery-equity cost as a fraction of notional (taxes + slippage;
    delivery brokerage is 0 so this is ~notional-independent)."""
    inst = Instrument(symbol="X", token="", exchange="NSE", kind=InstrumentKind.EQUITY,
                      lot_size=1, tick_size=0.05, point_value=1.0, sector="x",
                      adv=5_000_000, margin_rate=1.0)
    rt = cost_model.round_trip(inst, qty=10_000, price=500.0, delivery=True)
    return rt / (2.0 * 10_000 * 500.0)


def run_xs_backtest(panel: pd.DataFrame, cfg: BTConfig,
                    cost_model: CostModel | None = None,
                    start: str | None = None, end: str | None = None,
                    n_trials: int = 1, mc: bool = False,
                    prepared: dict | None = None) -> BTResult:
    cost_model = cost_model or CostModel(CostConfig())
    one_way = _equity_one_way_cost_frac(cost_model)

    P = prepared or prepare_matrices(panel)
    rets, adjp, turnover, close = P["rets"], P["adjp"], P["turnover"], P["close"]
    cal = rets.index
    rebal = [d for d in F.month_end_dates(cal)]

    w_prev = pd.Series(dtype=float)
    daily_port: list[tuple[pd.Timestamp, float]] = []
    daily_bench: list[tuple[pd.Timestamp, float]] = []
    betas: list[float] = []
    turnovers: list[float] = []
    names_count: list[int] = []
    n_reb = 0

    for i, t in enumerate(rebal[:-1]):
        nxt = rebal[i + 1]
        if start and t < pd.Timestamp(start):
            continue
        if end and t > pd.Timestamp(end):
            break
        uni = F.eligible_universe(turnover, close, t, top_n=cfg.top_n_universe,
                                  adv_window=cfg.adv_window, min_price=cfg.min_price)
        if len(uni) < cfg.top_k * 2:
            continue

        # composite factor = mean of component z-scores (already universe-restricted)
        zs = [F.compute_factor(name, rets, adjp, turnover, t, uni) for name in cfg.factor]
        zs = [z for z in zs if len(z)]
        if not zs:
            continue
        score = pd.concat(zs, axis=1).mean(axis=1).dropna().sort_values()
        if len(score) < cfg.top_k * 2:
            continue

        longs = score.tail(cfg.top_k).index
        w = pd.Series(0.0, index=uni, dtype=float)
        if cfg.side == "long_short":
            shorts = score.head(cfg.top_k).index
            w[longs] = +cfg.leverage / cfg.top_k
            w[shorts] = -cfg.leverage / cfg.top_k
        else:
            w[longs] = cfg.leverage / cfg.top_k
        names_count.append(int((w != 0).sum()))

        # transaction cost at rebalance, charged as an equity hit on day t
        dw = w.subtract(w_prev, fill_value=0.0).abs().sum()
        turnovers.append(float(dw))
        cost_today = dw * one_way
        w_prev = w

        # daily P&L over (t, nxt]
        hold_days = cal[(cal > t) & (cal <= nxt)]
        first = True
        for d in hold_days:
            r = rets.loc[d].reindex(w.index).fillna(0.0)
            pr = float((w * r).sum()) - (cost_today if first else 0.0)
            daily_port.append((d, pr))
            daily_bench.append((d, float(rets.loc[d].reindex(uni).fillna(0.0).mean())))
            first = False
        # realized beta of this period's positions
        b_window = rets.loc[:t].tail(252)
        if len(b_window) > 60:
            mk = b_window.mean(axis=1)
            port_hist = (b_window.reindex(columns=w.index).fillna(0.0) * w).sum(axis=1)
            if mk.std() > 0:
                betas.append(float(np.cov(port_hist, mk)[0, 1] / np.var(mk)))
        n_reb += 1

    if not daily_port:
        return BTResult(pd.Series(dtype=float), pd.Series(dtype=float))

    def _curve(pairs, e0=1_000_000.0):
        idx = [d for d, _ in pairs]
        rs = np.array([r for _, r in pairs])
        return pd.Series(e0 * np.cumprod(1.0 + rs), index=idx)

    eq = _curve(daily_port)
    bench = _curve(daily_bench)

    de = [(d, v) for d, v in zip(eq.index, eq.values)]
    db = [(d, v) for d, v in zip(bench.index, bench.values)]
    m = compute_metrics(de, [], 0.0, 0.0, float(eq.iloc[0]))
    if m.get("sharpe") is not None:
        m["deflated"] = deflated_sharpe(m["sharpe"], m.get("n_days", 0),
                                        m.get("skew", 0.0), m.get("kurtosis", 3.0), n_trials)
        if mc:
            m["monte_carlo"] = monte_carlo_resample(de)
    bm = compute_metrics(db, [], 0.0, 0.0, float(bench.iloc[0]))

    return BTResult(
        equity=eq, bench=bench, metrics=m, bench_metrics=bm,
        n_rebalances=n_reb, avg_names=float(np.mean(names_count)) if names_count else 0.0,
        realized_beta=float(np.mean(betas)) if betas else None,
        turnover_ann=float(np.mean(turnovers) * 12) if turnovers else None,
    )
