"""SimBroker — in-memory execution venue for backtests.

Fill model (documented assumptions):
- Order intents fill in full at the decision bar's close price. The engine's
  CostModel already charges half-spread slippage + square-root impact as
  explicit cost lines, so using close as the fill price does not double-count
  microstructure costs.
- Equity equation holds exactly at every bar: equity == cash + MTM. The loop
  asserts it.

This is the canonical simulation accounting. The dashboard's PaperBroker
performs the same average-price bookkeeping but persists rows; keep the two
in behavioural lockstep (test_backtest.py cross-checks the identity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from quantsys.core.types import Decision, Instrument, InstrumentKind, Position
from quantsys.costs import CostModel


@dataclass
class ClosedTrade:
    symbol: str
    strategy: str
    entry_ts: datetime
    exit_ts: datetime
    qty: int                 # signed, as opened
    entry_price: float
    exit_price: float
    pnl: float               # realized, gross of fees
    fees: float              # total fees paid over the round trip


@dataclass
class _OpenLot:
    strategy: str
    entry_ts: datetime
    entry_price: float
    fees: float = 0.0


@dataclass
class SimBroker:
    starting_cash: float
    instruments: dict[str, Instrument]
    cost_model: CostModel

    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[ClosedTrade] = field(default_factory=list)
    total_fees: float = 0.0
    traded_notional: float = 0.0
    n_fills: int = 0
    _lots: dict[str, _OpenLot] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    # ------------------------------------------------------------- marking
    def mtm(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is not None and inst is not None:
                total += pos.qty * px * inst.point_value
        return total

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.mtm(prices)

    def exposures(self, prices: dict[str, float]) -> tuple[float, float]:
        gross = net = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            inst = self.instruments.get(sym)
            if px is None or inst is None:
                continue
            notional = pos.qty * px * inst.point_value
            gross += abs(notional)
            net += notional
        return gross, net

    # ------------------------------------------------------------ execution
    def execute(self, decision: Decision, prices: dict[str, float],
                ts: datetime) -> None:
        for intent in decision.orders:
            qty = intent.qty_delta
            sym = intent.symbol
            inst = self.instruments.get(sym)
            px = prices.get(sym)
            if qty == 0 or inst is None or px is None or not px > 0:
                continue
            cb = self.cost_model.order_cost(
                inst, abs(qty), px, qty > 0,
                delivery=(inst.kind == InstrumentKind.EQUITY),
            )
            self._apply(sym, intent.strategy, qty, px, cb.total, inst, ts)
            self.cash -= qty * px * inst.point_value
            self.cash -= cb.total
            self.total_fees += cb.total
            self.traded_notional += abs(qty) * px * inst.point_value
            self.n_fills += 1

    def _apply(self, sym: str, strategy: str, qty: int, px: float,
               fees: float, inst: Instrument, ts: datetime) -> None:
        pos = self.positions.get(sym)
        if pos is None or pos.qty == 0:
            self.positions[sym] = Position(sym, qty, px)
            self._lots[sym] = _OpenLot(strategy or "", ts, px, fees)
            return

        lot = self._lots.get(sym) or _OpenLot(strategy or "", ts, pos.avg_price)
        if (pos.qty > 0) == (qty > 0):  # add
            new_qty = pos.qty + qty
            pos.avg_price = (pos.avg_price * abs(pos.qty) + px * abs(qty)) / abs(new_qty)
            pos.qty = new_qty
            lot.fees += fees
            return

        was_long = pos.qty > 0
        closing = min(abs(qty), abs(pos.qty))
        realized = closing * (px - pos.avg_price) * inst.point_value * (1 if was_long else -1)
        remaining = pos.qty + qty
        lot.fees += fees

        if remaining == 0 or (remaining > 0) != was_long:
            self.trades.append(ClosedTrade(
                symbol=sym, strategy=lot.strategy, entry_ts=lot.entry_ts,
                exit_ts=ts, qty=pos.qty, entry_price=lot.entry_price,
                exit_price=px, pnl=realized, fees=lot.fees,
            ))
            if remaining == 0:
                self.positions.pop(sym, None)
                self._lots.pop(sym, None)
            else:  # flipped through zero
                self.positions[sym] = Position(sym, remaining, px)
                self._lots[sym] = _OpenLot(strategy or lot.strategy, ts, px)
        else:  # partial close, same side remains
            pos.qty = remaining
            # realized P&L belongs to the eventual ClosedTrade; track via pnl
            # accumulation on the lot by folding into entry price? No — keep
            # simple: emit a partial-close trade record so P&L is never lost.
            self.trades.append(ClosedTrade(
                symbol=sym, strategy=lot.strategy, entry_ts=lot.entry_ts,
                exit_ts=ts, qty=(closing if was_long else -closing),
                entry_price=lot.entry_price, exit_price=px,
                pnl=realized, fees=0.0,
            ))
