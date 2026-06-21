"""DecisionEngine — the brain. One deterministic pass per decision bar:

    1. equity/kill/throttle (risk pre-pass)        risk/engine.py
    2. capital tier resolution                     portfolio/tiers.py
    3. regime probabilities                        regime/detector.py
    4. universe + strategy gating (tier)           here
    5. signals from the strategy ensemble          strategies/*
    6. hard-stop vetoes + cooldowns                risk/engine.py
    7. fractional-Kelly allocation                 portfolio/allocation.py
    8. risk-based sizing -> vol targeting ->
       exposure rules -> lots/cost gate            portfolio/, risk/rules.py
    9. order diff with anti-churn bands            engine/orders.py

`decide(state)` is pure given (state, config, engine state) — no I/O, no
wall clock, no randomness — so backtest, paper and live run THE SAME code.
Orchestrator contract per new bar: post_bar(state) THEN decide(state).
"""

from __future__ import annotations

import math

import numpy as np

from quantsys.config.schema import AppConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import (
    AuditEvent,
    Decision,
    Instrument,
    InstrumentKind,
    bars_per_day,
)
from quantsys.costs import CostModel
from quantsys.data.features import aligned_close_matrix, corr_from_cov, ewma_cov, ewma_vol
from quantsys.engine.orders import diff_orders
from quantsys.portfolio.allocation import KellyAllocator, VolTargeter
from quantsys.portfolio.sizing import SizingEngine
from quantsys.portfolio.tiers import TierLadder
from quantsys.regime.detector import RegimeDetector
from quantsys.risk.engine import RiskEngine, positions_net
from quantsys.risk.rules import RiskContext, apply_exposure_rules
from quantsys.strategies.base import REGISTRY, OnlineEdgeStats, Strategy

# import for side effect: strategy registration
from quantsys.strategies import expiry as _expiry  # noqa: F401
from quantsys.strategies import factor as _factor  # noqa: F401
from quantsys.strategies import meanrev as _meanrev  # noqa: F401
from quantsys.strategies import trend as _trend  # noqa: F401
from quantsys.strategies import voloptions as _voloptions  # noqa: F401

_TRADEABLE = {InstrumentKind.EQUITY, InstrumentKind.FUTURE}


class DecisionEngine:
    def __init__(self, cfg: AppConfig, instruments: dict[str, Instrument] | None = None):
        self.cfg = cfg
        self.instruments = instruments if instruments is not None else {
            u.symbol: Instrument(
                symbol=u.symbol, token=u.token, exchange=u.exchange, kind=u.kind,
                lot_size=u.lot_size, tick_size=u.tick_size, point_value=u.point_value,
                sector=u.sector, adv=u.adv, margin_rate=u.margin_rate,
            )
            for u in cfg.universe
        }
        self.strategies: list[Strategy] = []
        for name, builder in sorted(REGISTRY.items()):
            block = getattr(cfg, name, None)
            if block is not None and getattr(block, "enabled", False):
                self.strategies.append(builder(block))
        self.strategies.sort(key=lambda s: getattr(getattr(cfg, s.name), "priority", 99))

        self.edge_stats = {
            s.name: OnlineEdgeStats(cfg.kelly.edge_halflife_bars, cfg.kelly.prior_obs, cfg.kelly.var_floor)
            for s in self.strategies
        }
        self.detector = RegimeDetector(cfg.regime, cfg.engine.index_symbol)
        self.risk = RiskEngine(
            cfg.drawdown, cfg.sizing.base_risk_frac,
            set(cfg.engine.trailing_stop_strategies), cfg.engine.stop_cooldown_bars,
        )
        self.ladder = TierLadder(cfg.tiers)
        self.allocator = KellyAllocator(cfg.kelly)
        self.cost_model = CostModel(cfg.costs)
        self.voltargeter = VolTargeter(cfg.vol_target, cfg.engine.decision_bar_minutes)
        self.sizer = SizingEngine(cfg.sizing, self.cost_model, cfg.engine.min_order_notional)

        # virtual unit books for online edge estimation (see post_bar)
        self._unit_nets: dict[str, dict[str, float]] = {}      # decided at t
        self._unit_nets_old: dict[str, dict[str, float]] = {}  # decided at t-1
        self._last_closes: dict[str, float] = {}

    # ------------------------------------------------------------------ api
    def decide(self, state: MarketState) -> Decision:
        audits: list[AuditEvent] = []
        cfg = self.cfg
        pre = self.risk.pre_decide(state.ts, state.equity)
        tier = self.ladder.resolve(state.equity)
        prices = {s: state.price(s) for s in state.bars}

        if pre.halted_reason is not None:
            audits.append(AuditEvent("engine", "halted", pre.halted_reason))
            return self._decision(state, tier, audits, halted=True,
                                  kill_reason=None, risk_pre=pre)

        if pre.kill_reason is not None:
            audits.append(AuditEvent("engine", "kill_switch", pre.kill_reason))
            orders = diff_orders([], state.positions, self.instruments, prices, tier,
                                 cfg.engine.min_order_notional, kill=True)
            self.risk.stops.refresh([])
            self._shift_unit_nets({}, prices)
            return self._decision(state, tier, audits, halted=False,
                                  kill_reason=pre.kill_reason, risk_pre=pre, orders=orders)

        regime = self.detector.update(state)
        universe = self._universe(state, tier)
        view = state.restricted(universe | {cfg.engine.index_symbol})
        active = self.strategies[: tier.max_strategies]
        active_names = [s.name for s in active]

        signals = []
        for strat in active:
            signals.extend(strat.generate_signals(view))

        # hard stops: veto signals, force exits, start cooldowns
        self.risk.stops.update_prices(state)
        hits = self.risk.stops.breached(state)
        self.risk.register_stop_hits(hits)
        kept = []
        for sig in signals:
            legs = {leg.symbol for leg in sig.resolved_legs()}
            hit = any((sig.strategy, s) in hits for s in legs)
            cool = any(self.risk.in_cooldown(sig.strategy, s) for s in legs)
            if hit or cool:
                audits.append(AuditEvent("risk", "stop_veto" if hit else "stop_cooldown",
                                         sig.group_id, symbol=sig.symbol))
            else:
                kept.append(sig)
        signals = kept

        # virtual unit book (f=1, un-throttled) for online edge stats
        scratch: list[AuditEvent] = []
        unit_book = self.sizer.build_raw(
            signals, {n: 1.0 for n in active_names},
            state.equity * cfg.sizing.base_risk_frac, view, scratch,
        )
        unit_nets: dict[str, dict[str, float]] = {n: {} for n in active_names}
        for c in unit_book.components:
            d = unit_nets.setdefault(c.strategy, {})
            d[c.symbol] = d.get(c.symbol, 0.0) + c.qty

        kelly_f = self.allocator.allocate(self.edge_stats, regime, active_names, audits)
        book = self.sizer.build_raw(signals, kelly_f, state.equity * pre.risk_frac_eff, view, audits)

        cov_syms, sigma = self._covariance(state, book.symbols())
        vol_scaler = self.voltargeter.scaler(book, prices, self.instruments,
                                             cov_syms, sigma, state.equity, audits)
        if regime.risk_scaler != 1.0:
            audits.append(AuditEvent("regime", "risk_scaler",
                                     f"{regime.label} ({regime.source}) x{regime.risk_scaler:.2f}"))
        book.scale_all(vol_scaler * regime.risk_scaler)

        ctx = RiskContext(
            equity=state.equity, prices=prices, instruments=self.instruments,
            cfg=cfg.exposure, tier=tier, corr_symbols=cov_syms,
            corr=corr_from_cov(sigma) if sigma is not None else None,
        )
        apply_exposure_rules(book, ctx, audits)

        sigma_daily = self._sigma_daily(state, book.symbols())
        targets = self.sizer.finalize(book, tier, view, dict(state.positions), sigma_daily, audits)

        self.risk.stops.refresh(targets)
        self._shift_unit_nets(unit_nets, prices)

        orders = diff_orders(
            targets, state.positions, self.instruments, prices, tier,
            cfg.engine.min_order_notional,
            risk_reducing_symbols={sym for (_, sym) in hits},
        )
        return self._decision(state, tier, audits, halted=False, kill_reason=None,
                              risk_pre=pre, regime=regime, signals=tuple(signals),
                              kelly=kelly_f, vol_scaler=vol_scaler,
                              targets=tuple(targets), orders=orders)

    def post_bar(self, state: MarketState) -> None:
        """Update online stats with the bar just completed. Call BEFORE decide()."""
        if not self._unit_nets:
            return
        for strat_name, nets in sorted(self._unit_nets.items()):
            pnl = 0.0
            for sym, q in nets.items():
                px_now, px_prev = state.price(sym), self._last_closes.get(sym)
                inst = self.instruments.get(sym)
                if px_prev is None or inst is None or not math.isfinite(px_now):
                    continue
                pnl += q * (px_now - px_prev) * inst.point_value
            # proportional cost on unit turnover (flat fees are scale-dependent
            # and excluded by design — documented in costs.py)
            old = self._unit_nets_old.get(strat_name, {})
            for sym in set(nets) | set(old):
                dq = abs(nets.get(sym, 0.0) - old.get(sym, 0.0))
                inst = self.instruments.get(sym)
                px = self._last_closes.get(sym)
                if dq > 0 and inst is not None and px:
                    pnl -= dq * px * inst.point_value * self.cost_model_proportional(inst)
            if state.equity > 0:
                self.edge_stats[strat_name].update(pnl / state.equity)

    def cost_model_proportional(self, inst: Instrument) -> float:
        """Per-side proportional cost fraction (taxes + slippage, no flat fees)."""
        big = 1_000_000
        price = 100.0
        rt = self.cost_model.round_trip(inst, big, price,
                                        delivery=(inst.kind == InstrumentKind.EQUITY))
        return rt / (2.0 * big * price * inst.point_value)

    # ------------------------------------------------------------- internals
    def _shift_unit_nets(self, new_nets: dict[str, dict[str, float]],
                         prices: dict[str, float]) -> None:
        self._unit_nets_old = self._unit_nets
        self._unit_nets = new_nets
        self._last_closes = {s: p for s, p in prices.items() if math.isfinite(p)}

    def _universe(self, state: MarketState, tier) -> set[str]:
        win = self.cfg.engine.liquidity_rank_window
        scored: list[tuple[float, str]] = []
        for sym in sorted(state.bars):
            inst = self.instruments.get(sym)
            if inst is None or inst.kind not in _TRADEABLE:
                continue
            px = state.price(sym)
            if not (math.isfinite(px) and px > 0):
                continue
            if inst.adv:
                score = inst.adv * px
            else:
                vol = state.bars[sym].volume
                score = float(np.nanmean(vol[-win:])) * px if len(vol) else 0.0
            scored.append((-score, sym))
        scored.sort()
        return {sym for _, sym in scored[: tier.max_instruments]}

    def _covariance(self, state: MarketState, symbols: list[str]):
        cfg = self.cfg.engine
        if not symbols:
            return [], None
        window = cfg.cov_window_bars
        usable = [s for s in symbols if state.n_bars(s) >= window + 1]
        if len(usable) < 1:
            return [], None
        closes = aligned_close_matrix(state.bars, usable, window + 1)
        if closes is None:
            return [], None
        R = np.diff(np.log(closes), axis=0)
        return usable, ewma_cov(R, cfg.cov_halflife_bars, cfg.cov_shrink)

    def _sigma_daily(self, state: MarketState, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        bpd = bars_per_day(self.cfg.engine.decision_bar_minutes)
        for s in symbols:
            h = state.bars.get(s)
            if h is None or len(h) < 50:
                continue
            r = np.diff(np.log(h.close[-min(len(h), 500):]))
            v = ewma_vol(r, self.cfg.engine.vol_halflife_bars)
            if math.isfinite(v):
                out[s] = v * math.sqrt(bpd)
        return out

    def _decision(self, state, tier, audits, *, halted, kill_reason, risk_pre,
                  regime=None, signals=(), kelly=None, vol_scaler=1.0,
                  targets=(), orders=()) -> Decision:
        from quantsys.core.types import RegimeState

        return Decision(
            ts=state.ts,
            equity=state.equity,
            tier_name=tier.name,
            regime=regime or RegimeState("calm_range", {"calm_range": 1.0}, 0.0, {}, "none"),
            signals=signals,
            kelly=dict(kelly or {}),
            vol_scaler=vol_scaler,
            risk_frac_eff=risk_pre.risk_frac_eff,
            targets=targets,
            orders=tuple(orders),
            halted=halted,
            kill_reason=kill_reason,
            audit=tuple(audits),
        )

    # ----------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        return {
            "risk": self.risk.state_dict(),
            "ladder": self.ladder.state_dict(),
            "detector": self.detector.state_dict(),
            "edge_stats": {k: v.to_dict() for k, v in self.edge_stats.items()},
            "strategies": {s.name: s.state_dict() for s in self.strategies},
            "unit_nets": self._unit_nets,
            "unit_nets_old": self._unit_nets_old,
            "last_closes": self._last_closes,
        }

    def load_state(self, d: dict) -> None:
        self.risk.load_state(d.get("risk", {}))
        self.ladder.load_state(d.get("ladder", {}))
        self.detector.load_state(d.get("detector", {}))
        for k, v in d.get("edge_stats", {}).items():
            if k in self.edge_stats:
                self.edge_stats[k].load(v)
        for s in self.strategies:
            if s.name in d.get("strategies", {}):
                s.load_state(d["strategies"][s.name])
        self._unit_nets = d.get("unit_nets", {})
        self._unit_nets_old = d.get("unit_nets_old", {})
        self._last_closes = d.get("last_closes", {})
