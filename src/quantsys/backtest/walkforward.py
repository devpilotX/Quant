"""Walk-forward evaluation.

This engine is online/adaptive (EWMA edge stats, regime HMM, tier ladder) — it
has no separate "fit" step to freeze. So walk-forward here measures the honest
thing: train windows let the engine's estimators converge with NO execution
(in-sample warm-up), then the immediately-following test window executes and is
scored OUT-OF-SAMPLE on data the estimators had not yet seen when each decision
was made. Engine state carries forward across the rolling windows exactly as it
would live — there is no re-initialisation that would leak future info backward.

Returns per-fold OOS metrics plus the concatenated OOS curve, and the
IS-vs-OOS comparison the dashboard renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from quantsys.backtest.loop import BacktestResult, EquitySample, run_backtest
from quantsys.backtest.metrics import compute_metrics
from quantsys.backtest.simbroker import SimBroker
from quantsys.config.schema import AppConfig
from quantsys.core.types import Bar
from quantsys.engine.decision import DecisionEngine


@dataclass
class Fold:
    index: int
    train_start: datetime
    test_start: datetime
    test_end: datetime
    is_metrics: dict
    oos_metrics: dict


@dataclass
class WalkForwardResult:
    folds: list[Fold] = field(default_factory=list)
    oos_curve: list[EquitySample] = field(default_factory=list)
    oos_trades: list = field(default_factory=list)
    combined_oos: dict = field(default_factory=dict)
    pooled_is: dict = field(default_factory=dict)


def walk_forward(
    cfg: AppConfig,
    all_bars: list[tuple[datetime, dict[str, Bar]]],
    starting_equity: float,
    n_folds: int = 4,
    train_frac: float = 0.5,
) -> WalkForwardResult:
    """Rolling-origin folds over a materialised bar list.

    Each fold: [train window | test window]. Engine + broker state carry across
    folds (continuous, like live). Within a fold the train portion is warm-up
    (no fills); the test portion executes and is scored OOS.
    """
    n = len(all_bars)
    if n < n_folds * 20:
        raise ValueError("not enough bars for the requested folds")

    test_span = int(n * (1 - train_frac) / n_folds)
    train_span = int(n * train_frac)
    res = WalkForwardResult()

    # one continuous engine/broker across the whole horizon
    engine = DecisionEngine(cfg)
    broker = SimBroker(starting_equity, engine.instruments, engine.cost_model)

    is_returns_pool: list[tuple[datetime, float]] = []
    cursor = 0
    for fold_i in range(n_folds):
        train_lo = cursor
        test_lo = min(train_lo + train_span, n - test_span)
        test_hi = min(test_lo + test_span, n)
        if test_lo >= test_hi:
            break

        window = all_bars[train_lo:test_hi]
        score_from = all_bars[test_lo][0]

        eq_before = broker.equity(_last_prices(all_bars, test_lo))
        fold_res = run_backtest(
            cfg, window, starting_equity=eq_before,
            engine=engine, broker=broker, score_from=score_from,
        )
        oos = compute_metrics(
            fold_res.daily_equity(), fold_res.trades, fold_res.total_fees,
            fold_res.traded_notional, eq_before,
        )
        # in-sample proxy: score the train window's decisions on a SHADOW run
        # (fresh broker, same converged-so-far engine snapshot is impractical;
        # instead we report the realised train-window equity path of a shadow
        # broker executing the train bars) — gives an honest IS reference.
        is_metrics = _shadow_is(cfg, all_bars[train_lo:test_lo], eq_before)

        res.folds.append(Fold(
            index=fold_i,
            train_start=all_bars[train_lo][0],
            test_start=score_from,
            test_end=all_bars[test_hi - 1][0],
            is_metrics=is_metrics,
            oos_metrics=oos,
        ))
        res.oos_curve.extend(fold_res.equity_curve)
        res.oos_trades.extend(fold_res.trades)
        for ts, v in is_metrics.get("_daily", []):
            is_returns_pool.append((ts, v))

        cursor += test_span

    if res.oos_curve:
        daily: dict = {}
        for s in res.oos_curve:
            daily[s.ts.date()] = s.equity
        curve = [(datetime(d.year, d.month, d.day), v) for d, v in sorted(daily.items())]
        total_fees = sum(t.fees for t in res.oos_trades)
        res.combined_oos = compute_metrics(
            curve, res.oos_trades, total_fees,
            sum(abs(t.qty) * t.exit_price for t in res.oos_trades),
            res.oos_curve[0].equity,
        )
    if is_returns_pool:
        res.pooled_is = compute_metrics(is_returns_pool, [], 0.0, 0.0,
                                        is_returns_pool[0][1])
    return res


def _shadow_is(cfg: AppConfig, train_bars: list, starting_equity: float) -> dict:
    """In-sample reference: a fresh engine executing the train window. This is
    optimistic by construction (the estimators see the same data they trade),
    which is exactly the point — IS should look better than OOS; the gap is the
    degradation we report."""
    if len(train_bars) < 20:
        return {"insufficient_data": True}
    eng = DecisionEngine(cfg)
    brk = SimBroker(starting_equity, eng.instruments, eng.cost_model)
    r = run_backtest(cfg, train_bars, starting_equity, engine=eng, broker=brk)
    m = compute_metrics(r.daily_equity(), r.trades, r.total_fees,
                        r.traded_notional, starting_equity)
    m["_daily"] = r.daily_equity()
    return m


def _last_prices(all_bars: list, upto: int) -> dict[str, float]:
    px: dict[str, float] = {}
    for _, batch in all_bars[:upto]:
        for sym, bar in batch.items():
            px[sym] = bar.close
    return px
