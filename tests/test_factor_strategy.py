"""Pillar-2 factor strategy (daily-panel version): interface conformance,
seeding, breadth safety and ranking.

Verifies the engine-side strategy (1) registers, (2) is disabled by default,
(3) is a pure no-op when UNSEEDED (backtest safety) or below the breadth
floor, (4) emits a balanced, correctly-signed long/short basket from a seeded
daily panel, (5) drops stale panels (names rotated out of the tier view), and
(6) survives a state_dict/load_state round-trip (engine restarts).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from quantsys.config.schema import AppConfig, FactorConfig
from quantsys.strategies.base import REGISTRY
from quantsys.strategies.factor import FactorStrategy

from conftest import make_hist, make_inst, make_state

_STATE_DAY = datetime(2026, 1, 5)   # conftest ts_seq starts sessions here


def _small_cfg(**kw):
    base = dict(enabled=True, lookback_bars=20, skip_bars=2,
                vol_lookback=20, rebalance_bars=5, top_k=5, min_universe=20,
                market_neutral=True, atr_n=5)
    base.update(kw)
    return FactorConfig(**base)


def _seed_rows(closes, end_day: datetime = _STATE_DAY - timedelta(days=1)):
    """Daily candle rows (ts, o, h, l, c, v) ending the day before the state."""
    closes = np.asarray(closes, dtype=float)
    start = end_day - timedelta(days=len(closes) - 1)
    return [
        (start + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e6)
        for i, c in enumerate(closes)
    ]


def _universe(n_up=15, n_down=15, seed_days=30, live_bars=10):
    """n_up names trending up, n_down trending down; returns (state, seeds)."""
    bars_d, insts, seeds = {}, {}, {}
    for i in range(n_up):
        s = f"UP{i}"
        seeds[s] = _seed_rows(np.linspace(100, 200, seed_days) * (1 + 0.001 * i))
        bars_d[s] = make_hist(np.full(live_bars, 200.0))
        insts[s] = make_inst(s, sector="x", adv=1e7)
    for i in range(n_down):
        s = f"DN{i}"
        seeds[s] = _seed_rows(np.linspace(200, 100, seed_days) * (1 + 0.001 * i))
        bars_d[s] = make_hist(np.full(live_bars, 100.0))
        insts[s] = make_inst(s, sector="x", adv=1e7)
    return make_state(bars_d, insts), seeds


def _seeded(strat: FactorStrategy, seeds: dict) -> FactorStrategy:
    for s, rows in seeds.items():
        strat.seed_daily(s, rows)
    return strat


def test_factor_registered():
    assert "factor" in REGISTRY


def test_factor_disabled_by_default():
    assert AppConfig().factor.enabled is False


def test_factor_noop_when_unseeded():
    # without a seeded daily panel (e.g. in the backtest) there is no history
    # -> below breadth -> emits nothing
    st, _seeds = _universe()
    strat = FactorStrategy(_small_cfg())
    assert strat.generate_signals(st) == []


def test_factor_noop_below_breadth():
    st, seeds = _universe()
    strat = FactorStrategy(_small_cfg())
    _seeded(strat, {s: seeds[s] for s in list(seeds)[:3]})   # only 3 names seeded
    assert strat.generate_signals(st) == []


def test_factor_emits_balanced_signed_basket():
    st, seeds = _universe()
    strat = _seeded(FactorStrategy(_small_cfg(top_k=5)), seeds)
    sigs = strat.generate_signals(st)
    longs = [s for s in sigs if s.direction > 0]
    shorts = [s for s in sigs if s.direction < 0]
    assert len(longs) == 5 and len(shorts) == 5          # dollar-neutral basket
    assert all(s.symbol.startswith("UP") for s in longs)
    assert all(s.symbol.startswith("DN") for s in shorts)
    assert all(s.stop_distance > 0 for s in sigs)        # daily-ATR risk stop


def test_factor_long_only_mode():
    st, seeds = _universe()
    strat = _seeded(FactorStrategy(_small_cfg(market_neutral=False, top_k=5)), seeds)
    sigs = strat.generate_signals(st)
    assert len(sigs) == 5 and all(s.direction > 0 for s in sigs)


def test_factor_skips_stale_panels():
    """A name whose panel stopped updating (rotated out of the tier view)
    must not be ranked on frozen prices."""
    st, seeds = _universe(n_up=11, n_down=10)
    strat = FactorStrategy(_small_cfg(min_universe=20, top_k=5))
    stale_sym = "UP10"
    old_end = _STATE_DAY - timedelta(days=60)
    for s, rows in seeds.items():
        if s == stale_sym:
            strat.seed_daily(s, _seed_rows(np.linspace(100, 500, 30), end_day=old_end))
        else:
            strat.seed_daily(s, rows)
    sigs = strat.generate_signals(st)
    assert sigs, "20 fresh names remain -> breadth still met"
    assert all(s.symbol != stale_sym for s in sigs)


def test_factor_state_roundtrip_preserves_basket():
    st, seeds = _universe()
    strat = _seeded(FactorStrategy(_small_cfg(top_k=5)), seeds)
    before = {(s.symbol, s.direction) for s in strat.generate_signals(st)}
    assert before
    clone = FactorStrategy(_small_cfg(top_k=5))
    clone.load_state(strat.state_dict())
    after = {(s.symbol, s.direction) for s in clone.generate_signals(st)}
    assert after == before
