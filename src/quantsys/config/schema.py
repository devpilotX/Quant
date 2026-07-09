"""Typed configuration tree (pydantic v2). YAML files validate against this;
code defaults here ARE the documented baseline. Every tunable in the system
lives in this tree — nothing risk-relevant is hard-coded anywhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from quantsys.core.types import ExecutionStyle, InstrumentKind


class EngineConfig(BaseModel):
    decision_bar_minutes: int = 5
    cov_window_bars: int = 400          # rolling window fed to EWMA cov (< strategy warmups)
    cov_halflife_bars: float = 150.0
    cov_shrink: float = 0.15
    vol_halflife_bars: float = 60.0     # per-instrument EWMA vol for impact/stops
    min_order_notional: float = 5_000.0
    # Equity-scaled dust floor: effective min notional = max(min_order_notional,
    # min_order_frac * equity). A flat Rs5k floor is meaningless on a Rs15cr
    # book — 1-share rebalance dribbles passed it and churned every bar. 0 = off
    # (small accounts keep the flat floor).
    min_order_frac: float = 0.0
    index_symbol: str = "NIFTY"         # regime features source
    trailing_stop_strategies: list[str] = ["trend"]
    stop_cooldown_bars: int = 12        # re-entry lockout after a hard stop
    liquidity_rank_window: int = 250    # bars of volume used to rank universe
    # Paper-only exploration: when running --paper, the LiveRunner forces a
    # small edge-agnostic allocation and bypasses the cost gate so the engine
    # exercises the order->fill->reconcile->P&L plumbing even when no strategy
    # has positive edge after costs. NEVER applied in live (the honest gate
    # stays intact) or in the backtest. Off => paper behaves like live/backtest.
    paper_explore: bool = True
    paper_explore_floor: float = 0.05   # forced per-strategy Kelly f in paper
    # When exploring in paper, also bypass the cost gate (let trades through even
    # if they don't cover costs). True = the original plumbing-exercise behavior;
    # False = keep the honest cost gate so paper only trades where the expected
    # edge covers costs (paper P&L then reflects how live would actually run).
    paper_explore_bypass_cost_gate: bool = True


class SizingConfig(BaseModel):
    base_risk_frac: float = Field(0.006, gt=0, le=0.02)   # risk per unit trade
    atr_min_stop_ticks: int = 5
    conviction_weighting: bool = True
    max_signals_per_strategy: int = 12
    enforce_cost_gate: bool = True   # paper exploration sets this False (see EngineConfig)
    # Min-lot promotion (index-futures unlock): a single-leg FUTURE group whose
    # risk budget rounds below one lot may be promoted to exactly ONE lot iff
    # that lot's rupee risk stays within promotion_max_risk_frac of equity.
    # Off by default: at a small float the honest answer stays "too big to trade".
    min_lot_promotion: bool = False
    promotion_max_risk_frac: float = Field(0.005, gt=0, le=0.02)


class KellyConfig(BaseModel):
    kelly_fraction: float = Field(0.30, gt=0, le=0.5)  # full Kelly forbidden
    f_cap: float = 0.35
    gross_f_cap: float = 0.80
    edge_halflife_bars: float = 1500.0
    prior_obs: float = 750.0       # zero-edge prior weight (shrinks young means)
    var_floor: float = 1e-10
    ramp_floor: float = 0.08       # incubation allocation while n_eff < ramp_obs
    ramp_obs: float = 750.0
    explore_floor: float = 0.0     # forced min allocation (paper exploration only); 0 = off
    # Regime-conditional Kelly tilt: per-(strategy, regime-label) edge stats
    # (same EWMA estimator, soft-assigned by regime probability) tilt each
    # strategy's f by clip(1 + beta * sum_label p_label * tanh(t_label / 2),
    # min, max) where t is the bucket's shrunk t-stat with n_eff capped. The
    # allocation ADAPTS to which regimes a sleeve has actually earned in —
    # walk-forward by construction (only past bars enter the buckets).
    # beta = 0 (default) disables the tilt entirely: live/backtest unchanged.
    regime_tilt_beta: float = 0.0
    regime_tilt_min: float = 0.5
    regime_tilt_max: float = 1.5
    regime_tilt_neff_cap: float = 400.0

    @field_validator("kelly_fraction")
    @classmethod
    def _never_full_kelly(cls, v: float) -> float:
        if v > 0.5:
            raise ValueError("kelly_fraction > 0.5 is over-betting; refused")
        return v


class VolTargetConfig(BaseModel):
    annual_vol_target: float = Field(0.13, gt=0, le=0.40)
    scaler_min: float = 0.25
    scaler_max: float = 2.0


class DrawdownConfig(BaseModel):
    max_drawdown: float = 0.18        # risk_frac scales to 0 here (throttle)
    kill_drawdown: float = 0.20       # hard kill; manual re-arm
    daily_loss_limit: float = 0.025   # of session-open equity; kill for the day
    auto_rearm_daily: bool = True     # day kill clears at next session open


class ExposureConfig(BaseModel):
    per_instrument_frac: float = 0.25   # gross notional per symbol / E
    sector_frac: float = 0.50
    net_frac: float = 1.50
    margin_util_cap: float = 0.60
    corr_threshold: float = 0.70
    corr_cluster_frac: float = 0.40
    # gross cap comes from the tier ladder (leverage grows with capital)


class RegimeLabelConfig(BaseModel):
    risk_scaler: float = 1.0
    strategy_weights: dict[str, float] = {}


class RegimeConfig(BaseModel):
    n_states: int = 3
    timeframe_bars: int = 6            # regime features at 6 x decision bars
    train_window: int = 750            # regime-timeframe observations
    refit_every: int = 125             # decision bars between refits
    n_restarts: int = 4
    n_iter: int = 150
    seed: int = 7
    vol_halflife: float = 20.0
    fallback_turbulent_pctile: float = 0.85
    fallback_trend_z: float = 0.75
    labels: dict[str, RegimeLabelConfig] = {
        "calm_trend": RegimeLabelConfig(risk_scaler=1.00, strategy_weights={"trend": 1.2, "meanrev": 0.8}),
        "calm_range": RegimeLabelConfig(risk_scaler=0.90, strategy_weights={"trend": 0.7, "meanrev": 1.2}),
        "turbulent": RegimeLabelConfig(risk_scaler=0.35, strategy_weights={"trend": 0.6, "meanrev": 0.4}),
    }


class TierConfig(BaseModel):
    name: str
    min_equity: float
    max_instruments: int
    max_strategies: int
    execution_style: ExecutionStyle
    adv_cap_pct: float            # position <= this frac of ADV
    min_cost_multiple: float      # expected edge >= multiple * round-trip cost
    rebalance_band: float         # skip rebalances < band * |target qty|
    gross_leverage_cap: float


class TiersConfig(BaseModel):
    hysteresis: float = 0.10      # demote only when E < (1-h) * tier threshold
    ladder: list[TierConfig] = []

    @model_validator(mode="after")
    def _default_ladder(self) -> "TiersConfig":
        if not self.ladder:
            self.ladder = _DEFAULT_LADDER()
        thresholds = [t.min_equity for t in self.ladder]
        if thresholds != sorted(thresholds):
            raise ValueError("tier ladder must be sorted by min_equity")
        return self


def _DEFAULT_LADDER() -> list[TierConfig]:
    E = ExecutionStyle
    return [
        TierConfig(name="T1", min_equity=1e5, max_instruments=2, max_strategies=1,
                   execution_style=E.LIMIT_SINGLE, adv_cap_pct=0.01, min_cost_multiple=5.0,
                   rebalance_band=0.35, gross_leverage_cap=1.0),
        TierConfig(name="T2", min_equity=5e5, max_instruments=3, max_strategies=2,
                   execution_style=E.LIMIT_SINGLE, adv_cap_pct=0.01, min_cost_multiple=4.0,
                   rebalance_band=0.30, gross_leverage_cap=1.2),
        TierConfig(name="T3", min_equity=1.5e6, max_instruments=5, max_strategies=3,
                   execution_style=E.LIMIT_SMART, adv_cap_pct=0.015, min_cost_multiple=3.0,
                   rebalance_band=0.25, gross_leverage_cap=1.5),
        TierConfig(name="T4", min_equity=1e7, max_instruments=20, max_strategies=4,
                   execution_style=E.LIMIT_SMART, adv_cap_pct=0.02, min_cost_multiple=2.0,
                   rebalance_band=0.20, gross_leverage_cap=2.0),
        TierConfig(name="T5", min_equity=5e7, max_instruments=30, max_strategies=4,
                   execution_style=E.SLICE_TWAP, adv_cap_pct=0.03, min_cost_multiple=1.5,
                   rebalance_band=0.15, gross_leverage_cap=2.2),
        TierConfig(name="T6", min_equity=1.5e8, max_instruments=40, max_strategies=4,
                   execution_style=E.ALMGREN_CHRISS, adv_cap_pct=0.05, min_cost_multiple=1.25,
                   rebalance_band=0.10, gross_leverage_cap=2.5),
    ]


class CostConfig(BaseModel):
    # Verified June 2026 (Budget 2026 STT effective 2026-04-01). Re-verify quarterly.
    brokerage_flat: float = 20.0
    brokerage_pct: float = 0.0025
    brokerage_delivery_flat: float = 0.0
    stt_future_sell: float = 0.0005
    stt_option_sell: float = 0.0015
    stt_delivery: float = 0.001
    stt_intraday_sell: float = 0.00025
    exch_equity: float = 0.000030699
    exch_future: float = 0.000018299
    exch_option: float = 0.0003552
    sebi_rate: float = 1e-6
    stamp_delivery: float = 0.00015
    stamp_intraday: float = 0.00003
    stamp_future: float = 0.00002
    stamp_option: float = 0.00003
    gst: float = 0.18
    slippage_bps: dict[str, float] = {"EQUITY": 3.0, "FUTURE": 1.5, "OPTION": 8.0, "INDEX": 0.0}
    impact_coeff: float = 0.1


class TrendConfig(BaseModel):
    enabled: bool = True
    priority: int = 1                 # lower = enabled first at small tiers
    timeframe_bars: int = 6           # 30-min bars on a 5-min decision clock
    ema_fast: int = 20
    ema_slow: int = 80
    donchian: int = 55
    entry_threshold: float = 0.45
    exit_threshold: float = 0.20
    atr_n: int = 14
    atr_mult: float = 2.5
    expected_edge_R: float = 0.12
    momentum_scale: float = 1.5       # tanh(mom / scale)
    breakout_weight: float = 0.35


class MeanRevConfig(BaseModel):
    enabled: bool = True
    priority: int = 2
    timeframe_bars: int = 3
    lookback: int = 500               # strategy bars for cointegration tests
    rescan_every: int = 375           # decision bars between pair re-scans
    adf_alpha: float = 0.05
    min_half_life: float = 10.0       # strategy bars
    max_half_life: float = 200.0
    kappa_stability: float = 2.5      # split-half kappa ratio bound
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_stop: float = 3.5
    time_stop_half_lives: float = 3.0
    cooldown_bars: int = 50           # after a stop-out, per pair
    max_pairs: int = 5
    expected_edge_R: float = 0.15


class VolOptionsConfig(BaseModel):
    # DISABLED by default: needs a live option-chain feed + OptionUniverseManager.
    enabled: bool = False
    priority: int = 3
    rv_window: int = 80               # decision bars for realized vol
    trend_window: int = 60
    trend_deadband: float = 0.004     # |price/SMA-1| below this = flat
    risk_free_rate: float = 0.066
    iv_rich_ratio: float = 1.15       # IV/RV >= this -> sell premium
    iv_cheap_ratio: float = 0.85      # IV/RV <= this (and trend) -> buy premium
    min_days_to_expiry: int = 2
    spread_width_steps: int = 2       # strikes between the two legs
    otm_offset_steps: int = 2         # how far OTM the short strike sits
    expected_edge_R: float = 0.10


class ExpiryConfig(BaseModel):
    # Research candidate (Phase 4), DISABLED by default and unvalidated. Enabled
    # only inside the OOS test harness until/unless it clears the gate. Single
    # pre-registered hypothesis (NOT to be tuned): NSE monthly F&O expiry (last
    # Thursday) concentrates options OI; market-maker hedging + settlement flows
    # mean-revert short-horizon price deviations into expiry. Rule: during the
    # expiry-week window, FADE deviations from a rolling mean; flat otherwise.
    enabled: bool = False
    priority: int = 4
    timeframe_bars: int = 2           # 30-min bars on a 15-min decision clock
    z_lookback: int = 20              # bars for the mean/std of the price-deviation z
    z_entry: float = 1.5              # fade when |z| >= this
    window_days: int = 7              # active in the last N calendar days of the month
    atr_n: int = 14
    atr_mult: float = 2.5
    expected_edge_R: float = 0.15


class FactorConfig(BaseModel):
    # Pillar 2: broad-universe cross-sectional equity factors (price/volume only:
    # momentum + low-vol). DISABLED by default and unvalidated against the gate.
    # This is a DAILY, broad-universe strategy: it needs many names (>= min_universe)
    # and a daily decision clock to be meaningful, so it is a safe no-op in the live
    # intraday/narrow-universe engine. It stays off until the research harness
    # (quantsys.research.run_pillar2) shows a gate-clearing OOS edge AND a daily
    # broad-universe feed is wired. See docs/PILLAR2_FACTOR_RESEARCH.md.
    enabled: bool = False
    priority: int = 5
    timeframe_bars: int = 75          # ~1 trading day on a 5-min clock (resample to daily)
    lookback_bars: int = 252          # 12-month momentum (in resampled/daily bars)
    skip_bars: int = 21               # skip most-recent month (12-1 momentum)
    vol_lookback: int = 252           # low-vol factor window
    rebalance_bars: int = 21          # monthly rebalance cadence (resampled bars)
    top_k: int = 30                   # longs (and shorts if market_neutral)
    min_universe: int = 40            # emit nothing below this breadth (live-narrow safe)
    market_neutral: bool = True       # long top-K / short bottom-K, dollar-neutral
    atr_n: int = 14                   # ATR window for the per-name risk stop
    atr_mult: float = 2.5
    expected_edge_R: float = 0.10


class DownShockConfig(BaseModel):
    # Pillar 4 event sleeve — the down-shock underreaction drift, promoted from
    # the zero-risk tracker to a PAPER sleeve for the forward study. The rule is
    # the FROZEN research config (docs/PILLAR4_EVENT_DRIVEN.md §8a + gate table:
    # z 3.5 / hold 10, IS-selected, hold-out Sharpe 1.75 but deflated 0.46 →
    # historically DEAD; forward evidence is the only open question). DISABLED
    # by default. Deviation, disclosed: the research sleeve was beta-hedged with
    # index futures; at explore-floor sizing one hedge lot exceeds the group
    # budget (lot rounding would drop every group), so the paper sleeve trades
    # the shocked name UNHEDGED SHORT and the book's net-exposure caps bound the
    # residual beta. Signals: daily panel z_t = r_t / sigma_{t-1} (60d lagged
    # std), volume ratio vs 20d mean; event = z <= -z_threshold AND vol_ratio >=
    # volume_ratio_min, de-clustered per name; enter next session, hold
    # hold_days sessions, daily-ATR stop.
    enabled: bool = False
    priority: int = 6
    z_threshold: float = 3.5
    vol_window: int = 60
    volume_window: int = 20
    volume_ratio_min: float = 2.0
    decluster_days: int = 30
    hold_days: int = 10
    max_concurrent: int = 5
    atr_n: int = 14
    atr_mult: float = 2.5
    expected_edge_R: float = 0.12
    min_history_days: int = 80


class ReversalConfig(BaseModel):
    # NEW pre-registered hypothesis (Forward Study 2): short-term cross-sectional
    # reversal — long the past-week losers, short the winners, dollar-neutral,
    # weekly cadence on the self-seeded daily panel (same machinery as factor).
    # Classic anomaly (Jegadeesh 1990); NO historical validation was run on our
    # data (turnover is high and costs likely bite — that is exactly what the
    # forward paper record measures). DISABLED by default.
    enabled: bool = False
    priority: int = 7
    lookback_days: int = 5
    rebalance_days: int = 5
    top_k: int = 8
    min_universe: int = 34
    market_neutral: bool = True
    atr_n: int = 14
    atr_mult: float = 2.5
    expected_edge_R: float = 0.08


class TomConfig(BaseModel):
    # NEW pre-registered hypothesis (Forward Study 2): turn-of-month index tilt —
    # long index futures from the last `days_before` WEEKDAYS of the month
    # through the first `days_after` weekdays of the next (documented
    # institutional-flow calendar effect; weekday approximation of session days,
    # deterministic from the bar timestamp alone). Flat otherwise. DISABLED by
    # default.
    enabled: bool = False
    priority: int = 8
    days_before: int = 2
    days_after: int = 3
    symbols: list[str] = ["NIFTY-FUT"]
    timeframe_bars: int = 25          # ~1 session per resampled bar on a 15-min clock
    atr_n: int = 14
    atr_mult: float = 3.0
    expected_edge_R: float = 0.08


class InstrumentConfig(BaseModel):
    symbol: str
    token: str = ""
    exchange: str = "NSE"
    kind: InstrumentKind = InstrumentKind.EQUITY
    lot_size: int = 1
    tick_size: float = 0.05
    point_value: float = 1.0
    sector: str | None = None
    adv: float | None = None
    margin_rate: float = 1.0


class AppConfig(BaseModel):
    engine: EngineConfig = EngineConfig()
    sizing: SizingConfig = SizingConfig()
    kelly: KellyConfig = KellyConfig()
    vol_target: VolTargetConfig = VolTargetConfig()
    drawdown: DrawdownConfig = DrawdownConfig()
    exposure: ExposureConfig = ExposureConfig()
    regime: RegimeConfig = RegimeConfig()
    tiers: TiersConfig = TiersConfig()
    costs: CostConfig = CostConfig()
    trend: TrendConfig = TrendConfig()
    meanrev: MeanRevConfig = MeanRevConfig()
    voloptions: VolOptionsConfig = VolOptionsConfig()
    expiry: ExpiryConfig = ExpiryConfig()
    factor: FactorConfig = FactorConfig()
    downshock: DownShockConfig = DownShockConfig()
    reversal: ReversalConfig = ReversalConfig()
    tom: TomConfig = TomConfig()
    universe: list[InstrumentConfig] = []


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(*paths: str | Path) -> AppConfig:
    """Load and deep-merge YAML files left-to-right, validate into AppConfig."""
    merged: dict[str, Any] = {}
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        merged = _deep_merge(merged, doc)
    return AppConfig.model_validate(merged)
