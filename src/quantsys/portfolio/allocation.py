"""Capital allocation across strategies (fractional Kelly) and the book-level
volatility targeter.

Kelly: f_s = clip(kelly_fraction * mu_s / var_s, 0, f_cap) * regime_weight_s,
with mu already shrunk toward zero by the edge estimator. Incubation: while a
strategy has too little history for the online stats to mean anything, it gets
a small floor allocation (it must trade to earn statistics — the classic
online-Kelly chicken-and-egg). The floor is withdrawn early if evidence is
already clearly negative.

Vol targeting runs on the PROPOSED BOOK against the instrument-return
covariance (EWMA + diagonal shrinkage). Decision note: the spec sketches
strategy-return covariance here; instrument covariance is used instead because
it is observable from day one (no own-track-record needed), well conditioned,
and directly measures the thing being capped — book P&L variance. Strategy
covariance still enters implicitly through per-strategy Kelly stats.
"""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np

from quantsys.config.schema import KellyConfig, VolTargetConfig
from quantsys.core.types import AuditEvent, Instrument, RegimeState, bars_per_year
from quantsys.portfolio.book import TargetBook
from quantsys.strategies.base import OnlineEdgeStats


class KellyAllocator:
    def __init__(self, cfg: KellyConfig):
        self.cfg = cfg

    def allocate(
        self,
        stats: Mapping[str, OnlineEdgeStats],
        regime: RegimeState,
        active: list[str],
        audits: list[AuditEvent],
    ) -> dict[str, float]:
        cfg = self.cfg
        f: dict[str, float] = {}
        for s in sorted(active):
            st = stats[s]
            var = max(st.var, cfg.var_floor)
            f_raw = cfg.kelly_fraction * st.mean / var if var > 0 else 0.0
            f_s = float(np.clip(f_raw, 0.0, cfg.f_cap))
            if st.n_eff < cfg.ramp_obs and f_s < cfg.ramp_floor:
                # incubation, unless evidence is already clearly negative
                f_s = cfg.ramp_floor if f_raw > -cfg.ramp_floor else 0.0
                audits.append(AuditEvent("kelly", "incubation_floor",
                                         f"{s}: n_eff={st.n_eff:.0f} f->{f_s:.3f}"))
            if cfg.explore_floor > 0.0 and f_s < cfg.explore_floor:
                # forced, edge-agnostic exploration — paper-only plumbing
                # validation, NOT withdrawn by negative evidence. The default
                # (0.0) leaves live and the backtest gate fully honest.
                f_s = cfg.explore_floor
                audits.append(AuditEvent("kelly", "explore_floor",
                                         f"{s}: forced f->{f_s:.3f} (paper exploration)"))
            f_s *= regime.strategy_weights.get(s, 1.0)
            f[s] = min(f_s, cfg.f_cap)

        gross = sum(f.values())
        if gross > cfg.gross_f_cap and gross > 0:
            scale = cfg.gross_f_cap / gross
            f = {k: v * scale for k, v in f.items()}
            audits.append(AuditEvent("kelly", "gross_f_cap",
                                     f"sum f {gross:.3f} -> {cfg.gross_f_cap:.3f}"))
        return f


class VolTargeter:
    def __init__(self, cfg: VolTargetConfig, bar_minutes: float):
        self.cfg = cfg
        self.bar_minutes = bar_minutes

    def scaler(
        self,
        book: TargetBook,
        prices: Mapping[str, float],
        instruments: Mapping[str, Instrument],
        cov_symbols: list[str],
        sigma_bar: np.ndarray | None,
        equity: float,
        audits: list[AuditEvent],
    ) -> float:
        """target_vol / annualised proposed-book vol, clipped to config bounds."""
        cfg = self.cfg
        if book.empty or sigma_bar is None or not cov_symbols or equity <= 0:
            return 1.0
        nn = book.net_notional(prices, instruments)
        w = np.array([nn.get(s, 0.0) / equity for s in cov_symbols])
        var_bar = float(w @ sigma_bar @ w)
        if var_bar <= 1e-18:
            return 1.0
        ann_vol = math.sqrt(var_bar * bars_per_year(self.bar_minutes))
        s = float(np.clip(cfg.annual_vol_target / ann_vol, cfg.scaler_min, cfg.scaler_max))
        audits.append(AuditEvent("vol_target", "scaler",
                                 f"book_vol={ann_vol:.4f} target={cfg.annual_vol_target} scaler={s:.3f}"))
        return s
