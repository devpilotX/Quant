"""Regime detection facade: index bars -> (return, log-vol) features at a slow
clock -> HMM filtered state probabilities -> blended RegimeState.

Fail-safe ladder (capital preservation first):
1. healthy fitted HMM      -> probabilistic regime (smooth, no cliff-edges)
2. fit degenerate/missing  -> deterministic vol-percentile fallback
3. not enough history yet  -> conservative warmup state (reduced risk)
"""

from __future__ import annotations

import numpy as np

from quantsys.config.schema import RegimeConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import RegimeState
from quantsys.data.features import ema, ewma_vol_series, log_returns
from quantsys.regime.hmm import HMMParams, filtered_probs, fit_hmm

LABELS = ("calm_trend", "calm_range", "turbulent")


class RegimeDetector:
    def __init__(self, cfg: RegimeConfig, index_symbol: str):
        self.cfg = cfg
        self.index_symbol = index_symbol
        self._params: HMMParams | None = None
        self._label_map: dict[int, str] = {}
        self._scaler_mu: np.ndarray | None = None
        self._scaler_sd: np.ndarray | None = None
        self._bars_since_fit = 10**9

    # ------------------------------------------------------------------ api
    def update(self, state: MarketState) -> RegimeState:
        X = self._features(state)
        if X is None or len(X) < 30:
            return self._compose({"calm_trend": 1 / 3, "calm_range": 1 / 3, "turbulent": 1 / 3}, "warmup")

        self._bars_since_fit += 1
        if self._params is None or self._bars_since_fit >= self.cfg.refit_every:
            self._try_fit(X)

        if self._params is not None and not self._params.degenerate:
            Z = self._standardize(X[-self.cfg.train_window :])
            p_states = filtered_probs(self._params, Z)
            probs = {lab: 0.0 for lab in LABELS}
            for k, p in enumerate(p_states):
                probs[self._label_map[k]] += float(p)
            return self._compose(probs, "hmm")
        return self._fallback(X)

    # ------------------------------------------------------------- internals
    def _features(self, state: MarketState) -> np.ndarray | None:
        hist = state.bars.get(self.index_symbol)
        if hist is None:
            return None
        rs = hist.resampled(self.cfg.timeframe_bars)
        if not rs or len(rs["close"]) < 30:
            return None
        r = log_returns(rs["close"])
        vol = ewma_vol_series(r, self.cfg.vol_halflife)
        return np.column_stack([r, np.log(np.clip(vol, 1e-8, None))])

    def _standardize(self, X: np.ndarray) -> np.ndarray:
        assert self._scaler_mu is not None and self._scaler_sd is not None
        return (X - self._scaler_mu) / self._scaler_sd

    def _try_fit(self, X: np.ndarray) -> None:
        self._bars_since_fit = 0
        if len(X) < max(120, self.cfg.train_window // 3):
            return  # keep whatever we have (possibly nothing -> fallback)
        W = X[-self.cfg.train_window :]
        mu, sd = W.mean(axis=0), np.clip(W.std(axis=0), 1e-9, None)
        params = fit_hmm(
            (W - mu) / sd,
            self.cfg.n_states,
            n_iter=self.cfg.n_iter,
            seed=self.cfg.seed,
            n_restarts=self.cfg.n_restarts,
        )
        if params.degenerate:
            return  # refuse a bad fit; keep previous model or fall back
        self._params = params
        self._scaler_mu, self._scaler_sd = mu, sd
        self._label_map = self._map_labels(params)

    @staticmethod
    def _map_labels(params: HMMParams) -> dict[int, str]:
        """vol-dim mean ranks states; the calmer two split by trendiness."""
        vol_means = params.means[:, 1]
        turbulent = int(np.argmax(vol_means))
        rest = [k for k in range(len(vol_means)) if k != turbulent]
        trendiness = {
            k: abs(params.means[k, 0]) / np.sqrt(params.variances[k, 0] + 1e-12) for k in rest
        }
        trend = max(rest, key=lambda k: trendiness[k])
        mapping = {turbulent: "turbulent", trend: "calm_trend"}
        for k in rest:
            mapping.setdefault(k, "calm_range")
        return mapping

    def _fallback(self, X: np.ndarray) -> RegimeState:
        vol = np.exp(X[:, 1])
        pctile = float((vol[:-1] <= vol[-1]).mean()) if len(vol) > 1 else 0.5
        if pctile >= self.cfg.fallback_turbulent_pctile:
            probs = {"turbulent": 0.9, "calm_range": 0.05, "calm_trend": 0.05}
        else:
            r = X[:, 0]
            mom = ema(r, 20)[-1]
            z = mom / (np.std(r) + 1e-12) * np.sqrt(20.0)
            if abs(z) >= self.cfg.fallback_trend_z:
                probs = {"calm_trend": 0.8, "calm_range": 0.15, "turbulent": 0.05}
            else:
                probs = {"calm_range": 0.8, "calm_trend": 0.15, "turbulent": 0.05}
        return self._compose(probs, "fallback")

    def _compose(self, probs: dict[str, float], source: str) -> RegimeState:
        cfg = self.cfg.labels
        risk = sum(p * cfg[lab].risk_scaler for lab, p in probs.items())
        strategies: set[str] = set()
        for lab in probs:
            strategies.update(cfg[lab].strategy_weights)
        weights = {
            s: sum(p * cfg[lab].strategy_weights.get(s, 1.0) for lab, p in probs.items())
            for s in strategies
        }
        label = max(probs, key=lambda k: probs[k])
        return RegimeState(label=label, probs=probs, risk_scaler=risk,
                           strategy_weights=weights, source=source)

    # ---------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        return {
            "params": self._params.to_dict() if self._params else None,
            "label_map": {str(k): v for k, v in self._label_map.items()},
            "scaler_mu": self._scaler_mu.tolist() if self._scaler_mu is not None else None,
            "scaler_sd": self._scaler_sd.tolist() if self._scaler_sd is not None else None,
            "bars_since_fit": self._bars_since_fit,
        }

    def load_state(self, d: dict) -> None:
        self._params = HMMParams.from_dict(d["params"]) if d.get("params") else None
        self._label_map = {int(k): v for k, v in d.get("label_map", {}).items()}
        mu, sd = d.get("scaler_mu"), d.get("scaler_sd")
        self._scaler_mu = np.array(mu) if mu is not None else None
        self._scaler_sd = np.array(sd) if sd is not None else None
        self._bars_since_fit = d.get("bars_since_fit", 10**9)
