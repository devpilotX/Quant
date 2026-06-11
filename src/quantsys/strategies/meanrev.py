"""Cointegration pairs / statistical arbitrage.

Pipeline per re-scan (weekly-ish on the decision clock):
  same-sector candidate pairs -> OLS hedge ratio -> Engle-Granger ADF on the
  residual -> AR(1)/OU fit (kappa, half-life) -> split-half kappa stability
  -> keep the best `max_pairs`, one pair per symbol, best-p-value first.

Spread X = ln(Pa) - beta*ln(Pb), modelled OU: dX = kappa*(theta - X)dt + s dW.
Trade z = (X - theta)/sigma_eq: enter |z| >= z_entry against the move, exit at
z_exit, hard stop at z_stop, time stop after k half-lives. Parameters are
FROZEN per trade episode (no mid-trade re-estimation drift); flat pairs adopt
fresh parameters at the next scan.

Multi-leg representation: ONE signal, parent leg A ratio +1.0, leg B ratio
-beta. The sizing engine preserves the hedge ratio through every cap.
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np

from quantsys.config.schema import MeanRevConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import InstrumentKind, LegSpec, Signal
from quantsys.strategies.base import Strategy, register

_TRADEABLE = {InstrumentKind.EQUITY, InstrumentKind.FUTURE}


@register("meanrev")
class MeanRevStrategy(Strategy):
    def __init__(self, cfg: MeanRevConfig):
        super().__init__("meanrev")
        self.cfg = cfg
        self._pairs: dict[str, dict] = {}      # "A|B" -> params + episode state
        self._bars_since_scan = 10**9

    def warmup_bars(self) -> int:
        return self.cfg.timeframe_bars * (self.cfg.lookback + 10)

    # ------------------------------------------------------------------ api
    def generate_signals(self, state: MarketState) -> list[Signal]:
        cfg = self.cfg
        self._bars_since_scan += 1
        if self._bars_since_scan >= cfg.rescan_every:
            self._rescan(state)

        out: list[Signal] = []
        for key in sorted(self._pairs):
            p = self._pairs[key]
            a_sym, b_sym = key.split("|")
            pa, pb = state.price(a_sym), state.price(b_sym)
            if not (math.isfinite(pa) and math.isfinite(pb) and pa > 0 and pb > 0):
                # data gap: fail safe — flatten by not emitting; flat pairs drop
                if p["dir"] == 0:
                    del self._pairs[key]
                else:
                    p["dir"] = 0
                continue

            x = math.log(pa) - p["beta"] * math.log(pb)
            z = (x - p["theta"]) / p["sigma_eq"]

            if p["dir"] == 0:
                p["cooldown"] = max(0, p["cooldown"] - 1)
                if p.get("rearm") and abs(z) < cfg.z_entry:
                    p["rearm"] = False  # spread revisited normalcy: re-armed
                if (p["cooldown"] == 0 and not p.get("rearm")
                        and cfg.z_entry <= abs(z) < cfg.z_stop):
                    p["dir"] = -1 if z > 0 else 1
                    p["entry_absz"] = abs(z)
                    p["bars_held"] = 0
                    p["stop_px"] = pa * (cfg.z_stop - abs(z)) * p["sigma_eq"]
            else:
                p["bars_held"] += 1
                adverse_z = -p["dir"] * z  # entry ~ +z_entry, stop at +z_stop
                hl_decision_bars = p["half_life"] * cfg.timeframe_bars
                if adverse_z >= cfg.z_stop:
                    p["dir"] = 0
                    p["cooldown"] = cfg.cooldown_bars
                    p["rearm"] = True   # no re-entry until z normalises
                elif adverse_z <= cfg.z_exit:
                    p["dir"] = 0  # mean reached — take profit
                elif p["bars_held"] > cfg.time_stop_half_lives * hl_decision_bars:
                    p["dir"] = 0  # OU clock expired; thesis stale
                    p["cooldown"] = cfg.cooldown_bars
                    p["rearm"] = True

            if p["dir"] != 0:
                out.append(
                    Signal(
                        strategy=self.name,
                        symbol=a_sym,
                        direction=float(p["dir"]),
                        stop_distance=p["stop_px"],
                        legs=(LegSpec(a_sym, 1.0), LegSpec(b_sym, -p["beta"])),
                        expected_edge_R=cfg.expected_edge_R,
                        horizon_bars=int(cfg.time_stop_half_lives * p["half_life"] * cfg.timeframe_bars),
                        tag=key,
                    )
                )
        return out

    # ------------------------------------------------------------- scanning
    def _rescan(self, state: MarketState) -> None:
        cfg = self.cfg
        self._bars_since_scan = 0
        by_sector: dict[tuple, list[str]] = {}
        for sym in sorted(state.bars):
            inst = state.instruments.get(sym)
            if inst is None or inst.kind not in _TRADEABLE:
                continue
            by_sector.setdefault((inst.sector or "?", inst.kind), []).append(sym)

        candidates: list[tuple[float, str, dict]] = []
        for (_sector, _kind), syms in sorted(by_sector.items()):
            for a, b in combinations(syms, 2):
                fit = self._fit_pair(state, a, b)
                if fit is not None:
                    candidates.append((fit["pvalue"], f"{a}|{b}", fit))

        candidates.sort(key=lambda t: (t[0], t[1]))
        held = {k: v for k, v in self._pairs.items() if v["dir"] != 0}
        used = {s for k in held for s in k.split("|")}
        fresh: dict[str, dict] = {}
        for _p, key, fit in candidates:
            if len(held) + len(fresh) >= cfg.max_pairs:
                break
            a, b = key.split("|")
            if a in used or b in used or key in held:
                continue
            prev = self._pairs.get(key)
            fresh[key] = {**fit, "dir": 0, "cooldown": prev["cooldown"] if prev else 0,
                          "bars_held": 0, "entry_absz": 0.0, "stop_px": 0.0}
            used.update((a, b))
        # in-position pairs keep their frozen episode params; flat ones refresh
        self._pairs = {**held, **fresh}

    def _fit_pair(self, state: MarketState, a: str, b: str) -> dict | None:
        cfg = self.cfg
        ra = state.bars[a].resampled(cfg.timeframe_bars)
        rb = state.bars[b].resampled(cfg.timeframe_bars)
        if not ra or not rb:
            return None
        n = min(len(ra["close"]), len(rb["close"]))
        if n < cfg.lookback:
            return None
        y = np.log(ra["close"][-cfg.lookback:])
        x = np.log(rb["close"][-cfg.lookback:])
        if not (np.all(np.isfinite(y)) and np.all(np.isfinite(x))):
            return None

        vx = float(np.var(x))
        if vx < 1e-12:
            return None
        beta = float(np.cov(x, y, bias=True)[0, 1] / vx)
        if not (0.1 <= beta <= 10.0):
            return None
        alpha = float(y.mean() - beta * x.mean())
        resid = y - (alpha + beta * x)

        from statsmodels.tsa.stattools import adfuller

        try:
            pvalue = float(adfuller(resid, regression="c", autolag="AIC")[1])
        except Exception:
            return None
        if pvalue > cfg.adf_alpha:
            return None

        kappa_full, hl = self._ou_kappa(resid)
        if kappa_full is None or not (cfg.min_half_life <= hl <= cfg.max_half_life):
            return None
        half = len(resid) // 2
        k1, _ = self._ou_kappa(resid[:half])
        k2, _ = self._ou_kappa(resid[half:])
        if k1 is None or k2 is None:
            return None
        ratio = max(k1, k2) / max(min(k1, k2), 1e-12)
        if ratio > cfg.kappa_stability:
            return None  # unstable reversion speed — reject (spec requirement)

        return {
            "beta": beta,
            "theta": alpha + float(resid.mean()),  # == alpha; resid mean ~ 0
            "sigma_eq": float(resid.std()) or 1e-9,
            "half_life": hl,
            "pvalue": pvalue,
        }

    @staticmethod
    def _ou_kappa(resid: np.ndarray) -> tuple[float | None, float]:
        """AR(1) phi -> kappa = -ln(phi) per strategy bar; half-life ln2/kappa."""
        if len(resid) < 30:
            return None, 0.0
        r0, r1 = resid[:-1], resid[1:]
        denom = float(np.dot(r0, r0))
        if denom <= 0:
            return None, 0.0
        phi = float(np.dot(r0, r1) / denom)
        if not (0.0 < phi < 1.0):
            return None, 0.0
        kappa = -math.log(phi)
        return kappa, math.log(2.0) / kappa

    # ---------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        return {"pairs": self._pairs, "bars_since_scan": self._bars_since_scan}

    def load_state(self, d: dict) -> None:
        self._pairs = {k: dict(v) for k, v in d.get("pairs", {}).items()}
        self._bars_since_scan = d.get("bars_since_scan", 10**9)
