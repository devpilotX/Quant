"""Backtester correctness: accounting identity, no look-ahead, determinism,
cost charging, walk-forward mechanics, deflated-Sharpe math."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from quantsys.backtest import run_backtest, synthetic_bars, walk_forward
from quantsys.backtest.metrics import (
    compute_metrics,
    deflated_sharpe,
    expected_max_sharpe,
    monte_carlo_resample,
)
from quantsys.backtest.simbroker import SimBroker
from quantsys.config import load_config

CFG_PATH = "config/base.yaml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CFG_PATH)


@pytest.fixture(scope="module")
def bars(cfg):
    syms = [u.symbol for u in cfg.universe]
    start = datetime(2026, 1, 1)
    return list(synthetic_bars(syms, start, 800, cfg.engine.decision_bar_minutes, seed=5))


def test_accounting_identity_holds_every_bar(cfg, bars):
    # run_backtest asserts equity == cash + MTM internally; a clean run proves it
    res = run_backtest(cfg, iter(bars), starting_equity=50_000_000, warmup_bars=100)
    assert res.n_decisions == len(bars)
    assert len(res.equity_curve) == len(bars) - 100
    for s in res.equity_curve:
        assert math.isfinite(s.equity)


def test_determinism_same_seed_same_result(cfg, bars):
    a = run_backtest(cfg, iter(bars), 50_000_000, warmup_bars=50)
    b = run_backtest(cfg, iter(bars), 50_000_000, warmup_bars=50)
    assert a.final_equity == b.final_equity
    assert len(a.trades) == len(b.trades)
    assert a.total_fees == b.total_fees


def test_warmup_keeps_book_flat(cfg, bars):
    # during warm-up no fills happen, so no fees accrue before scoring starts
    res = run_backtest(cfg, iter(bars[:60]), 50_000_000, warmup_bars=60)
    assert res.n_fills == 0
    assert res.total_fees == 0.0
    assert res.equity_curve == []


def test_no_lookahead_decision_uses_only_past_bars(cfg, bars):
    """A decision at bar i must be identical whether or not bars after i exist
    in the stream — i.e. decide() never peeks ahead."""
    prefix = bars[:300]
    full = bars[:600]
    r_prefix = run_backtest(cfg, iter(prefix), 50_000_000, warmup_bars=50)
    r_full = run_backtest(cfg, iter(full), 50_000_000, warmup_bars=50)
    # equity path over the shared region must match exactly
    n = len(r_prefix.equity_curve)
    for a, b in zip(r_prefix.equity_curve, r_full.equity_curve[:n]):
        assert a.ts == b.ts
        assert abs(a.equity - b.equity) < 1e-6


def test_costs_are_charged(cfg, bars):
    res = run_backtest(cfg, iter(bars), 50_000_000, warmup_bars=100)
    if res.n_fills > 0:
        assert res.total_fees > 0
        assert res.traded_notional > 0


def test_metrics_shape(cfg, bars):
    res = run_backtest(cfg, iter(bars), 50_000_000, warmup_bars=100)
    m = compute_metrics(res.daily_equity(), res.trades, res.total_fees,
                        res.traded_notional, 50_000_000)
    assert "n_days" in m
    if not m.get("insufficient_data"):
        assert "max_dd" in m and m["max_dd"] >= 0
        assert "cost_drag_bps" in m


def test_walk_forward_runs_and_separates_is_oos(cfg, bars):
    wf = walk_forward(cfg, bars, 50_000_000, n_folds=3, train_frac=0.5)
    assert len(wf.folds) >= 1
    for f in wf.folds:
        assert f.test_start >= f.train_start
        assert f.test_end >= f.test_start


def test_walk_forward_feeds_each_bar_once_no_history_duplication(cfg, bars):
    """Regression: walk_forward streams each bar into the continuous engine
    EXACTLY ONCE. The old code re-fed overlapping train windows into the
    carried BarHistory, bloating it ~2.5x with duplicate bars — which corrupted
    the regime/edge estimators and made per-bar recompute blow up super-linearly
    (walk_forward effectively hung on realistic inputs). Guard the invariant by
    capturing the continuous engine and asserting its carried history is not
    duplicated."""
    import quantsys.backtest.walkforward as wfmod

    created: list = []
    orig = wfmod.DecisionEngine

    def _spy(*a, **k):
        e = orig(*a, **k)
        created.append(e)
        return e

    wfmod.DecisionEngine = _spy
    try:
        wf = walk_forward(cfg, bars, 50_000_000, n_folds=4, train_frac=0.5)
    finally:
        wfmod.DecisionEngine = orig

    assert wf.oos_curve, "walk-forward produced no OOS curve"
    continuous = created[0]  # built before the fold loop; shadows come after
    hist = getattr(continuous, "_bt_histories", {})
    assert hist, "continuous engine never accumulated bar history"
    sym = next(iter(hist))
    # no duplication: carried history must not exceed the input length
    # (the bug made this ~2.5x len(bars)).
    assert len(hist[sym]) <= len(bars)


def test_deflated_sharpe_deflates_with_more_trials():
    # same observed SR looks worse the more variants you tried
    p1 = deflated_sharpe(1.5, n_obs=252, n_trials=1)
    p50 = deflated_sharpe(1.5, n_obs=252, n_trials=50)
    assert p1 is not None and p50 is not None
    assert p50 < p1
    assert expected_max_sharpe(50, 252) > expected_max_sharpe(2, 252)


def test_monte_carlo_resample_runs(cfg, bars):
    res = run_backtest(cfg, iter(bars), 50_000_000, warmup_bars=100)
    mc = monte_carlo_resample(res.daily_equity(), n_paths=200)
    if not mc.get("insufficient_data"):
        assert "sharpe_p05" in mc and "max_dd_p95" in mc


def test_simbroker_realizes_pnl_on_close(cfg):
    from quantsys.core.types import Decision, ExecutionStyle, OrderIntent, RegimeState, Urgency

    sym = cfg.universe[0].symbol
    from quantsys.engine.decision import DecisionEngine
    eng = DecisionEngine(cfg)
    brk = SimBroker(1_000_000, eng.instruments, eng.cost_model)
    inst = eng.instruments[sym]

    def mk(qty):
        return Decision(
            ts=datetime(2026, 1, 1, 9, 15), equity=1_000_000, tier_name="T1",
            regime=RegimeState("calm_range", {}, 1.0, {}, "none"),
            signals=(), kelly={}, vol_scaler=1.0, risk_frac_eff=0.0,
            targets=(), orders=(OrderIntent(sym, qty, ExecutionStyle.MARKET_SINGLE,
                                            Urgency.NORMAL, "trend", "test"),),
        )
    brk.execute(mk(10), {sym: 100.0}, datetime(2026, 1, 1, 9, 15))
    assert brk.positions[sym].qty == 10
    brk.execute(mk(-10), {sym: 110.0}, datetime(2026, 1, 1, 9, 20))
    assert sym not in brk.positions
    assert len(brk.trades) == 1
    t = brk.trades[0]
    assert t.pnl == pytest.approx(10 * (110 - 100) * inst.point_value)
    assert t.fees > 0


def test_monte_carlo_flat_equity_does_not_crash():
    """Short / flat-equity windows yield zero positive-variance bootstrap paths;
    monte_carlo_resample must not crash on np.percentile([]) (the bug that
    blocked persisting a bounded real backtest)."""
    from quantsys.backtest.metrics import monte_carlo_resample

    base = datetime(2024, 1, 1)
    flat = [(base + timedelta(days=i), 1_000_000.0) for i in range(40)]
    mc = monte_carlo_resample(flat, n_paths=100)
    assert mc["insufficient_data"] is True
    assert mc["sharpe_p50"] is None
    assert mc["p_sharpe_negative"] == 1.0  # conservative: treated as worst
