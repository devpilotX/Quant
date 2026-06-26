"""Shared test fixtures: synthetic price processes, state builders, and a
small-warmup config so tests run in seconds while exercising the same code
paths as production settings."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from quantsys.config.schema import AppConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import Instrument, InstrumentKind
from quantsys.data.history import BarHistory

BARS_PER_SESSION = 75  # 375 min / 5-min bars


def ts_seq(n: int, start: datetime | None = None) -> list[datetime]:
    """5-min decision bars laid out over consecutive NSE sessions."""
    t0 = start or datetime(2026, 1, 5, 9, 15)
    out = []
    day, bar = 0, 0
    for _ in range(n):
        out.append(t0 + timedelta(days=day, minutes=5 * bar))
        bar += 1
        if bar >= BARS_PER_SESSION:
            bar = 0
            day += 1
    return out


def make_hist(closes, spread: float = 0.002, volume: float = 1e6,
              times: list[datetime] | None = None) -> BarHistory:
    c = np.asarray(closes, dtype=float)
    n = len(c)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + spread / 2)
    lo = np.minimum(o, c) * (1 - spread / 2)
    t = times or ts_seq(n)
    ts = np.array([x.timestamp() for x in t[:n]])
    return BarHistory.from_arrays(ts, o, h, lo, c, np.full(n, volume))


def make_inst(symbol: str, kind=InstrumentKind.EQUITY, lot_size=1, sector=None,
              adv=None, margin_rate=1.0, tick_size=0.05, point_value=1.0) -> Instrument:
    return Instrument(symbol=symbol, kind=kind, lot_size=lot_size, sector=sector,
                      adv=adv, margin_rate=margin_rate, tick_size=tick_size,
                      point_value=point_value)


def make_state(bars: dict, instruments: dict, equity: float = 1_000_000.0,
               positions: dict | None = None, ts: datetime | None = None) -> MarketState:
    some = next(iter(bars.values()))
    t = ts or datetime.fromtimestamp(float(some.ts[-1]))
    return MarketState(ts=t, equity=equity, bars=bars, instruments=instruments,
                       positions=positions or {})


def gbm(n: int, start=100.0, drift=0.0, vol=0.004, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    return start * np.exp(np.cumsum(r))


def ou_spread(n: int, kappa=0.05, sigma=0.004, seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] * (1 - kappa) + rng.normal(0, sigma)
    return x


def cointegrated_pair(n: int, beta=1.0, kappa=0.05, seed=2) -> tuple[np.ndarray, np.ndarray]:
    # base leg needs real variance so Engle-Granger identifies beta cleanly
    b = gbm(n, start=50.0, vol=0.01, seed=seed)
    spread = ou_spread(n, kappa=kappa, seed=seed + 10)
    a = np.exp(beta * np.log(b) + spread + 0.7)
    return a, b


@pytest.fixture
def small_cfg() -> AppConfig:
    """Production code paths, toy warmups."""
    return AppConfig.model_validate({
        "engine": {"decision_bar_minutes": 5, "cov_window_bars": 60,
                   "cov_halflife_bars": 30.0, "min_order_notional": 1000.0,
                   "index_symbol": "NIFTY", "stop_cooldown_bars": 5},
        "trend": {"timeframe_bars": 1, "ema_fast": 5, "ema_slow": 20, "donchian": 10,
                  "atr_n": 5, "entry_threshold": 0.3, "exit_threshold": 0.12},
        "meanrev": {"timeframe_bars": 1, "lookback": 150, "rescan_every": 40,
                    "min_half_life": 3.0, "max_half_life": 80.0, "max_pairs": 2,
                    "cooldown_bars": 10},
        "regime": {"timeframe_bars": 1, "train_window": 200, "refit_every": 60,
                   "n_restarts": 2, "n_iter": 60},
        "kelly": {"edge_halflife_bars": 200.0, "prior_obs": 100.0,
                  "ramp_obs": 100.0, "ramp_floor": 0.10},
        # default ladder but with the cost gate mostly open: gate economics are
        # proven in test_sizing; e2e focuses on flow, caps and adaptation
        "tiers": {"hysteresis": 0.10, "ladder": [
            {"name": "T1", "min_equity": 1e5, "max_instruments": 2, "max_strategies": 1,
             "execution_style": "LIMIT_SINGLE", "adv_cap_pct": 0.01, "min_cost_multiple": 0.2,
             "rebalance_band": 0.35, "gross_leverage_cap": 1.0},
            {"name": "T2", "min_equity": 5e5, "max_instruments": 3, "max_strategies": 2,
             "execution_style": "LIMIT_SINGLE", "adv_cap_pct": 0.01, "min_cost_multiple": 0.15,
             "rebalance_band": 0.30, "gross_leverage_cap": 1.2},
            {"name": "T3", "min_equity": 1.5e6, "max_instruments": 5, "max_strategies": 3,
             "execution_style": "LIMIT_SMART", "adv_cap_pct": 0.015, "min_cost_multiple": 0.1,
             "rebalance_band": 0.25, "gross_leverage_cap": 1.5},
            {"name": "T4", "min_equity": 1e7, "max_instruments": 20, "max_strategies": 4,
             "execution_style": "LIMIT_SMART", "adv_cap_pct": 0.02, "min_cost_multiple": 0.1,
             "rebalance_band": 0.20, "gross_leverage_cap": 2.0},
            {"name": "T5", "min_equity": 5e7, "max_instruments": 30, "max_strategies": 4,
             "execution_style": "SLICE_TWAP", "adv_cap_pct": 0.03, "min_cost_multiple": 0.1,
             "rebalance_band": 0.15, "gross_leverage_cap": 2.2},
            {"name": "T6", "min_equity": 1.5e8, "max_instruments": 40, "max_strategies": 4,
             "execution_style": "ALMGREN_CHRISS", "adv_cap_pct": 0.05, "min_cost_multiple": 0.1,
             "rebalance_band": 0.10, "gross_leverage_cap": 2.5},
        ]},
    })
