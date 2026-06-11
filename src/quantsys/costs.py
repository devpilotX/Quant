"""Indian market transaction-cost model.

Rates are config-driven; defaults reflect the verified schedule as of
June 2026 (Budget 2026 STT hike effective 2026-04-01):

- STT: futures 0.05% (sell), options 0.15% of premium (sell),
  equity delivery 0.1% (both sides), equity intraday 0.025% (sell).
- NSE transaction charges: equity ~0.0030699%, futures ~0.0018299%,
  options ~0.03552% (on premium).
- Angel One brokerage: equity delivery 0, otherwise min(Rs 20, 0.25%) per order.
- GST 18% on (brokerage + exchange txn + SEBI fees); SEBI Rs 10/crore.
- Stamp duty (buy side only): delivery 0.015%, intraday 0.003%,
  futures 0.002%, options 0.003%.

Slippage is a per-kind spread-cost estimate; market impact uses a square-root
model  impact = coeff * sigma_daily * sqrt(qty / ADV)  charged on notional —
this is what makes the ADV constraint bind economically at T5/T6, not just as
a hard cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from quantsys.core.types import Instrument, InstrumentKind


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    slippage: float
    impact: float

    @property
    def total(self) -> float:
        return (
            self.brokerage + self.stt + self.exchange_txn + self.sebi
            + self.stamp + self.gst + self.slippage + self.impact
        )


class CostModel:
    def __init__(self, cfg) -> None:  # cfg: config.schema.CostConfig
        self.cfg = cfg

    def order_cost(
        self,
        inst: Instrument,
        qty: int,
        price: float,
        is_buy: bool,
        delivery: bool = False,
        sigma_daily: float | None = None,
    ) -> CostBreakdown:
        """All-in cost of one order of |qty| units at `price`."""
        cfg = self.cfg
        notional = abs(qty) * price * inst.point_value
        kind = inst.kind

        if kind == InstrumentKind.EQUITY and delivery:
            brokerage = cfg.brokerage_delivery_flat
        else:
            brokerage = min(cfg.brokerage_flat, cfg.brokerage_pct * notional)

        if kind == InstrumentKind.FUTURE:
            stt = cfg.stt_future_sell * notional if not is_buy else 0.0
            exchange = cfg.exch_future * notional
            stamp = cfg.stamp_future * notional if is_buy else 0.0
        elif kind == InstrumentKind.OPTION:
            stt = cfg.stt_option_sell * notional if not is_buy else 0.0
            exchange = cfg.exch_option * notional
            stamp = cfg.stamp_option * notional if is_buy else 0.0
        else:  # EQUITY
            if delivery:
                stt = cfg.stt_delivery * notional
                stamp = cfg.stamp_delivery * notional if is_buy else 0.0
            else:
                stt = cfg.stt_intraday_sell * notional if not is_buy else 0.0
                stamp = cfg.stamp_intraday * notional if is_buy else 0.0
            exchange = cfg.exch_equity * notional

        sebi = cfg.sebi_rate * notional
        gst = cfg.gst * (brokerage + exchange + sebi)
        slippage = cfg.slippage_bps.get(kind.value, 5.0) / 1e4 * notional

        impact = 0.0
        if inst.adv and sigma_daily and inst.adv > 0:
            impact = cfg.impact_coeff * sigma_daily * ((abs(qty) / inst.adv) ** 0.5) * notional

        return CostBreakdown(brokerage, stt, exchange, sebi, stamp, gst, slippage, impact)

    def round_trip(
        self,
        inst: Instrument,
        qty: int,
        price: float,
        delivery: bool = False,
        sigma_daily: float | None = None,
    ) -> float:
        """Conservative all-in cost of entering and exiting |qty| at `price`."""
        buy = self.order_cost(inst, qty, price, is_buy=True, delivery=delivery, sigma_daily=sigma_daily)
        sell = self.order_cost(inst, qty, price, is_buy=False, delivery=delivery, sigma_daily=sigma_daily)
        return buy.total + sell.total
