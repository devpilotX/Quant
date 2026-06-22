"""Pillar-2 factor strategy: interface conformance + breadth safety.

Verifies the engine-side strategy (1) registers, (2) is disabled by default,
(3) is a pure no-op below the breadth floor (so it is safe in the live
narrow/intraday book), and (4) emits a balanced, correctly-signed long/short
basket on a broad synthetic universe.
"""

from __future__ import annotations

import numpy as np

from quantsys.config.schema import AppConfig, FactorConfig
from quantsys.strategies.base import REGISTRY
from quantsys.strategies.factor import FactorStrategy

from conftest import make_hist, make_inst, make_state


def _small_cfg(**kw):
    base = dict(enabled=True, timeframe_bars=1, lookback_bars=20, skip_bars=2,
                vol_lookback=20, rebalance_bars=5, top_k=5, min_universe=20,
                market_neutral=True, atr_n=5)
    base.update(kw)
    return FactorConfig(**base)


def _ramp_universe(n_up=15, n_down=15, bars=45):
    """n_up names trending up, n_down trending down, ~equal (low) volatility."""
    bars_d, insts = {}, {}
    for i in range(n_up):
        c = np.linspace(100, 200, bars) * (1 + 0.001 * i)
        s = f"UP{i}"
        bars_d[s] = make_hist(c)
        insts[s] = make_inst(s, sector="x", adv=1e7)
    for i in range(n_down):
        c = np.linspace(200, 100, bars) * (1 + 0.001 * i)
        s = f"DN{i}"
        bars_d[s] = make_hist(c)
        insts[s] = make_inst(s, sector="x", adv=1e7)
    return bars_d, insts


def test_factor_registered():
    assert "factor" in REGISTRY


def test_factor_disabled_by_default():
    assert AppConfig().factor.enabled is False


def test_factor_noop_on_narrow_universe():
    # only 3 names: far below min_universe -> emits nothing (live-narrow safe)
    bars_d, insts = _ramp_universe(n_up=2, n_down=1, bars=45)
    st = make_state(bars_d, insts)
    strat = FactorStrategy(_small_cfg())
    assert strat.generate_signals(st) == []


def test_factor_emits_balanced_signed_basket():
    bars_d, insts = _ramp_universe(15, 15, bars=45)
    st = make_state(bars_d, insts)
    strat = FactorStrategy(_small_cfg(top_k=5))
    sigs = strat.generate_signals(st)
    longs = [s for s in sigs if s.direction > 0]
    shorts = [s for s in sigs if s.direction < 0]
    assert len(longs) == 5 and len(shorts) == 5          # dollar-neutral basket
    # momentum: up-trenders are longed, down-trenders shorted
    assert all(s.symbol.startswith("UP") for s in longs)
    assert all(s.symbol.startswith("DN") for s in shorts)
    assert all(s.stop_distance > 0 for s in sigs)        # sizable risk stop


def test_factor_long_only_mode():
    bars_d, insts = _ramp_universe(15, 15, bars=45)
    st = make_state(bars_d, insts)
    strat = FactorStrategy(_small_cfg(market_neutral=False, top_k=5))
    sigs = strat.generate_signals(st)
    assert len(sigs) == 5 and all(s.direction > 0 for s in sigs)
