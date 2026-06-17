import numpy as np
import pytest

from quantsys.config.schema import KellyConfig, VolTargetConfig
from quantsys.core.types import RegimeState, bars_per_year
from quantsys.portfolio.allocation import KellyAllocator, VolTargeter
from quantsys.portfolio.book import Component, TargetBook
from quantsys.core.types import LegSpec, Signal
from quantsys.strategies.base import OnlineEdgeStats
from tests.conftest import make_inst

NEUTRAL = RegimeState("calm_range", {"calm_range": 1.0}, 1.0, {}, "test")


def _stats(mean_per_bar: float, sd_per_bar: float, n: int, cfg: KellyConfig) -> OnlineEdgeStats:
    st = OnlineEdgeStats(cfg.edge_halflife_bars, cfg.prior_obs, cfg.var_floor)
    rng = np.random.default_rng(0)
    for r in rng.normal(mean_per_bar, sd_per_bar, n):
        st.update(float(r))
    return st


def test_edge_stats_shrink_young_means_to_zero():
    cfg = KellyConfig(prior_obs=500.0)
    st = OnlineEdgeStats(1000.0, cfg.prior_obs)
    for _ in range(10):
        st.update(0.01)  # 10 great bars
    assert st.raw_mean == pytest.approx(0.01)
    assert st.mean < 0.0002  # shrunk hard: 10 obs vs 500 prior


def test_kelly_formula_clip_and_caps():
    cfg = KellyConfig(kelly_fraction=0.3, f_cap=0.35, gross_f_cap=0.5,
                      ramp_obs=10.0, prior_obs=10.0, edge_halflife_bars=5000.0)
    audits = []
    alloc = KellyAllocator(cfg)
    good = _stats(2e-4, 2e-3, 4000, cfg)   # strong edge -> hits f_cap
    bad = _stats(-3e-4, 2e-3, 4000, cfg)   # established negative edge -> 0
    f = alloc.allocate({"a": good, "b": bad}, NEUTRAL, ["a", "b"], audits)
    assert f["a"] == pytest.approx(min(0.35, 0.5), rel=1e-6) or f["a"] <= 0.35
    assert f["a"] > 0.2
    assert f["b"] == 0.0


def test_kelly_incubation_floor_and_negative_withdrawal():
    cfg = KellyConfig(ramp_floor=0.08, ramp_obs=1000.0, prior_obs=1000.0)
    alloc = KellyAllocator(cfg)
    young = OnlineEdgeStats(cfg.edge_halflife_bars, cfg.prior_obs)
    for _ in range(5):
        young.update(0.0)
    f = alloc.allocate({"s": young}, NEUTRAL, ["s"], [])
    assert f["s"] == pytest.approx(0.08)  # incubation
    losing_young = OnlineEdgeStats(50.0, 10.0)  # fast stats, weak prior
    for _ in range(60):
        losing_young.update(-0.01)
    f2 = alloc.allocate({"s": losing_young}, NEUTRAL, ["s"], [])
    assert f2["s"] == 0.0  # clearly negative evidence withdraws the floor


def test_explore_floor_forces_allocation_despite_negative_edge():
    # paper-only exploration: explore_floor forces a minimum allocation that is
    # NOT withdrawn by clearly-negative evidence. Default 0.0 keeps live and the
    # backtest gate fully honest (asserted in the negative-withdrawal test).
    losing = OnlineEdgeStats(50.0, 10.0)
    for _ in range(60):
        losing.update(-0.01)  # clearly negative edge -> normally withdrawn to 0
    cfg = KellyConfig(ramp_obs=10.0, prior_obs=10.0, edge_halflife_bars=50.0,
                      explore_floor=0.05)
    f = KellyAllocator(cfg).allocate({"s": losing}, NEUTRAL, ["s"], [])
    assert f["s"] == pytest.approx(0.05)  # forced despite negative edge

    cfg_off = KellyConfig(ramp_obs=10.0, prior_obs=10.0, edge_halflife_bars=50.0)
    f_off = KellyAllocator(cfg_off).allocate({"s": losing}, NEUTRAL, ["s"], [])
    assert f_off["s"] == 0.0  # default: still honestly withdrawn


def test_kelly_gross_cap_scales_proportionally():
    cfg = KellyConfig(gross_f_cap=0.4, f_cap=0.35, ramp_obs=1.0, prior_obs=1.0,
                      edge_halflife_bars=5000.0)
    alloc = KellyAllocator(cfg)
    s1 = _stats(3e-4, 2e-3, 4000, cfg)
    s2 = _stats(3e-4, 2e-3, 4000, cfg)
    f = alloc.allocate({"a": s1, "b": s2}, NEUTRAL, ["a", "b"], [])
    assert sum(f.values()) == pytest.approx(0.4, rel=1e-9)
    assert f["a"] == pytest.approx(f["b"])


def test_regime_weights_multiply():
    cfg = KellyConfig(ramp_obs=1.0, prior_obs=1.0, edge_halflife_bars=5000.0)
    alloc = KellyAllocator(cfg)
    st = _stats(2e-4, 2e-3, 4000, cfg)
    reg = RegimeState("turbulent", {"turbulent": 1.0}, 0.35, {"a": 0.5}, "test")
    f_neutral = alloc.allocate({"a": st}, NEUTRAL, ["a"], [])
    f_reg = alloc.allocate({"a": st}, reg, ["a"], [])
    assert f_reg["a"] == pytest.approx(0.5 * f_neutral["a"], rel=1e-9)


def _book_one(symbol: str, qty: float, price: float) -> TargetBook:
    book = TargetBook()
    sig = Signal("s", symbol, 1.0, 1.0, legs=(LegSpec(symbol, 1.0),))
    book.add_group([Component(symbol, qty, "s", "g1", 1.0, price)], sig)
    return book


def test_vol_targeter_exact_math_and_clipping():
    cfg = VolTargetConfig(annual_vol_target=0.13, scaler_min=0.25, scaler_max=2.0)
    vt = VolTargeter(cfg, bar_minutes=5)
    E = 1_000_000.0
    sigma_bar_1d = 0.01 / np.sqrt(bars_per_year(5))  # 1% annual book vol if fully invested
    cov = np.array([[sigma_bar_1d**2]])
    book = _book_one("X", 10_000.0, 100.0)  # notional = E -> w = 1
    s = vt.scaler(book, {"X": 100.0}, {"X": make_inst("X")}, ["X"], cov, E, [])
    assert s == 2.0  # 0.13/0.01 = 13, clipped to scaler_max
    cov_hot = np.array([[(1.0 / np.sqrt(bars_per_year(5))) ** 2]])  # 100% annual vol
    s2 = vt.scaler(book, {"X": 100.0}, {"X": make_inst("X")}, ["X"], cov_hot, E, [])
    assert s2 == pytest.approx(0.25)  # 0.13 wanted, clipped at min
    cov_mid = np.array([[(0.26 / np.sqrt(bars_per_year(5))) ** 2]])
    s3 = vt.scaler(book, {"X": 100.0}, {"X": make_inst("X")}, ["X"], cov_mid, E, [])
    assert s3 == pytest.approx(0.5, rel=1e-6)  # exact: 0.13/0.26


def test_vol_targeter_empty_book_neutral():
    vt = VolTargeter(VolTargetConfig(), 5)
    assert vt.scaler(TargetBook(), {}, {}, [], None, 1e6, []) == 1.0
