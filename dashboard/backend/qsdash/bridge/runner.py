"""Engine runner: drives DecisionEngine against a data source, executes via
the PaperBroker, records everything, heartbeats, and consumes commands.

Data sources:
- ``--synthetic`` : correlated GBM-with-regime bars for the configured
  universe (detail.data_source='synthetic'). Lets the whole stack run with
  honest engine/cost math before the Angel One feed exists.
- ``--replay DIR``: CSV files <SYMBOL>.csv with ts,open,high,low,close,volume.

Live mode is intentionally NOT implemented here: set_mode(live) is rejected by
the CommandConsumer until a real broker adapter lands (next phase). Capital
preservation > feature count.

Run:  python -m qsdash.bridge.runner --synthetic [--speed 0.5]
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from datetime import datetime, timedelta

import numpy as np

from quantsys.config import load_config
from quantsys.core.market_state import MarketState
from quantsys.core.types import Bar, Position, is_session_open
from quantsys.data.history import BarHistory
from quantsys.engine.decision import DecisionEngine

from qsdash.bus import make_sync_publisher
from qsdash.bridge.commands import CommandConsumer
from qsdash.bridge.paper import PaperBroker
from qsdash.bridge.recorder import Recorder
from qsdash.db import SessionLocal, now_ist
from qsdash.models import RuntimeConfig

log = logging.getLogger("qsdash.runner")

DEFAULT_CONFIG = "config/base.yaml"


class Runner:
    def __init__(self, cfg_path: str, mode: str = "paper",
                 paper_capital: float = 1_000_000.0):
        self.cfg = load_config(cfg_path)
        self.cfg_path = cfg_path
        self.mode = mode
        self.engine = DecisionEngine(self.cfg)
        self._all_strategies = list(self.engine.strategies)
        self.publisher = make_sync_publisher(SessionLocal)
        self.recorder = Recorder(mode, self.publisher)
        self.broker = PaperBroker(mode, paper_capital,
                                  self.engine.instruments,
                                  self.engine.cost_model, self.publisher)
        self.commands = CommandConsumer(self, self.publisher)
        self.histories: dict[str, BarHistory] = {
            sym: BarHistory() for sym in self.engine.instruments
        }
        self.deployable_cap_frac: float | None = None
        self.deployable_cap_abs: float | None = None
        self._disabled: set[str] = set()
        self._last_prices: dict[str, float] = {}
        # the paper runner can NEVER trade real money — the live gate sees this
        self.supports_live = False
        self.adapter_connected = False
        self.paused = False              # operator pause: halt decisions, no flatten
        self._load_runtime_config()
        self.broker.load_open_state()
        for sym, pos in self.broker.positions.items():
            log.info("resumed open paper position %s qty=%d", sym, pos.qty)

    # -------------------------------------------------------------- config
    def _load_runtime_config(self) -> None:
        sess = SessionLocal()
        try:
            rc = {r.key: r.value.get("v") for r in sess.query(RuntimeConfig).all()}
        finally:
            sess.close()
        if rc.get("paper_capital") and not self.broker.positions:
            self.broker.cash = float(rc["paper_capital"])
        self.deployable_cap_frac = rc.get("deployable_cap_frac")
        self.deployable_cap_abs = rc.get("deployable_cap_abs")
        self.paused = bool(rc.get("engine_paused", False))  # durable across restart
        for name in [s.name for s in self._all_strategies]:
            enabled = rc.get(f"strategy_enabled.{name}")
            if enabled is False:
                self.set_strategy_enabled(name, False)
        # publish engine config snapshot the dashboard needs (tier ladder)
        self._publish_config_snapshot(sess_rc=rc)

    def _publish_config_snapshot(self, sess_rc: dict) -> None:
        sess = SessionLocal()
        try:
            ladder = [{
                "name": t.name, "min_equity": t.min_equity,
                "max_instruments": t.max_instruments,
                "max_strategies": t.max_strategies,
            } for t in self.cfg.tiers.ladder]
            for key, val in (("engine.tier_ladder", ladder),
                             ("engine.tier_hysteresis", self.cfg.tiers.hysteresis),
                             ("mode", self.mode)):
                row = sess.query(RuntimeConfig).filter(RuntimeConfig.key == key).first()
                if row is None:
                    sess.add(RuntimeConfig(key=key, value={"v": val},
                                           updated_by="engine"))
                else:
                    row.value = {"v": val}
                    row.updated_at = now_ist()
                    row.updated_by = "engine"
            sess.commit()
        finally:
            sess.close()

    # ------------------------------------------------------------- control
    def reset_paper_capital(self, capital: float) -> None:
        self.broker.cash = capital
        self.broker.realized_total = 0.0
        log.info("paper capital reset to %.0f", capital)

    def set_strategy_enabled(self, name: str, enabled: bool) -> None:
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)
        self.engine.strategies = [s for s in self._all_strategies
                                  if s.name not in self._disabled]
        log.info("strategy %s -> %s; active: %s", name, enabled,
                 [s.name for s in self.engine.strategies])

    def apply_config_override(self, key: str, value) -> None:
        """Apply an operator override by rebuilding the engine with the new
        config and round-tripping its state (persistence is bit-identical —
        proven by the core tests)."""
        parts = key.split(".")
        obj = self.cfg
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], type(getattr(obj, parts[-1]))(value))
        state = self.engine.state_dict()
        self.engine = DecisionEngine(self.cfg)
        self._all_strategies = list(self.engine.strategies)
        self.engine.load_state(state)
        self.engine.strategies = [s for s in self._all_strategies
                                  if s.name not in self._disabled]
        self.broker.cost_model = self.engine.cost_model
        self.broker.instruments = self.engine.instruments
        log.info("config override applied: %s = %s (engine rebuilt)", key, value)

    def current_prices(self) -> dict[str, float]:
        return dict(self._last_prices)

    # ---------------------------------------------------------------- loop
    def effective_equity(self, raw_equity: float) -> float:
        """Deployable-capital cap: the engine sizes off min(E, caps)."""
        e = raw_equity
        if self.deployable_cap_frac is not None:
            e = min(e, raw_equity * self.deployable_cap_frac)
        if self.deployable_cap_abs is not None:
            e = min(e, self.deployable_cap_abs)
        return e

    def step(self, ts: datetime, bars: dict[str, Bar],
             data_source: str) -> None:
        """One decision bar: ingest -> post_bar -> decide -> execute -> record."""
        known = {}
        for sym, bar in bars.items():
            hist = self.histories.get(sym)
            if hist is not None:
                hist.append(bar)
                self._last_prices[sym] = bar.close
                known[sym] = bar
        self.recorder.record_bars(self.cfg.engine.decision_bar_minutes, known)

        if self.paused:
            # operator pause: bars recorded (strategies stay warm), but the
            # decision loop is halted — no decide, no execute, NO flatten.
            self.recorder.heartbeat(
                status="paused", market_open=is_session_open(ts),
                detail={"data_source": data_source, "bar_ts": ts.isoformat(),
                        "note": "operator pause: decisions halted, not flattened"})
            self.commands.poll()
            return

        prices = self.current_prices()
        equity = self.broker.equity(prices)
        state = MarketState(
            ts=ts, equity=self.effective_equity(equity),
            bars=self.histories, instruments=self.engine.instruments,
            positions={s: Position(s, p.qty, p.avg_price)
                       for s, p in self.broker.positions.items()},
        )
        self.engine.post_bar(state)
        decision = self.engine.decide(state)
        decision_id = self.recorder.record_decision(decision, self.engine)
        self.broker.execute(decision, decision_id, prices, ts)

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
            detail={"data_source": data_source, "bar_ts": ts.isoformat(),
                    "equity": equity, "n_orders": len(decision.orders)},
        )
        self.commands.poll()


# ------------------------------------------------------------ data sources
def synthetic_bars(symbols: list[str], start: datetime, n_bars: int,
                   bar_minutes: int, seed: int = 7):
    """Correlated GBM with a slow vol-regime cycle. Session-aware timestamps."""
    rng = np.random.default_rng(seed)
    k = len(symbols)
    base = rng.uniform(0.2, 1.0, size=(k, k))
    corr = 0.4 * (base @ base.T)
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    np.fill_diagonal(corr, 1.0)
    chol = np.linalg.cholesky(corr + 1e-9 * np.eye(k))
    prices = rng.uniform(80, 3000, size=k)
    ann_vol = rng.uniform(0.12, 0.35, size=k)
    bar_vol = ann_vol / math.sqrt(252 * 375 / bar_minutes)
    drift = rng.normal(0.02, 0.06, size=k) / (252 * 375 / bar_minutes)

    ts = start.replace(hour=9, minute=15, second=0, microsecond=0)
    for i in range(n_bars):
        if not is_session_open(ts):
            ts = (ts + timedelta(days=1)).replace(hour=9, minute=15)
            while ts.weekday() >= 5:
                ts += timedelta(days=1)
        regime = 1.0 + 1.4 * (0.5 + 0.5 * math.sin(2 * math.pi * i / 2200)) ** 3
        z = chol @ rng.standard_normal(k)
        rets = drift + bar_vol * regime * z
        new_prices = prices * np.exp(rets)
        out: dict[str, Bar] = {}
        for j, sym in enumerate(symbols):
            o, c = prices[j], new_prices[j]
            hi = max(o, c) * (1 + abs(rng.normal(0, 0.0006)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.0006)))
            vol = float(rng.lognormal(11, 0.6))
            out[sym] = Bar(ts=ts, open=float(o), high=float(hi),
                           low=float(lo), close=float(c), volume=vol)
        prices = new_prices
        yield ts, out
        ts += timedelta(minutes=bar_minutes)


def replay_bars(directory: str, symbols: list[str]):
    """CSV replay: <SYMBOL>.csv with header ts,open,high,low,close,volume."""
    import csv
    from pathlib import Path

    streams: dict[str, list[Bar]] = {}
    for sym in symbols:
        f = Path(directory) / f"{sym}.csv"
        if not f.exists():
            continue
        rows = []
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append(Bar(
                    ts=datetime.fromisoformat(r["ts"]), open=float(r["open"]),
                    high=float(r["high"]), low=float(r["low"]),
                    close=float(r["close"]), volume=float(r.get("volume", 0) or 0),
                ))
        streams[sym] = rows
    all_ts = sorted({b.ts for rows in streams.values() for b in rows})
    idx = {sym: 0 for sym in streams}
    for ts in all_ts:
        out = {}
        for sym, rows in streams.items():
            i = idx[sym]
            if i < len(rows) and rows[i].ts == ts:
                out[sym] = rows[i]
                idx[sym] = i + 1
        if out:
            yield ts, out


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--replay", default=None, help="directory of <SYMBOL>.csv")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--speed", type=float, default=0.0,
                    help="seconds of wall-clock per bar (0 = as fast as possible)")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    args = ap.parse_args()

    runner = Runner(args.config, mode="paper", paper_capital=args.capital)
    bar_minutes = runner.cfg.engine.decision_bar_minutes
    symbols = list(runner.engine.instruments)

    if args.replay:
        source, label = replay_bars(args.replay, symbols), "replay"
    else:
        source, label = synthetic_bars(
            symbols, now_ist() - timedelta(days=120), args.bars, bar_minutes
        ), "synthetic"

    log.info("runner starting: mode=%s source=%s symbols=%d capital=%.0f",
             runner.mode, label, len(symbols), runner.broker.cash)
    try:
        for ts, bars in source:
            runner.step(ts, bars, label)
            if args.speed > 0:
                time.sleep(args.speed)
    except KeyboardInterrupt:
        pass
    finally:
        runner.recorder.heartbeat(status="stopped", market_open=False,
                                  detail={"reason": "runner exit"})
        log.info("runner stopped")


if __name__ == "__main__":
    main()
