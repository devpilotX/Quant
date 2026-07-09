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
from datetime import datetime, timedelta

from quantsys.config import load_config
from quantsys.core.market_state import MarketState
from quantsys.core.types import Bar, InstrumentKind, Position, is_session_open
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

# decision-bar minutes -> Angel candle interval, for startup history warmup
_ANGEL_INTERVAL = {1: "ONE_MINUTE", 3: "THREE_MINUTE", 5: "FIVE_MINUTE",
                   10: "TEN_MINUTE", 15: "FIFTEEN_MINUTE", 30: "THIRTY_MINUTE",
                   60: "ONE_HOUR"}


class LiveRunner:
    def __init__(self, cfg_path: str, mode: str = "paper",
                 broker: AngelOneBroker | None = None,
                 paper_capital: float = 1_000_000.0):
        self.cfg = load_config(cfg_path)
        self.cfg_path = cfg_path
        self.mode = mode
        self._apply_paper_exploration()
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
        self._bar_accum: dict[str, Bar] = {}      # completed bars awaiting the bucket decide
        self._last_step_bucket: datetime | None = None
        self._lock = threading.Lock()
        self.feed = None  # set by main() to the AngelWebSocketFeed (feed health)
        # alert transition state (only alert on state CHANGES, not every bar)
        self._last_kill: str | None = None
        self._last_halted = False
        self._feed_stale = False
        self._started_monotonic = 0.0
        self.aggregator = BarAggregator(self.cfg.engine.decision_bar_minutes,
                                        self._on_completed_bar)
        self.paused = False              # operator pause: halt decisions, no flatten
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
    def _apply_paper_exploration(self) -> None:
        """Paper-ONLY: force a small edge-agnostic Kelly allocation and bypass
        the cost gate so the engine exercises the full order -> fill ->
        reconcile -> P&L path even when no strategy has positive edge after
        costs. Mutates the in-memory config BEFORE the engine is built. Never
        runs in live (mode != 'paper' -> the honest gate stays intact) and never
        in the backtest (which builds the engine directly, not via LiveRunner).
        Toggle with engine.paper_explore in config."""
        if self.mode != "paper" or not self.cfg.engine.paper_explore:
            return
        self.cfg.kelly.explore_floor = max(self.cfg.kelly.explore_floor,
                                           self.cfg.engine.paper_explore_floor)
        self.cfg.sizing.enforce_cost_gate = False
        log.warning("PAPER EXPLORATION ON: kelly.explore_floor=%.3f, cost gate "
                    "bypassed — paper-validation trades only (live/backtest are "
                    "unaffected and stay honest)", self.cfg.kelly.explore_floor)

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
        self.paused = bool(rc.get("engine_paused", False))  # durable across restart
        # Durable paper capital: applied ONLY when the operator value CHANGED
        # (tracked via the paper_capital_applied marker). Re-applying it on
        # every restart silently reset cash to the full float on top of a
        # carried book — a phantom equity jump equal to the open positions'
        # cost basis at every 08:50 recycle, compounding into the study's
        # equity series. Unchanged value => the broker's persisted cash /
        # realized (see PaperBroker.load_open_state) carries the true series.
        pc = rc.get("paper_capital")
        applied = rc.get("paper_capital_applied")
        if pc is not None and pc != applied:
            if isinstance(self.broker, PaperBroker) and self.broker.positions:
                log.error(
                    "durable paper_capital=%s NOT applied: open positions were "
                    "carried across the restart. Flatten first, then re-set — "
                    "keeping persisted cash so the equity series stays honest.", pc)
            else:
                try:
                    self.reset_paper_capital(float(pc))
                    self._set_runtime_key("paper_capital_applied", pc)
                    log.info("durable paper_capital applied (operator change): "
                             "Rs%.0f", float(pc))
                except (TypeError, ValueError):
                    log.warning("ignoring invalid durable paper_capital=%r", pc)
        for name in [s.name for s in self._all_strategies]:
            if rc.get(f"strategy_enabled.{name}") is False:
                self.set_strategy_enabled(name, False)

    def _set_runtime_key(self, key: str, value) -> None:
        sess = SessionLocal()
        try:
            row = sess.query(RuntimeConfig).filter(RuntimeConfig.key == key).first()
            if row is None:
                sess.add(RuntimeConfig(key=key, value={"v": value},
                                       updated_by="engine"))
            else:
                row.value = {"v": value}
                row.updated_at = now_ist()
                row.updated_by = "engine"
            sess.commit()
        finally:
            sess.close()

    # ---------------------------------------------------- control surface
    def current_prices(self) -> dict[str, float]:
        return dict(self._last_prices)

    def reset_paper_capital(self, capital: float) -> None:
        if isinstance(self.broker, PaperBroker):
            self.broker.cash = capital
            self.broker.realized_total = 0.0
            self.broker.persist_cash()

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

        if self.paused:
            # operator pause: bars recorded (strategies stay warm), decision loop
            # halted — no reconcile/decide/execute, NO flatten (distinct from kill).
            self.recorder.heartbeat(
                status="paused", market_open=is_session_open(ts),
                detail={"data_source": "angelone", "venue": self.mode,
                        "bar_ts": ts.isoformat(),
                        "note": "operator pause: decisions halted, not flattened"})
            self.commands.poll()
            return

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

    def _warmup_from_history(self, lookback_bars: int = 2000) -> None:
        """Replay recent Angel history through the engine (post_bar+decide, NO
        execution) so strategies, online edge-stats and the regime HMM are warm
        at the FIRST live bar. Without it the engine starts blind: with only a
        handful of live bars no strategy has its lookback, so it emits 0 signals
        for days (the 2026-06-15 'no trades' symptom). Mirrors the backtest
        warmup (backtest/loop.py). Best-effort — never blocks the engine start.
        """
        bar_min = self.cfg.engine.decision_bar_minutes
        interval = _ANGEL_INTERVAL.get(bar_min)
        if interval is None:
            log.warning("warmup: no Angel interval for %d-min bars; skipping", bar_min)
            return
        days = int(lookback_bars / 75 * 1.7) + 7  # ~75 session bars/day + buffer
        end = now_ist()
        start = end - timedelta(days=days)
        per_sym: dict[str, list] = {}
        for sym in self.instruments:
            try:
                rows = self.broker_adapter.historical_candles(sym, interval, start, end)
                if rows:
                    per_sym[sym] = rows[-lookback_bars:]
            except Exception as e:
                log.warning("warmup fetch %s failed: %s", sym, e)
        if not per_sym:
            log.warning("warmup: no history fetched — engine starts cold")
            return
        # merge per-symbol candles into time-ordered batches (point-in-time)
        all_ts = sorted({r[0] for rows in per_sym.values() for r in rows})
        idx = {s: 0 for s in per_sym}
        n = 0
        for ts in all_ts:
            for s, rows in per_sym.items():
                i = idx[s]
                if i < len(rows) and rows[i][0] == ts:
                    _, o, h, lo, c, v = rows[i]
                    self.histories[s].append(Bar(ts=ts, open=o, high=h, low=lo,
                                                 close=c, volume=v))
                    self._last_prices[s] = c
                    idx[s] = i + 1
            if not is_session_open(ts):
                continue
            state = MarketState(
                ts=ts,
                equity=self.effective_equity(self.broker.equity(self._last_prices)),
                bars=self.histories, instruments=self.instruments,
                positions={s: Position(s, p.qty, getattr(p, "avg_price", 0.0))
                           for s, p in self.broker.positions.items()},
            )
            self.engine.post_bar(state)
            self.engine.decide(state)  # warms stats/regime; orders NOT executed
            n += 1
        log.info("warmup: replayed %d bars across %d instruments (engine ready)",
                 n, len(per_sym))

    def _seed_daily_panels(self) -> None:
        """Give every panel sleeve (factor / reversal / downshock — anything
        exposing ``seed_daily``) its daily history: none of them can derive
        months of daily closes from live intraday bars, so 'enabled' without
        this would silently mean 'no-op for a year'. One broker fetch per
        EQUITY symbol, fanned out to every consumer; best-effort per symbol
        (a missing name just drops out of the breadth count)."""
        sleeves = [s for s in self.engine.strategies if hasattr(s, "seed_daily")]
        if not sleeves:
            return
        days = 400
        for s in sleeves:
            if hasattr(s, "seed_days_needed"):
                days = max(days, int(s.seed_days_needed()))
        end = now_ist()
        start = end - timedelta(days=days)
        n = 0
        for sym, inst in self.instruments.items():
            if inst.kind != InstrumentKind.EQUITY:
                continue
            try:
                rows = self.broker_adapter.historical_candles(sym, "ONE_DAY", start, end)
            except Exception as e:
                log.warning("daily panel seed %s failed: %s", sym, e)
                continue
            if rows:
                for s in sleeves:
                    s.seed_daily(sym, rows)
                n += 1
        # Warmup (run just before this) drove decide() on the still-empty
        # panels, advancing any cadence counters; tell the sleeves seeding is
        # done so cadence-based ones (factor) rebalance on the next live bar
        # instead of staying a no-op until the counter rolls over.
        for s in sleeves:
            try:
                s.on_seeded()
            except Exception as e:  # pragma: no cover - defensive
                log.warning("on_seeded %s failed: %s", s.name, e)
        log.info("daily panels seeded for %d equities across %s",
                 n, [s.name for s in sleeves])

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

    def _absorb_bars(self, batch: dict[str, Bar]) -> None:
        """Record bars into history WITHOUT deciding — for stragglers that
        arrive after their bucket was already decided (or out-of-session
        buckets, e.g. the pre-open aggregation of a stale overnight bar)."""
        for sym, bar in batch.items():
            h = self.histories.get(sym)
            if h is not None:
                h.append(bar)
        self.recorder.record_bars(self.cfg.engine.decision_bar_minutes, batch)

    def _poll_once(self, now: datetime, decide_grace_s: float = 3.0) -> bool:
        """One poll-loop iteration: complete elapsed bars, accumulate, and run
        at most ONE decision — for the bucket, at the bucket timestamp, on the
        full cross-section. Returns True when a decision step ran.

        Bars used to complete per-symbol on the next tick and the old loop
        stepped on every non-empty drain: 3–5 decisions per bar, each on a
        partial universe (the churn + duplicate-decision bug), and no
        close-bar decision at all (nothing crosses 15:30).
        """
        self.aggregator.flush_older(now)
        for sym, bar in self._drain_pending().items():
            self._bar_accum[sym] = bar
        if not self._bar_accum:
            return False

        bucket = max(b.ts for b in self._bar_accum.values())
        older = {s: b for s, b in self._bar_accum.items() if b.ts < bucket}
        if older:
            self._absorb_bars(older)
            self._bar_accum = {s: b for s, b in self._bar_accum.items()
                               if b.ts >= bucket}
        due = now >= bucket + timedelta(minutes=self.cfg.engine.decision_bar_minutes,
                                        seconds=decide_grace_s)
        if not (due and self._bar_accum):
            return False
        batch, self._bar_accum = self._bar_accum, {}
        already = (self._last_step_bucket is not None
                   and bucket <= self._last_step_bucket)
        if already or not is_session_open(bucket):
            self._absorb_bars(batch)
            return False
        # ONE decision per bucket, stamped at the bar ts — the backtest
        # convention, so live and backtest share the same clock.
        self._last_step_bucket = bucket
        self.step(bucket, batch)
        return True

    def run_forever(self, poll_seconds: float = 1.0,
                    decide_grace_s: float = 3.0) -> None:
        log.info("LiveRunner started mode=%s instruments=%d", self.mode,
                 len(self.instruments))
        self._started_monotonic = time.monotonic()
        try:
            self._warmup_from_history()
        except Exception as e:  # best-effort — a cold start is still functional
            log.warning("warmup failed (engine starts cold): %s", e)
        try:
            self._seed_daily_panels()
        except Exception as e:  # panel sleeves then stay breadth-gated no-ops
            log.warning("daily panel seed failed: %s", e)
        self._seed_equity_snapshot()
        notify_alert(self.publisher, severity="info", kind="engine_start",
                     title=f"Engine started ({self.mode})",
                     body=f"instruments={len(self.instruments)} "
                          "data_source=angelone")
        last_heartbeat = 0.0
        while True:
            now = now_ist()
            stepped = self._poll_once(now, decide_grace_s)
            if not stepped:
                # idle (closed market or bucket not due): heartbeat + commands
                if isinstance(self.broker, LiveExecutionBroker):
                    self.broker.pump(self.current_prices())
                if time.monotonic() - last_heartbeat > 10:
                    feed_age = self.feed.seconds_since_tick() if self.feed else None
                    self.recorder.heartbeat(
                        status=("paused" if self.paused else
                                "idle" if not is_session_open(now) else "running"),
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
