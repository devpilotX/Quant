"""LiveRunner mechanics with a mock Angel transport: bar aggregation,
paper-on-live-data step loop, and the engine-side live-gate refusal through
the command consumer."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from quantsys.execution.marketdata import BarAggregator, floor_to_bucket
from tests.conftest import REPO_ROOT

CFG = str(REPO_ROOT / "config" / "base.yaml")


# ------------------------------------------------------- bar aggregation
def test_floor_to_bucket_aligns_to_session_open():
    ts = datetime(2026, 6, 12, 9, 17, 30)
    assert floor_to_bucket(ts, 5) == datetime(2026, 6, 12, 9, 15)
    ts2 = datetime(2026, 6, 12, 9, 21)
    assert floor_to_bucket(ts2, 5) == datetime(2026, 6, 12, 9, 20)


def test_aggregator_emits_on_bucket_cross():
    emitted = []
    agg = BarAggregator(5, lambda s, b: emitted.append((s, b)))
    base = datetime(2026, 6, 12, 9, 15)
    agg.on_tick("X", 100.0, base + timedelta(seconds=10))
    agg.on_tick("X", 102.0, base + timedelta(seconds=60))
    agg.on_tick("X", 101.0, base + timedelta(seconds=120))
    assert emitted == []  # still in first bucket
    agg.on_tick("X", 105.0, base + timedelta(minutes=5, seconds=1))  # cross
    assert len(emitted) == 1
    sym, bar = emitted[0]
    assert sym == "X"
    assert bar.open == 100.0 and bar.high == 102.0 and bar.low == 100.0 and bar.close == 101.0


def test_aggregator_volume_is_delta_of_cumulative():
    emitted = []
    agg = BarAggregator(5, lambda s, b: emitted.append(b))
    base = datetime(2026, 6, 12, 9, 15)
    agg.on_tick("X", 100.0, base, cum_volume=1000)
    agg.on_tick("X", 101.0, base + timedelta(seconds=30), cum_volume=1500)
    agg.on_tick("X", 102.0, base + timedelta(minutes=5, seconds=1), cum_volume=1800)
    assert emitted[0].volume == 500  # 1500 - 1000 within the bar


# ------------------------------------- live gate refusal via command queue
class _MockTransport:
    def generateSession(self, c, m, t):
        return {"status": True, "data": {"refreshToken": "rt"}}

    def getfeedToken(self):
        return "ft"

    def rmsLimit(self):
        return {"data": {"availablecash": "1000000"}}

    def position(self):
        return {"data": []}

    def orderBook(self):
        return {"data": []}


_MASTER = [
    {"symbol": u, "token": str(1000 + i), "exch_seg": "NSE", "lotsize": "1", "tick_size": "5"}
    for i, u in enumerate(["SBIN-EQ", "RELIANCE-EQ"])
]


@pytest.fixture()
def live_runner(db, monkeypatch):
    monkeypatch.setenv("QS_LIVE_ARMED", "1")
    from quantsys.execution.angelone import AngelOneBroker
    from qsdash.bridge.live import LiveRunner

    adapter = AngelOneBroker(api_key="k", client_code="c", mpin="1",
                             totp_secret="JBSWY3DPEHPK3PXP",
                             transport=_MockTransport(), instrument_master=_MASTER)
    # tiny universe so the merge keeps only the two mocked instruments present
    r = LiveRunner.__new__(LiveRunner)
    # build minimally to exercise the gate path without the full feed
    from quantsys.config import load_config
    r.cfg = load_config(CFG)
    r.mode = "live"
    from qsdash.bus import make_sync_publisher
    from qsdash.db import SessionLocal
    r.publisher = make_sync_publisher(SessionLocal)
    r.broker_adapter = adapter
    adapter.connect()
    r.instruments = r._merge_instruments(adapter.instruments())
    from quantsys.engine.decision import DecisionEngine
    r.engine = DecisionEngine(r.cfg, instruments=r.instruments)
    r._all_strategies = list(r.engine.strategies)
    r._disabled = set()
    from qsdash.bridge.livebroker import LiveExecutionBroker
    r.broker = LiveExecutionBroker(adapter, r.instruments, r.publisher)
    from qsdash.bridge.commands import CommandConsumer
    r.commands = CommandConsumer(r, r.publisher)
    return r


def test_live_runner_reports_adapter_present(live_runner):
    assert live_runner.supports_live is True
    assert live_runner.adapter_connected is True


def test_set_mode_live_refused_without_passing_backtest(live_runner, db):
    """Even fully armed with a connected adapter, live is refused while only
    synthetic (or no) backtests exist."""
    from qsdash.models import BacktestRun, Command

    db.query(BacktestRun).delete()
    db.add(BacktestRun(label="syn", metrics={"is_synthetic": True}))
    db.commit()

    db.add(Command(created_by="t", kind="set_mode", payload={"target_mode": "live"}))
    db.commit()
    live_runner.mode = "paper"  # so target 'live' is a real transition
    live_runner.commands.poll()

    cmd = db.query(Command).filter(Command.kind == "set_mode").order_by(Command.id.desc()).first()
    assert cmd.status == "rejected"
    assert "gate refused" in cmd.result["reason"]
    db.query(BacktestRun).delete()
    db.commit()


def test_set_mode_live_allowed_with_passing_backtest(live_runner, db):
    from qsdash.models import BacktestRun, Command, RuntimeConfig

    db.query(BacktestRun).delete()
    db.add(BacktestRun(label="real", metrics={
        "is_synthetic": False, "sharpe_oos": 1.4, "sharpe_deflated": 0.98,
        "monte_carlo": {"p_sharpe_negative": 0.02},
    }))
    db.commit()

    db.add(Command(created_by="t", kind="set_mode", payload={"target_mode": "live"}))
    db.commit()
    live_runner.mode = "paper"  # so the transition is a real change
    live_runner.commands.poll()

    cmd = db.query(Command).filter(Command.kind == "set_mode").order_by(Command.id.desc()).first()
    assert cmd.status == "done", cmd.result
    assert cmd.result.get("mode") == "live"
    db.query(BacktestRun).delete()
    db.commit()
