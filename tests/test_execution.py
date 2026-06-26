"""Execution layer: rate limiter, OMS lifecycle + idempotency + slicing,
reconciliation freeze, Angel One adapter against a mock transport."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pytest

from quantsys.core.types import (
    ExecutionStyle,
    Instrument,
    InstrumentKind,
    OrderIntent,
    Urgency,
    now_ist,
)
from quantsys.execution.angelone import AngelOneBroker
from quantsys.execution.broker import BrokerError, BrokerOrder
from quantsys.execution.oms import OMS
from quantsys.execution.ratelimit import TokenBucket
from quantsys.execution.reconcile import reconcile_positions


# ----------------------------------------------------------- rate limiter
def test_token_bucket_blocks_then_refills():
    tb = TokenBucket(rate_per_sec=10, capacity=2)
    assert tb.try_acquire()
    assert tb.try_acquire()
    assert not tb.try_acquire()       # bucket empty
    assert tb.acquire(timeout=1.0)    # blocks ~0.1s then succeeds


def test_token_bucket_timeout_returns_false():
    tb = TokenBucket(rate_per_sec=1, capacity=1)
    assert tb.try_acquire()
    t0 = time.monotonic()
    assert not tb.acquire(timeout=0.2)
    assert time.monotonic() - t0 >= 0.2


# --------------------------------------------------------------- fakes
class FakeTransport:
    """Minimal SmartConnect stand-in."""

    def __init__(self):
        self.placed = []
        self._seq = 0
        self._book = {}
        self.candle_calls = []

    def generateSession(self, code, mpin, totp):
        return {"status": True, "data": {"refreshToken": "rt"}}

    def getfeedToken(self):
        return "ft"

    def rmsLimit(self):
        return {"data": {"availablecash": "1500000"}}

    def position(self):
        return {"data": [{"tradingsymbol": "SBIN-EQ", "netqty": "10", "netprice": "550"}]}

    def orderBook(self):
        return {"data": list(self._book.values())}

    def placeOrder(self, params):
        self._seq += 1
        oid = f"BRK{self._seq}"
        self.placed.append(params)
        self._book[oid] = {
            "orderid": oid, "ordertag": params["ordertag"],
            "tradingsymbol": params["tradingsymbol"],
            "transactiontype": params["transactiontype"],
            "quantity": params["quantity"], "status": "open",
            "filledshares": "0", "averageprice": "0",
        }
        return {"data": {"orderid": oid}}

    def cancelOrder(self, oid, variety):
        self._book.pop(oid, None)
        return {"status": True}

    def getCandleData(self, params):
        # one synthetic bar stamped at the window start (IST +05:30), so a
        # multi-chunk fetch yields one distinct bar per request window
        self.candle_calls.append(dict(params))
        frm = params["fromdate"].replace(" ", "T") + ":00+05:30"
        return {"data": [[frm, 100.0, 101.0, 99.5, 100.5, 1234]]}


MASTER = [
    {"symbol": "SBIN-EQ", "token": "3045", "exch_seg": "NSE", "lotsize": "1", "tick_size": "5"},
    {"symbol": "NIFTY28AUG25FUT", "token": "53001", "exch_seg": "NFO", "lotsize": "75", "tick_size": "5"},
]


@pytest.fixture()
def broker():
    b = AngelOneBroker(api_key="k", client_code="c", mpin="1234",
                       totp_secret="JBSWY3DPEHPK3PXP", transport=FakeTransport(),
                       instrument_master=MASTER)
    b.connect()
    return b


# --------------------------------------------------------- adapter tests
def test_connect_loads_instruments(broker):
    assert broker.is_connected()
    insts = broker.instruments()
    assert "SBIN-EQ" in insts
    assert insts["NIFTY28AUG25FUT"].lot_size == 75
    assert insts["NIFTY28AUG25FUT"].kind == InstrumentKind.FUTURE
    assert insts["SBIN-EQ"].tick_size == 0.05


def test_index_resolves_to_amxidx_candle_token():
    """Regression: config 'NIFTY' must resolve to the AMXIDX index token
    (99926000, which returns candles), NOT the bare spot row (26000, returns
    none) — the bug that starved the regime HMM and left it stuck in `warmup`."""
    master = [
        {"symbol": "NIFTY", "name": "NIFTY", "token": "26000", "exch_seg": "NSE",
         "instrumenttype": "", "lotsize": "1", "tick_size": "5"},
        {"symbol": "Nifty 50", "name": "NIFTY", "token": "99926000", "exch_seg": "NSE",
         "instrumenttype": "AMXIDX", "lotsize": "1", "tick_size": "5"},
        # decoy: same index name on the currency segment with a token that
        # returns no NSE candles — must NOT win.
        {"symbol": "NIFTY", "name": "NIFTY", "token": "2", "exch_seg": "CDS",
         "instrumenttype": "AMXIDX", "lotsize": "1", "tick_size": "5"},
        {"symbol": "HDFCBANK", "name": "HDFCBANK", "token": "1333", "exch_seg": "NSE",
         "instrumenttype": "", "lotsize": "1", "tick_size": "5"},
    ]
    b = AngelOneBroker(api_key="k", client_code="c", mpin="1234",
                       totp_secret="JBSWY3DPEHPK3PXP", transport=FakeTransport(),
                       instrument_master=master)
    b.connect()
    insts = b.instruments()
    assert insts["NIFTY"].token == "99926000"               # AMXIDX, not 26000
    assert insts["NIFTY"].kind == InstrumentKind.INDEX
    assert insts["HDFCBANK"].kind == InstrumentKind.EQUITY   # equities unchanged


_MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _expiry_str(d: date) -> str:  # Angel master format, locale-independent
    return f"{d.day:02d}{_MON[d.month - 1]}{d.year}"


def test_index_future_resolves_to_front_month():
    """Regression: the static config symbol 'NIFTY-FUT' must resolve to the
    NEAREST non-expired dated FUTIDX contract (front month), so the index
    future gets a real token + data feed. Without this it has NO token -> no
    websocket subscription -> no bars -> it never enters the tradeable universe
    (the 'algo takes no index trades' symptom). Dates are relative to today so
    the test never goes stale."""
    today = now_ist().date()
    past, front, far = (today - timedelta(days=30),
                        today + timedelta(days=5),
                        today + timedelta(days=40))
    master = [
        {"symbol": f"NIFTY{_expiry_str(d)}FUT", "name": "NIFTY", "token": tok,
         "exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": _expiry_str(d),
         "lotsize": "65", "tick_size": "10"}
        for d, tok in ((past, "111"), (front, "222"), (far, "333"))
    ]
    b = AngelOneBroker(api_key="k", client_code="c", mpin="1234",
                       totp_secret="JBSWY3DPEHPK3PXP", transport=FakeTransport(),
                       instrument_master=master)
    b.connect()
    insts = b.instruments()
    assert "NIFTY-FUT" in insts
    assert insts["NIFTY-FUT"].token == "222"            # front month, not past/far
    assert insts["NIFTY-FUT"].kind == InstrumentKind.FUTURE
    assert insts["NIFTY-FUT"].exchange == "NFO"
    assert insts["NIFTY-FUT"].lot_size == 65
    assert b._token_to_symbol["222"] == "NIFTY-FUT"     # ticks route to the alias


def test_parse_expiry():
    from quantsys.execution.angelone import _parse_expiry
    assert _parse_expiry("30JUN2026") == date(2026, 6, 30)
    assert _parse_expiry("07JUL2026") == date(2026, 7, 7)
    assert _parse_expiry(None) is None
    assert _parse_expiry("") is None
    assert _parse_expiry("BADVALUE") is None


def test_funds_and_positions(broker):
    assert broker.funds() == 1_500_000.0
    pos = broker.positions()
    assert len(pos) == 1 and pos[0].symbol == "SBIN-EQ" and pos[0].qty == 10


def test_place_is_idempotent(broker):
    o = BrokerOrder("QS-1", "SBIN-EQ", "BUY", 5, ExecutionStyle.MARKET_SINGLE, Urgency.NORMAL)
    a1 = broker.place(o)
    a2 = broker.place(o)  # same client id
    assert a1.broker_order_id == a2.broker_order_id
    assert "idempotent" in a2.detail
    assert len(broker._transport.placed) == 1  # only ONE real order sent


def test_unknown_instrument_raises(broker):
    with pytest.raises(BrokerError):
        broker.place(BrokerOrder("QS-2", "NOPE", "BUY", 1,
                                 ExecutionStyle.MARKET_SINGLE, Urgency.NORMAL))


def test_historical_candles_chunks_and_normalises_to_naive_ist(broker):
    # ~243 days at FIVE_MINUTE (100-day max window) => 3 requests
    rows = broker.historical_candles(
        "SBIN-EQ", "FIVE_MINUTE", datetime(2021, 1, 1, 9, 15),
        datetime(2021, 9, 1, 15, 30))
    assert len(broker._transport.candle_calls) == 3
    assert len(rows) == 3
    ts, o, h, lo, c, v = rows[0]
    assert ts.tzinfo is None            # +05:30 stripped to naive IST
    assert (o, h, lo, c, v) == (100.0, 101.0, 99.5, 100.5, 1234.0)
    assert rows == sorted(rows, key=lambda r: r[0])  # ascending


def test_historical_candles_rejects_unknown_interval(broker):
    with pytest.raises(BrokerError):
        broker.historical_candles("SBIN-EQ", "TWO_SECOND",
                                  datetime(2021, 1, 1), datetime(2021, 1, 2))


def test_reconnect_feed_session_relogins_without_master(broker):
    # the websocket feed calls this after a drop: re-login for fresh tokens,
    # but do NOT re-pull the (already loaded) instrument master.
    creds = broker.reconnect_feed_session()
    assert creds["feed_token"] == "ft"
    assert creds["client_code"] == "c" and creds["api_key"] == "k"
    assert broker.is_connected()
    assert "SBIN-EQ" in broker.instruments()  # universe still present


# ------------------------------------------------------------- OMS tests
def test_oms_single_order_places_once(broker):
    oms = OMS(broker)
    insts = broker.instruments()
    intent = OrderIntent("SBIN-EQ", 10, ExecutionStyle.MARKET_SINGLE,
                         Urgency.NORMAL, "trend", "test")
    ts = datetime(2026, 6, 12, 9, 20)
    mos = oms.submit_intents([intent], insts, ts, {"SBIN-EQ": 550.0})
    assert len(mos) == 1 and mos[0].done
    # resubmitting the same bar's intent is idempotent (same parent id)
    again = oms.submit_intents([intent], insts, ts, {"SBIN-EQ": 550.0})
    assert again[0].parent_id == mos[0].parent_id
    assert len(broker._transport.placed) == 1


def test_oms_twap_slices_over_pumps(broker):
    oms = OMS(broker)
    insts = broker.instruments()
    # large futures order with TWAP style -> multiple slices, one per pump
    intent = OrderIntent("NIFTY28AUG25FUT", 75 * 8, ExecutionStyle.SLICE_TWAP,
                         Urgency.NORMAL, "trend", "test")
    insts["NIFTY28AUG25FUT"] = Instrument(
        symbol="NIFTY28AUG25FUT", token="53001", exchange="NFO",
        kind=InstrumentKind.FUTURE, lot_size=75, tick_size=0.05, adv=75 * 20)
    ts = datetime(2026, 6, 12, 9, 20)
    mos = oms.submit_intents([intent], insts, ts, {"NIFTY28AUG25FUT": 24000.0})
    mo = mos[0]
    assert len(mo.slices) > 1
    placed_after_submit = sum(s.placed for s in mo.slices)
    assert placed_after_submit == 1   # only first slice placed immediately
    for _ in range(len(mo.slices)):
        oms.pump(insts, {"NIFTY28AUG25FUT": 24000.0})
    assert all(s.placed for s in mo.slices)
    assert mo.done


def test_oms_urgency_orders_go_first(broker):
    oms = OMS(broker)
    insts = broker.instruments()
    normal = OrderIntent("SBIN-EQ", 5, ExecutionStyle.MARKET_SINGLE, Urgency.NORMAL, "a")
    kill = OrderIntent("SBIN-EQ", -5, ExecutionStyle.MARKET_SINGLE, Urgency.KILL, "b")
    ts = datetime(2026, 6, 12, 9, 20)
    oms.submit_intents([normal, kill], insts, ts, {"SBIN-EQ": 550.0})
    # the KILL (risk-reducing) order must be the first placeOrder call — it
    # sorts ahead of the NORMAL order, so it is the SELL
    assert broker._transport.placed[0]["transactiontype"] == "SELL"
    assert broker._transport.placed[1]["transactiontype"] == "BUY"
    # every ordertag fits Angel's 20-char limit verbatim
    for p in broker._transport.placed:
        assert len(p["ordertag"]) <= 20


# ------------------------------------------------------- reconciliation
def test_reconcile_detects_mismatch_and_freezes():
    from quantsys.config import load_config
    from quantsys.risk.engine import RiskEngine

    cfg = load_config("config/base.yaml")
    risk = RiskEngine(cfg.drawdown, cfg.sizing.base_risk_frac, set(), 0)

    class B:
        def positions(self):
            from quantsys.execution.broker import BrokerPosition
            return [BrokerPosition("SBIN-EQ", 10)]
    r = reconcile_positions(B(), internal={"SBIN-EQ": 5})
    assert not r.ok and r.mismatches
    risk.reconcile(r.internal, r.broker)
    assert risk.halted_reason is not None and "reconciliation" in risk.halted_reason


def test_reconcile_clean_when_matched():
    class B:
        def positions(self):
            from quantsys.execution.broker import BrokerPosition
            return [BrokerPosition("SBIN-EQ", 5)]
    r = reconcile_positions(B(), internal={"SBIN-EQ": 5})
    assert r.ok and not r.mismatches


def test_reconcile_unreadable_broker_is_not_ok():
    class B:
        def positions(self):
            raise BrokerError("network down", retryable=True)
    r = reconcile_positions(B(), internal={"SBIN-EQ": 5})
    assert not r.ok
