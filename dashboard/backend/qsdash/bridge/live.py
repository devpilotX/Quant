"""LiveRunner — the 24x7 live/paper-on-live-data orchestrator.

Wires: AngelOneBroker (data + execution) -> BarAggregator -> DecisionEngine ->
LiveExecutionBroker (OMS) -> Recorder, with reconciliation every cycle and
market-hours gating. Shares the Recorder, engine, and CommandConsumer with the
paper runner; the difference is the venue and that real ticks drive the clock.

Modes:
- mode='paper'  : real live data feed, but LiveExecutionBroker is NOT used —
                  a PaperBroker fills locally. This is "paper-on-VPS", the
                  first test of the feed/postback/reconcile plumbing.
- mode='live'   : real orders, gated by livegate (adapter + QS_LIVE_ARMED +
                  passed real backtest). Refused otherwise.

Run:  QS_LIVE_ARMED=0 python -m qsdash.bridge.live --paper      # paper on live data
      QS_LIVE_ARMED=1 python -m qsdash.bridge.live --live       # real money (gated)

Real-money trading requires rotated credentials, the VPS whitelisted IP, and a
passed real-data backtest — see docs/GOLIVE.md. This module is complete and
tested with a mock transport; it has NOT been run against the live broker.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from datetime import datetime

from quantsys.config import load_config
from quantsys.core.market_state import MarketState
from quantsys.core.types import Bar, Position, is_session_open
from quantsys.data.history import BarHistory
from quantsys.engine.decision import DecisionEngine
from quantsys.execution.angelone import AngelOneBroker
from quantsys.execution.broker import BrokerError
from quantsys.execution.marketdata import BarAggregator

from qsdash.audit import notify_alert
from qsdash.bridge.commands import CommandConsumer
from qsdash.bridge.livebroker import LiveExecutionBroker
from qsdash.bridge.paper import PaperBroker
from qsdash.bridge.recorder import Recorder
from qsdash.bus import make_sync_publisher
from qsdash.db import SessionLocal, now_ist
from qsdash.models import RuntimeConfig

log = logging.getLogger("qsdash.live")

FEED_STALE_S = 90  # no ticks for this long during market hours => alert


class LiveRunner:
    def __init__(self, cfg_path: str, mode: str = "paper",
                 broker: AngelOneBroker | None = None,
                 paper_capital: float = 1_000_000.0):
        self.cfg = load_config(cfg_path)
        self.cfg_path = cfg_path
        self.mode = mode
        self.publisher = make_sync_publisher(SessionLocal)

        self.broker_adapter = broker or AngelOneBroker()
        self.broker_adapter.connect()
        # instrument master is the live source of truth for lot/tick/token;
        # config values are only warm-start fallbacks (merge: master wins,
        # fall back to config for fields the master lacks like point_value/adv)
        self.instruments = self._merge_instruments(self.broker_adapter.instruments())
        self.engine = DecisionEngine(self.cfg, instruments=self.instruments)
        self._all_strategies = list(self.engine.strategies)
        self._disabled: set[str] = set()

        self.recorder = Recorder(mode, self.publisher)
        if mode == "live":
            self.broker = LiveExecutionBroker(self.broker_adapter,
                                              self.instruments, self.publisher)
        else:
            self.broker = PaperBroker(mode, paper_capital, self.instruments,
                                      self.engine.cost_model, self.publisher)
            self.broker.load_open_state()
        self.commands = CommandConsumer(self, self.publisher)

        self.histories: dict[str, BarHistory] = {s: BarHistory() for s in self.instruments}
        self._last_prices: dict[str, float] = {}
        self.deployable_cap_frac: float | None = None
        self.deployable_cap_abs: float | None = None
        self._pending_bars: dict[str, Bar] = {}
        self._lock = threading.Lock()
        self.feed = None  # set by main() to the AngelWebSocketFeed (feed health)
        # alert transition state (only alert on state CHANGES, not every bar)
        self._last_kill: str | None = None
        self._last_halted = False
        self._feed_stale = False
        self._started_monotonic = 0.0
        self.aggregator = BarAggregator(self.cfg.engine.decision_bar_minutes,
                                        self._on_completed_bar)
        self._publish_config_snapshot()
        self._load_runtime_config()

    # ----------------------------------------------------- gate properties
    @property
    def supports_live(self) -> bool:
        return isinstance(self.broker, LiveExecutionBroker)

    @property
    def adapter_connected(self) -> bool:
        try:
            return self.broker_adapter.is_connected()
        except Exception:
            return False

    # ------------------------------------------------------------- config
    def _merge_instruments(self, master: dict) -> dict:
        from quantsys.core.types import Instrument
        out = {}
        cfg_by_sym = {u.symbol: u for u in self.cfg.universe}
        for sym, u in cfg_by_sym.items():
            m = master.get(sym)
            if m is not None:
                out[sym] = Instrument(
                    symbol=sym, token=m.token or u.token, exchange=m.exchange,
                    kind=m.kind, lot_size=m.lot_size, tick_size=m.tick_size,
                    point_value=u.point_value, sector=u.sector, adv=u.adv,
                    margin_rate=u.margin_rate,
                )
            else:
                log.warning("instrument %s not in master — using config fallback", sym)
                out[sym] = Instrument(
                    symbol=u.symbol, token=u.token, exchange=u.exchange, kind=u.kind,
                    lot_size=u.lot_size, tick_size=u.tick_size, point_value=u.point_value,
                    sector=u.sector, adv=u.adv, margin_rate=u.margin_rate,
                )
        return out

    def _publish_config_snapshot(self) -> None:
        sess = SessionLocal()
        try:
            ladder = [{"name": t.name, "min_equity": t.min_equity,
                       "max_instruments": t.max_instruments,
                       "max_strategies": t.max_strategies}
                      for t in self.cfg.tiers.ladder]
            for key, val in (("engine.tier_ladder", ladder),
                             ("engine.tier_hysteresis", self.cfg.tiers.hysteresis),
                             ("mode", self.mode)):
                row = sess.query(RuntimeConfig).filter(RuntimeConfig.key == key).first()
                if row is None:
                    sess.add(RuntimeConfig(key=key, value={"v": val}, updated_by="engine"))
                else:
                    row.value = {"v": val}
                    row.updated_at = now_ist()
            sess.commit()
        finally:
            sess.close()

    def _load_runtime_config(self) -> None:
        sess = SessionLocal()
        try:
            rc = {r.key: r.value.get("v") for r in sess.query(RuntimeConfig).all()}
        finally:
            sess.close()
        self.deployable_cap_frac = rc.get("deployable_cap_frac")
        self.deployable_cap_abs = rc.get("deployable_cap_abs")
        for name in [s.name for s in self._all_strategies]:
            if rc.get(f"strategy_enabled.{name}") is False:
                self.set_strategy_enabled(name, False)

    # ---------------------------------------------------- control surface
    def current_prices(self) -> dict[str, float]:
        return dict(self._last_prices)

    def reset_paper_capital(self, capital: float) -> None:
        if isinstance(self.broker, PaperBroker):
            self.broker.cash = capital
            self.broker.realized_total = 0.0

    def set_strategy_enabled(self, name: str, enabled: bool) -> None:
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)
        self.engine.strategies = [s for s in self._all_strategies
                                  if s.name not in self._disabled]

    def apply_config_override(self, key: str, value) -> None:
        parts = key.split(".")
        obj = self.cfg
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], type(getattr(obj, parts[-1]))(value))
        state = self.engine.state_dict()
        self.engine = DecisionEngine(self.cfg, instruments=self.instruments)
        self._all_strategies = list(self.engine.strategies)
        self.engine.load_state(state)
        self.engine.strategies = [s for s in self._all_strategies
                                  if s.name not in self._disabled]

    def effective_equity(self, raw_equity: float) -> float:
        e = raw_equity
        if self.deployable_cap_frac is not None:
            e = min(e, raw_equity * self.deployable_cap_frac)
        if self.deployable_cap_abs is not None:
            e = min(e, self.deployable_cap_abs)
        return e

    # ------------------------------------------------------- bar handling
    def _on_completed_bar(self, symbol: str, bar: Bar) -> None:
        """Called by the aggregator thread when a symbol's bar completes."""
        with self._lock:
            self._pending_bars[symbol] = bar
            self._last_prices[symbol] = bar.close

    def _drain_pending(self) -> dict[str, Bar]:
        with self._lock:
            batch = self._pending_bars
            self._pending_bars = {}
            return batch

    def step(self, ts: datetime, batch: dict[str, Bar]) -> None:
        """One decision cycle. Reconcile FIRST (freeze on mismatch), then
        decide, execute, record."""
        for sym, bar in batch.items():
            h = self.histories.get(sym)
            if h is not None:
                h.append(bar)
        self.recorder.record_bars(self.cfg.engine.decision_bar_minutes, batch)

        prices = self.current_prices()

        # reconciliation: broker is ground truth; mismatch => freeze (no orders)
        if isinstance(self.broker, LiveExecutionBroker):
            internal = {s: p.qty for s, p in self.broker.positions.items()}
            ok, mismatches = self.broker.reconcile(internal)
            if not ok:
                self.engine.risk.reconcile(internal, self.broker.positions_map())

        equity = self.broker.equity(prices)
        state = MarketState(
            ts=ts, equity=self.effective_equity(equity), bars=self.histories,
            instruments=self.instruments,
            positions={s: Position(s, p.qty, getattr(p, "avg_price", 0.0))
                       for s, p in self.broker.positions.items()},
        )
        self.engine.post_bar(state)
        decision = self.engine.decide(state)
        decision_id = self.recorder.record_decision(decision, self.engine)
        self._alert_on_decision(decision)
        try:
            self.broker.execute(decision, decision_id, prices, ts)
        except BrokerError as e:
            log.error("execute failed (fail-safe: no new risk this bar): %s", e)

        prices = self.current_prices()
        gross, net = self.broker.exposures(prices)
        self.recorder.record_equity(
            ts, equity=self.broker.equity(prices), cash=self.broker.cash,
            mtm=self.broker.mtm(prices), gross=gross, net=net,
            realized=self.broker.realized_total,
            unrealized=self.broker.unrealized(prices),
        )
        status = ("killed" if decision.kill_reason else
                  "halted" if decision.halted else "running")
        self.recorder.heartbeat(
            status=status, market_open=is_session_open(ts),
            detail={"data_source": "angelone", "venue": self.mode,
                    "bar_ts": ts.isoformat(), "n_orders": len(decision.orders)},
        )
        self.commands.poll()

    # --------------------------------------------------------------- loop
    def _alert_on_decision(self, decision) -> None:
        """Raise an alert on a risk-state TRANSITION (kill priority over halt).
        Best-effort; notify_alert swallows its own errors."""
        kr = decision.kill_reason
        if kr and kr != self._last_kill:
            notify_alert(self.publisher, severity="crit", kind="kill",
                         title=f"KILL: {kr}",
                         body=f"{self.mode}: new risk blocked / positions flattened")
        elif not kr and decision.halted and not self._last_halted:
            notify_alert(self.publisher, severity="crit", kind="halted",
                         title="Engine halted (risk veto)",
                         body=f"{self.mode}: no new risk this bar")
        elif (not kr and not decision.halted
              and (self._last_kill or self._last_halted)):
            notify_alert(self.publisher, severity="info", kind="recovered",
                         title="Risk state cleared — engine running",
                         body=self.mode)
        self._last_kill = kr
        self._last_halted = decision.halted

    def _alert_on_feed(self, now: datetime) -> None:
        """Alert on a market-data feed stale/recovery transition during hours."""
        if self.feed is None or not is_session_open(now):
            return
        age = self.feed.seconds_since_tick()
        if age is not None:
            stale = age > FEED_STALE_S
        else:  # never ticked: stale only after a grace period from start
            stale = (time.monotonic() - self._started_monotonic) > FEED_STALE_S
        if stale and not self._feed_stale:
            notify_alert(self.publisher, severity="warn", kind="feed_stale",
                         title="Market-data feed stale",
                         body=f"{self.mode}: no ticks for "
                              f"{round(age) if age is not None else '∞'}s; "
                              "auto-reconnecting")
            self._feed_stale = True
        elif not stale and self._feed_stale:
            notify_alert(self.publisher, severity="info", kind="feed_ok",
                         title="Market-data feed recovered", body=self.mode)
            self._feed_stale = False

    def _seed_equity_snapshot(self) -> None:
        """Write one equity point at startup so the dashboard shows starting
        capital straight away instead of 'no data' until the first bar
        completes (up to a full bar after a restart)."""
        try:
            prices = self.current_prices()
            gross, net = self.broker.exposures(prices)
            self.recorder.record_equity(
                now_ist(), equity=self.broker.equity(prices),
                cash=self.broker.cash, mtm=self.broker.mtm(prices),
                gross=gross, net=net, realized=self.broker.realized_total,
                unrealized=self.broker.unrealized(prices),
            )
        except Exception as e:  # pragma: no cover - best-effort seed
            log.warning("initial equity snapshot failed: %s", e)

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        log.info("LiveRunner started mode=%s instruments=%d", self.mode,
                 len(self.instruments))
        self._started_monotonic = time.monotonic()
        self._seed_equity_snapshot()
        notify_alert(self.publisher, severity="info", kind="engine_start",
                     title=f"Engine started ({self.mode})",
                     body=f"instruments={len(self.instruments)} "
                          "data_source=angelone")
        last_heartbeat = 0.0
        while True:
            now = now_ist()
            batch = self._drain_pending()
            if batch and is_session_open(now):
                self.step(now, batch)
            else:
                # idle (closed market or no completed bar): heartbeat + commands
                if isinstance(self.broker, LiveExecutionBroker):
                    self.broker.pump(self.current_prices())
                if time.monotonic() - last_heartbeat > 10:
                    feed_age = self.feed.seconds_since_tick() if self.feed else None
                    self.recorder.heartbeat(
                        status="idle" if not is_session_open(now) else "running",
                        market_open=is_session_open(now),
                        detail={"venue": self.mode, "data_source": "angelone",
                                "feed_age_s": (round(feed_age, 1)
                                               if feed_age is not None else None)})
                    self._alert_on_feed(now)
                    last_heartbeat = time.monotonic()
                self.commands.poll()
            time.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/base.yaml")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="paper on live data (default)")
    g.add_argument("--live", action="store_true", help="REAL MONEY (gated)")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    args = ap.parse_args()

    mode = "live" if args.live else "paper"
    runner = LiveRunner(args.config, mode=mode, paper_capital=args.capital)

    # start the websocket feed
    adapter = runner.broker_adapter
    from quantsys.execution.marketdata import AngelWebSocketFeed

    tokens_by_exchange: dict[str, list[str]] = {}
    token_to_symbol: dict[str, str] = {}
    for sym, inst in runner.instruments.items():
        if inst.token:
            tokens_by_exchange.setdefault(inst.exchange, []).append(inst.token)
            token_to_symbol[inst.token] = sym
    feed = AngelWebSocketFeed(
        auth_token=getattr(adapter._transport, "access_token", ""),
        api_key=adapter.api_key, client_code=adapter.client_code,
        feed_token=adapter._feed_token, tokens_by_exchange=tokens_by_exchange,
        aggregator=runner.aggregator, token_to_symbol=token_to_symbol,
        reauth=adapter.reconnect_feed_session,
    )
    runner.feed = feed
    feed.start()
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        runner.recorder.heartbeat(status="stopped", market_open=False,
                                  detail={"reason": "live runner exit"})
        feed.stop()


if __name__ == "__main__":
    main()
