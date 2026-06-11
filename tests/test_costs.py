import pytest

from quantsys.config.schema import CostConfig
from quantsys.core.types import InstrumentKind
from quantsys.costs import CostModel
from tests.conftest import make_inst


@pytest.fixture
def cm() -> CostModel:
    return CostModel(CostConfig())


def test_future_stt_sell_side_only(cm):
    fut = make_inst("NIFTY-FUT", kind=InstrumentKind.FUTURE, lot_size=65)
    buy = cm.order_cost(fut, 65, 25000.0, is_buy=True)
    sell = cm.order_cost(fut, 65, 25000.0, is_buy=False)
    notional = 65 * 25000.0
    assert buy.stt == 0.0
    assert sell.stt == pytest.approx(0.0005 * notional)  # Budget-2026 rate
    assert buy.stamp == pytest.approx(0.00002 * notional)
    assert sell.stamp == 0.0


def test_brokerage_flat_cap(cm):
    fut = make_inst("F", kind=InstrumentKind.FUTURE)
    big = cm.order_cost(fut, 1000, 1000.0, is_buy=True)
    assert big.brokerage == 20.0  # min(20, 0.25% of 1e6)
    small = cm.order_cost(fut, 1, 100.0, is_buy=True)
    assert small.brokerage == pytest.approx(0.0025 * 100.0)


def test_gst_base(cm):
    fut = make_inst("F", kind=InstrumentKind.FUTURE)
    c = cm.order_cost(fut, 100, 1000.0, is_buy=True)
    assert c.gst == pytest.approx(0.18 * (c.brokerage + c.exchange_txn + c.sebi))


def test_equity_delivery_vs_intraday(cm):
    eq = make_inst("RELIANCE")
    deliv = cm.order_cost(eq, 100, 1500.0, is_buy=True, delivery=True)
    intra = cm.order_cost(eq, 100, 1500.0, is_buy=True, delivery=False)
    assert deliv.brokerage == 0.0 and intra.brokerage > 0
    assert deliv.stt == pytest.approx(0.001 * 150000.0)  # both sides on delivery
    assert intra.stt == 0.0  # intraday STT is sell-side only


def test_impact_scales_with_participation(cm):
    eq = make_inst("X", adv=1_000_000)
    small = cm.order_cost(eq, 1_000, 100.0, is_buy=True, sigma_daily=0.02)
    big = cm.order_cost(eq, 100_000, 100.0, is_buy=True, sigma_daily=0.02)
    assert big.impact / big.brokerage > 0
    assert (big.impact / (100_000 * 100)) > (small.impact / (1_000 * 100))


def test_round_trip_positive_and_sane(cm):
    fut = make_inst("F", kind=InstrumentKind.FUTURE, lot_size=65)
    rt = cm.round_trip(fut, 65, 25000.0)
    notional = 65 * 25000.0
    assert 0 < rt < 0.01 * notional  # all-in round trip well under 1%
