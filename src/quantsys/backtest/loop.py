"""The backtest event loop: bars -> post_bar -> decide -> SimBroker fills.

Identical orchestrator contract to the live runner (post_bar THEN decide,
fills at the same bar's close). The accounting identity equity == cash + MTM
is asserted every bar — any drift is a bug, not noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator

from quantsys.backtest.simbroker import ClosedTrade, SimBroker
from quantsys.config.schema import AppConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import Bar, Position
from quantsys.data.history import BarHistory
from quantsys.engine.decision import DecisionEngine


@dataclass
class EquitySample:
    ts: datetime
    equity: float
    cash: float
    gross: float
    net: float


@dataclass
class BacktestResult:
    starting_equity: float
    equity_curve: list[EquitySample] = field(default_factory=list)
    trades: list[ClosedTrade] = field(default_factory=list)
    total_fees: float = 0.0
    traded_notional: float = 0.0
    n_fills: int = 0
    n_decisions: int = 0
    n_signals: int = 0
    n_kill_bars: int = 0
    warmup_bars: int = 0
    bar_minutes: int = 5
    final_engine_state: dict | None = None

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1].equity if self.equity_curve else self.starting_equity

    def daily_equity(self) -> list[tuple[datetime, float]]:
        """Last equity per session date (date carried as datetime midnight)."""
        out: dict = {}
        for s in self.equity_curve:
            out[s.ts.date()] = s.equity
        return [(datetime(d.year, d.month, d.day), v) for d, v in sorted(out.items())]


def run_backtest(
    cfg: AppConfig,
    bars: Iterable[tuple[datetime, dict[str, Bar]]],
    starting_equity: float,
    warmup_bars: int = 0,
    engine: DecisionEngine | None = None,
    broker: SimBroker | None = None,
    score_from: datetime | None = None,
) -> BacktestResult:
    """Run the engine over a bar stream.

    warmup_bars: initial bars where signals are ingested (post_bar/decide run,
    keeping the engine's online stats live-faithful) but orders are NOT
    executed — the book stays flat while estimators converge. Equity sampling
    starts after warmup.
    score_from: alternatively, execute/score only from this timestamp.
    engine/broker: pass existing instances to continue a session (walk-forward
    with carried state); fresh ones are built when omitted.
    """
    engine = engine or DecisionEngine(cfg)
    broker = broker or SimBroker(
        starting_cash=starting_equity,
        instruments=engine.instruments,
        cost_model=engine.cost_model,
    )
    histories: dict[str, BarHistory] = {s: BarHistory() for s in engine.instruments}
    # continuing a session: keep prior histories if the engine carries them
    if getattr(engine, "_bt_histories", None) is not None:
        histories = engine._bt_histories  # type: ignore[attr-defined]
    engine._bt_histories = histories  # type: ignore[attr-defined]

    res = BacktestResult(starting_equity=starting_equity,
                         warmup_bars=warmup_bars,
                         bar_minutes=cfg.engine.decision_bar_minutes)
    last_prices: dict[str, float] = {}
    fees0, notional0, fills0 = broker.total_fees, broker.traded_notional, broker.n_fills
    trades0 = len(broker.trades)
    i = -1

    for ts, batch in bars:
        i += 1
        for sym, bar in batch.items():
            h = histories.get(sym)
            if h is not None:
                h.append(bar)
                last_prices[sym] = bar.close

        in_warmup = (i < warmup_bars) or (score_from is not None and ts < score_from)
        equity = broker.equity(last_prices)
        state = MarketState(
            ts=ts, equity=equity, bars=histories,
            instruments=engine.instruments,
            positions={s: Position(s, p.qty, p.avg_price)
                       for s, p in broker.positions.items()},
        )
        engine.post_bar(state)
        decision = engine.decide(state)
        res.n_decisions += 1
        res.n_signals += len(decision.signals)
        if decision.kill_reason is not None:
            res.n_kill_bars += 1

        if not in_warmup:
            broker.execute(decision, last_prices, ts)
            equity = broker.equity(last_prices)
            # accounting identity — hard invariant
            mtm = broker.mtm(last_prices)
            assert abs(equity - (broker.cash + mtm)) < 1e-6, "equity != cash + MTM"
            gross, net = broker.exposures(last_prices)
            res.equity_curve.append(EquitySample(ts, equity, broker.cash, gross, net))

    res.trades = broker.trades[trades0:]
    res.total_fees = broker.total_fees - fees0
    res.traded_notional = broker.traded_notional - notional0
    res.n_fills = broker.n_fills - fills0
    res.final_engine_state = engine.state_dict()
    return res
