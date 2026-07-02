"""Single-decide-per-bar loop + durable paper cash (Forward Study 2 fixes).

The old loop stepped on every non-empty drain — 3–5 decisions per 15-min bar,
each on a partial cross-section, and never a decision on the session's close
bar. And every restart reset paper cash to the durable float on top of a
carried book (phantom equity at each 08:50 recycle). These tests pin the
fixed behaviour.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.conftest import REPO_ROOT

CFG = str(REPO_ROOT / "config" / "base.yaml")


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
    {"symbol": u, "token": str(1000 + i), "exch_seg": "NSE", "lotsize": "1",
     "tick_size": "5"}
    for i, u in enumerate(["SBIN-EQ", "RELIANCE-EQ"])
]


def _rc_cleanup(db, *keys):
    from qsdash.models import RuntimeConfig

    db.query(RuntimeConfig).filter(RuntimeConfig.key.in_(keys)).delete(
        synchronize_session=False)
    db.commit()


@pytest.fixture()
def paper_runner(db):
    """Minimal paper LiveRunner with the mock transport — real engine, real
    PaperBroker, real Recorder against the test DB."""
    import threading

    from quantsys.config import load_config
    from quantsys.data.history import BarHistory
    from quantsys.engine.decision import DecisionEngine
    from quantsys.execution.angelone import AngelOneBroker
    from quantsys.execution.marketdata import BarAggregator
    from qsdash.bridge.commands import CommandConsumer
    from qsdash.bridge.live import LiveRunner
    from qsdash.bridge.paper import PaperBroker
    from qsdash.bridge.recorder import Recorder
    from qsdash.bus import make_sync_publisher
    from qsdash.db import SessionLocal

    _rc_cleanup(db, "paper_broker_state", "paper_capital",
                "paper_capital_applied", "engine_paused")

    adapter = AngelOneBroker(api_key="k", client_code="c", mpin="1",
                             totp_secret="JBSWY3DPEHPK3PXP",
                             transport=_MockTransport(),
                             instrument_master=_MASTER)
    adapter.connect()
    r = LiveRunner.__new__(LiveRunner)
    r.cfg = load_config(CFG)
    r.mode = "paper"
    r.publisher = make_sync_publisher(SessionLocal)
    r.broker_adapter = adapter
    r.instruments = r._merge_instruments(adapter.instruments())
    r.engine = DecisionEngine(r.cfg, instruments=r.instruments)
    r._all_strategies = list(r.engine.strategies)
    r._disabled = set()
    r.recorder = Recorder("paper", r.publisher)
    r.broker = PaperBroker("paper", 1_000_000.0, r.instruments,
                           r.engine.cost_model, r.publisher)
    r.commands = CommandConsumer(r, r.publisher)
    r.histories = {s: BarHistory() for s in r.instruments}
    r._last_prices = {}
    r.deployable_cap_frac = None
    r.deployable_cap_abs = None
    r._pending_bars = {}
    r._bar_accum = {}
    r._last_step_bucket = None
    r._lock = threading.Lock()
    r.feed = None
    r._last_kill = None
    r._last_halted = False
    r._feed_stale = False
    r._started_monotonic = 0.0
    r.paused = False
    r.aggregator = BarAggregator(r.cfg.engine.decision_bar_minutes,
                                 r._on_completed_bar)
    yield r
    _rc_cleanup(db, "paper_broker_state", "paper_capital",
                "paper_capital_applied")


def _count_decides(runner, monkeypatch):
    calls = []
    orig = runner.engine.decide

    def wrapper(state):
        calls.append(state.ts)
        return orig(state)

    monkeypatch.setattr(runner.engine, "decide", wrapper)
    return calls


def test_one_decision_per_bucket_on_full_cross_section(paper_runner, monkeypatch):
    r = paper_runner
    calls = _count_decides(r, monkeypatch)
    d = datetime(2026, 7, 2)
    # ticks land staggered inside the 09:15 bucket (15-min bars)
    r.aggregator.on_tick("SBIN", 1000.0, d.replace(hour=9, minute=16))
    r.aggregator.on_tick("RELIANCE", 1500.0, d.replace(hour=9, minute=17))
    r.aggregator.on_tick("SBIN", 1001.0, d.replace(hour=9, minute=20))

    assert r._poll_once(d.replace(hour=9, minute=29, second=59)) is False
    assert calls == []                                  # window still open
    assert r._poll_once(d.replace(hour=9, minute=30, second=1)) is False
    assert calls == []                                  # flushed, grace pending
    assert r._poll_once(d.replace(hour=9, minute=30, second=4)) is True
    assert calls == [d.replace(hour=9, minute=15)]      # ONE decide, bar-stamped
    # both symbols' bars landed in history before the decide
    assert len(r.histories["SBIN"]) == 1
    assert len(r.histories["RELIANCE"]) == 1
    # idle poll right after: no duplicate decision
    assert r._poll_once(d.replace(hour=9, minute=30, second=5)) is False
    assert len(calls) == 1


def test_straggler_bar_absorbed_without_second_decide(paper_runner, monkeypatch):
    from quantsys.core.types import Bar

    r = paper_runner
    calls = _count_decides(r, monkeypatch)
    d = datetime(2026, 7, 2)
    r.aggregator.on_tick("SBIN", 1000.0, d.replace(hour=9, minute=16))
    assert r._poll_once(d.replace(hour=9, minute=30, second=4)) is True
    assert len(calls) == 1

    # a late emission for the SAME bucket (e.g. thin name's delayed bar)
    r._on_completed_bar("RELIANCE", Bar(
        ts=d.replace(hour=9, minute=15), open=1500.0, high=1501.0,
        low=1499.0, close=1500.5, volume=10.0))
    assert r._poll_once(d.replace(hour=9, minute=30, second=10)) is False
    assert len(calls) == 1                              # absorbed, NOT re-decided
    assert len(r.histories["RELIANCE"]) == 1         # but history caught up


def test_session_close_bar_is_decided(paper_runner, monkeypatch):
    """The 15:15 bar completes only via the time flush (no tick crosses 15:30);
    the decision still runs even though the wall clock is past the close."""
    r = paper_runner
    calls = _count_decides(r, monkeypatch)
    d = datetime(2026, 7, 2)
    r.aggregator.on_tick("SBIN", 1000.0, d.replace(hour=15, minute=16))
    r.aggregator.on_tick("SBIN", 999.0, d.replace(hour=15, minute=29))
    assert r._poll_once(d.replace(hour=15, minute=30, second=4)) is True
    assert calls == [d.replace(hour=15, minute=15)]


def test_out_of_session_bucket_absorbed_not_decided(paper_runner, monkeypatch):
    r = paper_runner
    calls = _count_decides(r, monkeypatch)
    d = datetime(2026, 7, 2)
    # pre-open tick forms an (out-of-session-floored) 09:15 bucket per the
    # aggregator's open-anchor; a bucket stamped OUTSIDE the session (e.g. a
    # stale overnight straggler at 15:30+) must never decide
    from quantsys.core.types import Bar

    r._on_completed_bar("SBIN", Bar(
        ts=d.replace(hour=15, minute=30), open=1.0, high=1.0, low=1.0,
        close=1.0, volume=0.0))
    assert r._poll_once(d.replace(hour=15, minute=45, second=30)) is False
    assert calls == []
    assert len(r.histories["SBIN"]) == 1             # absorbed into history


# ------------------------------------------------------ durable paper cash
def test_paper_cash_and_realized_survive_restart(paper_runner, db):
    r = paper_runner
    r.broker.cash = 987_654.0
    r.broker.realized_total = 4_321.0
    r.broker.persist_cash()

    from qsdash.bridge.paper import PaperBroker

    fresh = PaperBroker("paper", 1_000_000.0, r.instruments,
                        r.engine.cost_model, r.publisher)
    fresh.load_open_state()
    assert fresh.cash == 987_654.0
    assert fresh.realized_total == 4_321.0


def test_durable_capital_applied_once_not_every_restart(paper_runner, db):
    from qsdash.models import RuntimeConfig

    r = paper_runner
    db.add(RuntimeConfig(key="paper_capital", value={"v": 2_000_000.0},
                         updated_by="op"))
    db.commit()

    r._load_runtime_config()
    assert r.broker.cash == 2_000_000.0                 # operator change applied

    # simulate the next 08:50 recycle: persisted cash differs from the float
    r.broker.cash = 1_700_000.0
    r._load_runtime_config()
    assert r.broker.cash == 1_700_000.0                 # NOT reset (marker match)


def test_durable_capital_refused_over_open_positions(paper_runner, db):
    from quantsys.core.types import Position
    from qsdash.models import RuntimeConfig

    r = paper_runner
    db.add(RuntimeConfig(key="paper_capital", value={"v": 3_000_000.0},
                         updated_by="op"))
    db.commit()
    r.broker.cash = 900_000.0
    r.broker.positions["SBIN-EQ"] = Position("SBIN-EQ", 100, 1000.0)

    r._load_runtime_config()
    assert r.broker.cash == 900_000.0                   # refused, series honest
