"""Forward Study 2 upgrades: time-based bar flush, equity-scaled dust floor,
regime-conditional Kelly tilt, and the three new sleeves (downshock, reversal,
tom) — registration, frozen-rule behaviour, breadth/unseeded safety, and state
round-trips.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from quantsys.config.schema import (
    AppConfig,
    DownShockConfig,
    ReversalConfig,
    TomConfig,
)
from quantsys.core.types import InstrumentKind, RegimeState
from quantsys.execution.marketdata import BarAggregator
from quantsys.portfolio.allocation import KellyAllocator
from quantsys.strategies.base import REGISTRY, OnlineEdgeStats
from quantsys.strategies.downshock import DownShockStrategy
from quantsys.strategies.reversal import ReversalStrategy
from quantsys.strategies.tom import TomStrategy, tom_window_active

from conftest import make_hist, make_inst, make_state


# ------------------------------------------------------- aggregator flush
def test_flush_older_completes_elapsed_bars_without_next_tick():
    emitted = []
    agg = BarAggregator(15, lambda s, b: emitted.append((s, b)))
    t0 = datetime(2026, 7, 2, 9, 16)
    agg.on_tick("X", 100.0, t0)
    agg.on_tick("X", 101.0, t0 + timedelta(minutes=5))
    assert agg.flush_older(datetime(2026, 7, 2, 9, 29, 59)) == 0  # window open
    assert emitted == []
    assert agg.flush_older(datetime(2026, 7, 2, 9, 30, 1)) == 1   # elapsed
    sym, bar = emitted[0]
    assert sym == "X" and bar.ts == datetime(2026, 7, 2, 9, 15)
    assert bar.close == 101.0
    # nothing left building; a later flush is a no-op
    assert agg.flush_older(datetime(2026, 7, 2, 9, 45, 1)) == 0


def test_flush_older_emits_the_session_close_bar():
    """The 15:15 bar never sees a tick cross 15:30 — only a time-based flush
    can complete it (the engine previously never decided on the close bar)."""
    emitted = []
    agg = BarAggregator(15, lambda s, b: emitted.append(b))
    agg.on_tick("X", 100.0, datetime(2026, 7, 2, 15, 16))
    agg.on_tick("X", 99.0, datetime(2026, 7, 2, 15, 29))
    assert agg.flush_older(datetime(2026, 7, 2, 15, 30, 1)) == 1
    assert emitted[0].ts == datetime(2026, 7, 2, 15, 15)
    assert emitted[0].close == 99.0


# ------------------------------------------------- equity-scaled dust floor
def test_effective_min_notional_scales_with_equity():
    from quantsys.config.schema import SizingConfig
    from quantsys.costs import CostModel
    from quantsys.portfolio.sizing import SizingEngine

    sizer = SizingEngine(SizingConfig(), CostModel(AppConfig().costs),
                         5_000.0, 0.0001)
    assert sizer.effective_min_notional(1e5) == 5_000.0        # flat floor
    assert sizer.effective_min_notional(15e7) == 15_000.0      # 1bp of 15cr
    zero = SizingEngine(SizingConfig(), CostModel(AppConfig().costs), 5_000.0)
    assert zero.effective_min_notional(15e7) == 5_000.0        # frac off => old


# --------------------------------------------------- weighted edge stats
def test_edge_stats_weighted_update():
    full = OnlineEdgeStats(halflife=100, prior_obs=0.0)
    soft = OnlineEdgeStats(halflife=100, prior_obs=0.0)
    for _ in range(10):
        full.update(0.01)
        soft.update(0.01, weight=0.5)
    assert np.isclose(soft.n_eff, full.n_eff * 0.5)
    assert np.isclose(soft.raw_mean, full.raw_mean)   # mean invariant to weight


# ------------------------------------------------- regime-conditional tilt
def _regime(label="calm_trend", probs=None):
    return RegimeState(label, probs or {label: 1.0}, 1.0, {}, "test")


def test_allocator_applies_tilt_and_audits():
    cfg = AppConfig().kelly
    alloc = KellyAllocator(cfg)
    stats = {"trend": OnlineEdgeStats(cfg.edge_halflife_bars, cfg.prior_obs)}
    audits = []
    base = alloc.allocate(stats, _regime(), ["trend"], audits)
    tilted = alloc.allocate(stats, _regime(), ["trend"], audits,
                            tilts={"trend": 1.5})
    assert np.isclose(tilted["trend"], min(base["trend"] * 1.5, cfg.f_cap))
    assert any(a.rule == "regime_tilt" for a in audits)


def _tilt_engine(beta: float):
    cfg = AppConfig.model_validate({
        "kelly": {"regime_tilt_beta": beta},
        "universe": [{"symbol": "A"}, {"symbol": "B"}],
    })
    from quantsys.engine.decision import DecisionEngine

    return DecisionEngine(cfg)


def test_engine_regime_buckets_learn_and_tilt():
    eng = _tilt_engine(0.4)
    eng._unit_nets = {"trend": {"A": 10.0}}
    eng._last_closes = {"A": 100.0}
    eng._last_regime_probs = {"calm_trend": 1.0}
    bars = {"A": make_hist(np.full(30, 101.0))}
    insts = {"A": make_inst("A")}
    st = make_state(bars, insts)
    for _ in range(30):   # repeated positive unit P&L under calm_trend
        eng.post_bar(st)
    b = eng.regime_stats["trend"]["calm_trend"]
    assert b.n_eff > 5 and b.mean > 0
    tilts = eng._regime_tilts(_regime("calm_trend"), ["trend", "meanrev"])
    assert tilts["trend"] > 1.0                       # earned upweight
    assert np.isclose(tilts["meanrev"], 1.0)          # no evidence -> neutral
    cfgk = eng.cfg.kelly
    assert tilts["trend"] <= cfgk.regime_tilt_max

    # persistence: buckets and tilt survive a state round-trip
    clone = _tilt_engine(0.4)
    clone.load_state(eng.state_dict())
    assert np.isclose(
        clone._regime_tilts(_regime("calm_trend"), ["trend"])["trend"],
        tilts["trend"])


def test_tilt_disabled_is_noop():
    eng = _tilt_engine(0.0)
    assert eng._regime_tilts(_regime(), ["trend"]) is None


# ------------------------------------------------------------- new sleeves
def test_new_sleeves_registered_and_disabled_by_default():
    for name in ("downshock", "reversal", "tom"):
        assert name in REGISTRY
        assert getattr(AppConfig(), name).enabled is False


def _daily_state(day: datetime, closes: dict[str, float],
                 volumes: dict[str, float], insts: dict):
    bars = {}
    for sym, c in closes.items():
        bars[sym] = make_hist([c], volume=volumes.get(sym, 1e6),
                              times=[day.replace(hour=9, minute=15)])
    return make_state(bars, insts, ts=day.replace(hour=10, minute=0))


def _ds_cfg(**kw):
    base = dict(enabled=True, z_threshold=3.5, vol_window=30, volume_window=10,
                volume_ratio_min=2.0, decluster_days=30, hold_days=3,
                max_concurrent=5, atr_n=5, min_history_days=40)
    base.update(kw)
    return DownShockConfig(**base)


def _noisy_seed(days: int, end_day: datetime, base_px=100.0, vol=1e6):
    """Daily rows with ±0.3% alternating noise (sigma ~ 0.3%)."""
    rows = []
    start = end_day - timedelta(days=days - 1)
    for i in range(days):
        c = base_px * (1 + 0.003 * (1 if i % 2 == 0 else -1))
        rows.append((start + timedelta(days=i), c, c * 1.005, c * 0.995, c, vol))
    return rows


def test_downshock_frozen_rule_end_to_end():
    day0 = datetime(2026, 7, 6)
    insts = {"SHK": make_inst("SHK"), "OK": make_inst("OK")}
    strat = DownShockStrategy(_ds_cfg())
    for sym in insts:
        strat.seed_daily(sym, _noisy_seed(60, day0 - timedelta(days=1)))

    # unseeded/backtest safety on a fresh instance
    assert DownShockStrategy(_ds_cfg()).generate_signals(
        _daily_state(day0, {"SHK": 100.0}, {}, insts)) == []

    # day 0: SHK crashes -8% on 3x volume; no signal yet (event day itself)
    st0 = _daily_state(day0, {"SHK": 92.0, "OK": 100.2},
                       {"SHK": 3e6, "OK": 1e6}, insts)
    assert strat.generate_signals(st0) == []

    # day 1: the roll finalizes the shock day -> SHORT signal (enter at +1)
    st1 = _daily_state(day0 + timedelta(days=1), {"SHK": 91.5, "OK": 100.0},
                       {"SHK": 1e6, "OK": 1e6}, insts)
    sigs = strat.generate_signals(st1)
    assert [s.symbol for s in sigs] == ["SHK"]
    assert sigs[0].direction == -1.0 and sigs[0].stop_distance > 0

    # holds for hold_days sessions, then exits (stops emitting)
    for k in range(2, 2 + 3):
        stk = _daily_state(day0 + timedelta(days=k), {"SHK": 91.0, "OK": 100.0},
                           {"SHK": 1e6, "OK": 1e6}, insts)
        sigs = strat.generate_signals(stk)
        held = {s.symbol for s in sigs}
        if k < 1 + 3:
            assert "SHK" in held
        else:
            assert "SHK" not in held    # hold expired

    # state round-trip preserves the active book
    clone = DownShockStrategy(_ds_cfg())
    clone.load_state(strat.state_dict())
    assert clone._active == strat._active and clone._last_event == strat._last_event


def test_downshock_decluster_and_concurrency_cap():
    day0 = datetime(2026, 7, 6)
    syms = [f"S{i}" for i in range(7)]
    insts = {s: make_inst(s) for s in syms}
    strat = DownShockStrategy(_ds_cfg(max_concurrent=5))
    for s in syms:
        strat.seed_daily(s, _noisy_seed(60, day0 - timedelta(days=1)))
    # all 7 crash with slightly different magnitudes on 3x volume
    st0 = _daily_state(day0, {s: 92.0 - i * 0.1 for i, s in enumerate(syms)},
                       {s: 3e6 for s in syms}, insts)
    strat.generate_signals(st0)
    st1 = _daily_state(day0 + timedelta(days=1), {s: 91.0 for s in syms},
                       {s: 1e6 for s in syms}, insts)
    sigs = strat.generate_signals(st1)
    assert len(sigs) == 5                              # concurrency cap binds
    # deepest z first: S6 (largest drop) must be in, S0 (smallest) out
    got = {s.symbol for s in sigs}
    assert "S6" in got and "S0" not in got

    # de-cluster: another crash in the same names within 30d is ignored
    st2 = _daily_state(day0 + timedelta(days=2),
                       {s: 84.0 for s in syms}, {s: 3e6 for s in syms}, insts)
    strat.generate_signals(st2)
    st3 = _daily_state(day0 + timedelta(days=3), {s: 84.0 for s in syms},
                       {s: 1e6 for s in syms}, insts)
    sigs3 = strat.generate_signals(st3)
    assert {s.symbol for s in sigs3} <= got            # no NEW names entered


def _rev_cfg(**kw):
    base = dict(enabled=True, lookback_days=5, rebalance_days=5, top_k=8,
                min_universe=20, market_neutral=True, atr_n=5)
    base.update(kw)
    return ReversalConfig(**base)


def _rev_universe(n_win=15, n_los=15, days=20):
    end = datetime(2026, 7, 5)
    insts, seeds, closes = {}, {}, {}
    for i in range(n_win):
        s = f"WIN{i}"
        insts[s] = make_inst(s)
        px = np.concatenate([np.full(days - 5, 100.0),
                             np.linspace(101, 110 + i, 5)])   # recent winners
        seeds[s] = [(end - timedelta(days=days - 1 - j), p, p * 1.01, p * 0.99, p, 1e6)
                    for j, p in enumerate(px)]
        closes[s] = float(px[-1])
    for i in range(n_los):
        s = f"LOS{i}"
        insts[s] = make_inst(s)
        px = np.concatenate([np.full(days - 5, 100.0),
                             np.linspace(99, 90 - i, 5)])     # recent losers
        seeds[s] = [(end - timedelta(days=days - 1 - j), p, p * 1.01, p * 0.99, p, 1e6)
                    for j, p in enumerate(px)]
        closes[s] = float(px[-1])
    return insts, seeds, closes, end + timedelta(days=1)


def test_reversal_longs_losers_shorts_winners():
    insts, seeds, closes, day = _rev_universe()
    strat = ReversalStrategy(_rev_cfg())
    for s, rows in seeds.items():
        strat.seed_daily(s, rows)
    st = _daily_state(day, closes, {}, insts)
    sigs = strat.generate_signals(st)
    longs = {s.symbol for s in sigs if s.direction > 0}
    shorts = {s.symbol for s in sigs if s.direction < 0}
    assert len(longs) == 8 and len(shorts) == 8
    assert all(s.startswith("LOS") for s in longs)     # losers bought
    assert all(s.startswith("WIN") for s in shorts)    # winners shorted
    assert all(s.stop_distance > 0 for s in sigs)

    clone = ReversalStrategy(_rev_cfg())
    clone.load_state(strat.state_dict())
    assert {(s.symbol, s.direction) for s in clone.generate_signals(st)} == \
           {(s.symbol, s.direction) for s in sigs}


def test_reversal_breadth_gate_and_unseeded_noop():
    insts, seeds, closes, day = _rev_universe(n_win=5, n_los=5)
    strat = ReversalStrategy(_rev_cfg(min_universe=20))
    for s, rows in seeds.items():
        strat.seed_daily(s, rows)
    assert strat.generate_signals(_daily_state(day, closes, {}, insts)) == []
    assert ReversalStrategy(_rev_cfg()).generate_signals(
        _daily_state(day, closes, {}, insts)) == []


def test_tom_window_function():
    # July 2026: 31st is a Friday -> last 2 weekdays are Thu 30 + Fri 31
    assert tom_window_active(date(2026, 7, 30), 2, 3)
    assert tom_window_active(date(2026, 7, 31), 2, 3)
    assert not tom_window_active(date(2026, 7, 29), 2, 3)
    assert not tom_window_active(date(2026, 7, 15), 2, 3)
    # August 2026: Sat 1 / Sun 2 excluded; Mon 3, Tue 4, Wed 5 are +1..+3
    assert not tom_window_active(date(2026, 8, 1), 2, 3)
    assert tom_window_active(date(2026, 8, 3), 2, 3)
    assert tom_window_active(date(2026, 8, 5), 2, 3)
    assert not tom_window_active(date(2026, 8, 6), 2, 3)


def test_tom_emits_long_index_future_inside_window_only():
    cfg = TomConfig(enabled=True, symbols=["NIFTY-FUT"], timeframe_bars=1,
                    atr_n=5)
    strat = TomStrategy(cfg)
    hist = make_hist(np.linspace(24000, 24200, 30))
    insts = {"NIFTY-FUT": make_inst("NIFTY-FUT", kind=InstrumentKind.FUTURE,
                                    lot_size=65)}
    inside = make_state({"NIFTY-FUT": hist}, insts,
                        ts=datetime(2026, 7, 30, 10, 0))
    sigs = strat.generate_signals(inside)
    assert len(sigs) == 1 and sigs[0].direction == 1.0 and sigs[0].stop_distance > 0
    outside = make_state({"NIFTY-FUT": hist}, insts,
                         ts=datetime(2026, 7, 15, 10, 0))
    assert strat.generate_signals(outside) == []


# ------------------------------------------ ensemble wiring sanity (T6 slots)
def test_study2_config_activates_six_sleeves():
    """Deployed ensemble: reversal is implemented but OFF — its pre-deployment
    sanity check was net-negative across the whole grid (rejected arm)."""
    from pathlib import Path

    from quantsys.config import load_config
    from quantsys.engine.decision import DecisionEngine

    cfg = load_config(str(Path(__file__).resolve().parents[1] / "config" / "base.yaml"))
    eng = DecisionEngine(cfg)
    names = [s.name for s in eng.strategies]
    assert names == ["trend", "meanrev", "expiry", "factor",
                     "downshock", "tom"]                 # priority order
    assert cfg.reversal.enabled is False                 # rejected arm stays off
    t6 = [t for t in cfg.tiers.ladder if t.name == "T6"][0]
    assert t6.max_strategies >= len(names)               # all run at T6
    assert cfg.kelly.regime_tilt_beta > 0
    assert cfg.engine.min_order_frac > 0
    for lbl in ("calm_trend", "calm_range", "turbulent"):
        w = cfg.regime.labels[lbl].strategy_weights
        assert set(w) >= {"trend", "meanrev", "factor", "expiry",
                          "downshock", "reversal", "tom"}
