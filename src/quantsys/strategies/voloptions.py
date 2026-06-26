"""Volatility sleeve — defined-risk index/stock option spreads.

Edge: implied vs realized volatility on the underlying.
- IV rich (IV/RV high): SELL premium with a credit vertical (defined risk).
  Direction chosen to lean WITH the underlying trend so the short strike is the
  out-of-the-money side (bull put when not bearish, else bear call).
- IV cheap (IV/RV low): BUY a debit vertical in the trend direction.

NEVER naked: every position is a two-leg vertical with a bought protective wing,
so max loss = strike width - net credit (credit) or net debit (debit). That max
loss per unit is the Signal's stop_distance, so the existing risk-fraction sizer
sizes the spread by its true defined risk. Greeks are delta-managed by re-
selecting strikes each bar (declarative targets; the order diff rolls as needed).

Disabled by default: it requires a live option-chain feed in
state.extra['option_chains'] and the option legs registered as instruments
(OptionUniverseManager). With no chain it emits nothing — safe no-op.
"""

from __future__ import annotations

import math

import numpy as np

from quantsys.config.schema import VolOptionsConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import LegSpec, Signal
from quantsys.options.blackscholes import implied_vol
from quantsys.options.chain import OptionChain
from quantsys.strategies.base import Strategy, register


@register("voloptions")
class VolOptionsStrategy(Strategy):
    def __init__(self, cfg: VolOptionsConfig):
        super().__init__("voloptions")
        self.cfg = cfg

    def warmup_bars(self) -> int:
        return self.cfg.rv_window + 5

    # ----------------------------------------------------------- helpers
    def _realized_vol(self, state: MarketState, underlying: str) -> float | None:
        h = state.bars.get(underlying)
        if h is None or len(h) < self.cfg.rv_window + 1:
            return None
        from quantsys.core.types import bars_per_year

        closes = h.close[-(self.cfg.rv_window + 1):]
        rets = np.diff(np.log(closes))
        if rets.size < 2:
            return None
        per_bar = float(np.std(rets, ddof=1))
        ann = math.sqrt(bars_per_year(state.extra.get("bar_minutes", 5)))
        return per_bar * ann

    def _trend(self, state: MarketState, underlying: str) -> float:
        """Cheap trend read: sign of (price - SMA) normalised; 0 if flat."""
        h = state.bars.get(underlying)
        if h is None or len(h) < self.cfg.trend_window:
            return 0.0
        c = h.close[-self.cfg.trend_window:]
        sma = float(np.mean(c))
        if sma <= 0:
            return 0.0
        z = (c[-1] - sma) / sma
        if abs(z) < self.cfg.trend_deadband:
            return 0.0
        return math.copysign(1.0, z)

    # --------------------------------------------------------- main loop
    def generate_signals(self, state: MarketState) -> list[Signal]:
        chains: dict[str, OptionChain] = state.extra.get("option_chains") or {}
        if not chains:
            return []
        out: list[Signal] = []
        for underlying in sorted(chains):
            sig = self._signal_for(state, underlying, chains[underlying])
            if sig is not None:
                out.append(sig)
        return out

    def _atm_iv(self, chain: OptionChain, expiry, T: float) -> float | None:
        call = chain.strike_nearest(expiry, chain.spot, is_call=True)
        if call is None:
            return None
        if call.iv is not None:
            return call.iv
        return implied_vol(call.ltp, chain.spot, call.strike, T,
                           self.cfg.risk_free_rate, is_call=True)

    def _signal_for(self, state: MarketState, underlying: str,
                    chain: OptionChain) -> Signal | None:
        rv = self._realized_vol(state, underlying)
        if rv is None or rv <= 0:
            return None
        expiry = chain.nearest_expiry(min_days=self.cfg.min_days_to_expiry)
        if expiry is None:
            return None
        T = max((expiry - chain.asof.date()).days, 1) / 365.0
        iv = self._atm_iv(chain, expiry, T)
        if iv is None or iv <= 0:
            return None
        ratio = iv / rv
        trend = self._trend(state, underlying)
        step = _infer_step(chain.for_expiry(expiry))
        width = self.cfg.spread_width_steps * step

        if ratio >= self.cfg.iv_rich_ratio:
            return self._credit_spread(chain, expiry, underlying, trend, width, ratio)
        if ratio <= self.cfg.iv_cheap_ratio and trend != 0.0:
            return self._debit_spread(chain, expiry, underlying, trend, width, ratio)
        return None

    # ---- credit vertical (sell premium, defined risk) -----------------
    def _credit_spread(self, chain, expiry, underlying, trend, width, ratio):
        # lean with the trend: bullish/flat -> bull put; bearish -> bear call
        is_put = trend >= 0
        otm = chain.spot - self.cfg.otm_offset_steps * _infer_step(chain.for_expiry(expiry)) \
            if is_put else chain.spot + self.cfg.otm_offset_steps * _infer_step(chain.for_expiry(expiry))
        short = chain.strike_nearest(expiry, otm, is_call=not is_put)
        if short is None:
            return None
        long_strike = short.strike - width if is_put else short.strike + width
        long_ = chain.strike_nearest(expiry, long_strike, is_call=not is_put)
        if long_ is None or long_.symbol == short.symbol:
            return None
        net_credit = short.ltp - long_.ltp
        if net_credit <= 0:
            return None  # no edge to harvest
        strike_dist = abs(short.strike - long_.strike)
        max_loss = max(strike_dist - net_credit, 0.01)
        # parent = short option (we are SHORT it -> direction -1)
        ratio_long = long_.ltp / short.ltp if short.ltp > 0 else 1.0
        return Signal(
            strategy=self.name, symbol=short.symbol, direction=-1.0,
            stop_distance=max_loss,
            legs=(LegSpec(short.symbol, 1.0), LegSpec(long_.symbol, -ratio_long)),
            expected_edge_R=self.cfg.expected_edge_R,
            tag=f"{underlying}:credit:{'P' if is_put else 'C'}",
        )

    # ---- debit vertical (buy premium in trend direction) --------------
    def _debit_spread(self, chain, expiry, underlying, trend, width, ratio):
        is_call = trend > 0
        near = chain.spot  # buy ATM
        bought = chain.strike_nearest(expiry, near, is_call=is_call)
        if bought is None:
            return None
        sell_strike = bought.strike + width if is_call else bought.strike - width
        sold = chain.strike_nearest(expiry, sell_strike, is_call=is_call)
        if sold is None or sold.symbol == bought.symbol:
            return None
        net_debit = bought.ltp - sold.ltp
        if net_debit <= 0:
            return None
        ratio_sold = sold.ltp / bought.ltp if bought.ltp > 0 else 1.0
        return Signal(
            strategy=self.name, symbol=bought.symbol, direction=1.0,
            stop_distance=net_debit,  # max loss on a debit spread = premium paid
            legs=(LegSpec(bought.symbol, 1.0), LegSpec(sold.symbol, -ratio_sold)),
            expected_edge_R=self.cfg.expected_edge_R,
            tag=f"{underlying}:debit:{'C' if is_call else 'P'}",
        )


def _infer_step(quotes) -> float:
    strikes = sorted({q.strike for q in quotes})
    diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return min(diffs) if diffs else 100.0
