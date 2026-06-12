"""Options sleeve: Black-Scholes correctness, IV round-trip, defined-risk
spread construction, and end-to-end sizing through the REAL SizingEngine
(legs come out equal-contract / opposite-sign — the defined-risk invariant)."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from quantsys.config import load_config
from quantsys.core.market_state import MarketState
from quantsys.core.types import Bar
from quantsys.data.history import BarHistory
from quantsys.options.blackscholes import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_vega,
    implied_vol,
)
from quantsys.options.chain import SyntheticChainProvider
from quantsys.options.universe import OptionUniverseManager
from quantsys.strategies.voloptions import VolOptionsStrategy


# ----------------------------------------------------------- Black-Scholes
def test_put_call_parity():
    S, K, T, r, sig = 100, 100, 0.5, 0.06, 0.2
    c = bs_price(S, K, T, r, sig, is_call=True)
    p = bs_price(S, K, T, r, sig, is_call=False)
    # c - p = S - K e^{-rT}
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-6)


def test_atm_call_delta_near_half():
    d = bs_delta(100, 100, 0.5, 0.06, 0.2, is_call=True)
    assert 0.5 < d < 0.65  # slightly >0.5 due to drift


def test_intrinsic_at_expiry():
    assert bs_price(120, 100, 0, 0.06, 0.2, is_call=True) == 20
    assert bs_price(80, 100, 0, 0.06, 0.2, is_call=False) == 20
    assert bs_price(80, 100, 0, 0.06, 0.2, is_call=True) == 0


def test_iv_round_trips():
    S, K, T, r, true_sig = 100, 105, 0.25, 0.06, 0.28
    px = bs_price(S, K, T, r, true_sig, is_call=True)
    iv = implied_vol(px, S, K, T, r, is_call=True)
    assert iv == pytest.approx(true_sig, abs=1e-4)


def test_iv_rejects_below_intrinsic():
    # price below intrinsic has no valid IV
    assert implied_vol(0.01, 120, 100, 0.25, 0.06, is_call=True) is None


def test_greeks_signs():
    assert bs_gamma(100, 100, 0.5, 0.06, 0.2) > 0
    assert bs_vega(100, 100, 0.5, 0.06, 0.2) > 0
    assert bs_delta(100, 100, 0.5, 0.06, 0.2, is_call=False) < 0


# --------------------------------------------------- chain + universe mgr
def test_synthetic_chain_has_both_sides():
    prov = SyntheticChainProvider(iv=0.15, lot_size=75)
    ch = prov.chain("NIFTY", 24000, datetime(2026, 6, 12, 10, 0))
    assert ch.nearest_expiry() is not None
    exp = ch.nearest_expiry()
    calls = [q for q in ch.for_expiry(exp) if q.is_call]
    puts = [q for q in ch.for_expiry(exp) if not q.is_call]
    assert calls and puts
    atm_call = ch.strike_nearest(exp, 24000, is_call=True)
    assert abs(atm_call.strike - 24000) <= 100


def test_universe_manager_registers_legs():
    prov = SyntheticChainProvider(iv=0.15, lot_size=75)
    ch = prov.chain("NIFTY", 24000, datetime(2026, 6, 12, 10, 0))
    bars, insts = {}, {}
    mgr = OptionUniverseManager(strike_window=4)
    syms = mgr.register(bars, insts, {"NIFTY": ch}, datetime(2026, 6, 12, 10, 0))
    assert syms
    for s in syms:
        assert s in insts and s in bars
        assert insts[s].lot_size == 75
        assert insts[s].point_value == 1.0
        assert bars[s].last_close > 0


# ------------------------------------------ strategy + REAL sizer e2e
def _build_state(cfg, iv: float, spot: float = 24000.0):
    """MarketState with an underlying that has a trend + low realized vol, plus
    a synthetic chain in extra and its legs registered as instruments."""
    asof = datetime(2026, 6, 12, 10, 0)
    # underlying bars: gentle uptrend, low realized vol
    h = BarHistory(capacity=300)
    px = spot * 0.96
    for i in range(120):
        px *= 1.0 + 0.0003  # steady drift, ~no noise -> low RV
        h.append(Bar(ts=asof, open=px, high=px, low=px, close=px, volume=1e6))
    bars = {"NIFTY": h}
    from quantsys.core.types import Instrument, InstrumentKind
    insts = {"NIFTY": Instrument("NIFTY", kind=InstrumentKind.INDEX, lot_size=1,
                                 point_value=1.0)}
    prov = SyntheticChainProvider(iv=iv, lot_size=75, days_to_expiry=7)
    chain = prov.chain("NIFTY", h.last_close, asof)
    OptionUniverseManager(strike_window=8).register(bars, insts, {"NIFTY": chain}, asof)
    return MarketState(ts=asof, equity=50_000_000, bars=bars, instruments=insts,
                       positions={}, extra={"option_chains": {"NIFTY": chain},
                                            "bar_minutes": 5})


def test_credit_spread_emitted_when_iv_rich():
    cfg = load_config("config/base.yaml")
    cfg.voloptions.enabled = True
    strat = VolOptionsStrategy(cfg.voloptions)
    state = _build_state(cfg, iv=0.40)  # rich IV vs the ~tiny realized vol
    sigs = strat.generate_signals(state)
    assert len(sigs) == 1
    sig = sigs[0]
    assert len(sig.legs) == 2
    assert sig.direction == -1.0          # short premium (credit)
    assert sig.stop_distance > 0          # defined max loss
    assert "credit" in sig.tag


def test_spread_sizes_to_equal_contract_legs():
    """The defined-risk invariant: through the real SizingEngine the two legs
    come out with EQUAL magnitude and OPPOSITE sign (a true vertical)."""
    from quantsys.costs import CostModel
    from quantsys.core.types import AuditEvent
    from quantsys.portfolio.sizing import SizingEngine

    cfg = load_config("config/base.yaml")
    cfg.voloptions.enabled = True
    strat = VolOptionsStrategy(cfg.voloptions)
    state = _build_state(cfg, iv=0.40)
    sigs = strat.generate_signals(state)
    assert sigs

    sizer = SizingEngine(cfg.sizing, CostModel(cfg.costs), cfg.engine.min_order_notional)
    audits: list[AuditEvent] = []
    book = sizer.build_raw(sigs, {"voloptions": 1.0},
                           state.equity * cfg.sizing.base_risk_frac, state, audits)
    comps = [c for c in book.components if c.strategy == "voloptions"]
    assert len(comps) == 2
    qtys = sorted(c.qty for c in comps)
    # equal magnitude, opposite sign (pre-lot-rounding, raw build)
    assert qtys[0] == pytest.approx(-qtys[1], rel=1e-6)
    assert qtys[0] < 0 < qtys[1]


def test_no_chain_means_no_signals():
    cfg = load_config("config/base.yaml")
    cfg.voloptions.enabled = True
    strat = VolOptionsStrategy(cfg.voloptions)
    asof = datetime(2026, 6, 12, 10, 0)
    h = BarHistory()
    for _ in range(120):
        h.append(Bar(ts=asof, open=100, high=100, low=100, close=100))
    state = MarketState(ts=asof, equity=1e6, bars={"NIFTY": h},
                        instruments={}, positions={}, extra={})
    assert strat.generate_signals(state) == []


def test_voloptions_disabled_by_default():
    cfg = load_config("config/base.yaml")
    assert cfg.voloptions.enabled is False
