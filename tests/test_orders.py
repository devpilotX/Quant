from quantsys.core.types import ExecutionStyle, Position, TargetPosition, Urgency
from quantsys.engine.orders import diff_orders
from quantsys.portfolio.tiers import TierState
from tests.conftest import make_inst


def _tier(band=0.2, style=ExecutionStyle.LIMIT_SMART):
    return TierState("T", 0, 10, 4, style, 0.02, 0.0, band, 2.0)


INSTS = {"X": make_inst("X"), "Y": make_inst("Y", kind="FUTURE", lot_size=65)}
PRICES = {"X": 100.0, "Y": 200.0}


def _tp(sym, qty):
    return TargetPosition(sym, qty, "s", f"g:{sym}", 1.0, PRICES[sym])


def test_entry_exit_rebalance():
    orders = diff_orders([_tp("X", 300)], {}, INSTS, PRICES, _tier(), 0.0)
    assert orders[0].qty_delta == 300 and orders[0].reason == "entry"
    orders2 = diff_orders([], {"X": Position("X", 300)}, INSTS, PRICES, _tier(), 0.0)
    assert orders2[0].qty_delta == -300 and orders2[0].reason == "exit"
    orders3 = diff_orders([_tp("X", 400)], {"X": Position("X", 300)}, INSTS, PRICES,
                          _tier(band=0.1), 0.0)
    assert orders3[0].qty_delta == 100 and orders3[0].reason == "rebalance"


def test_rebalance_band_suppresses_churn():
    # delta 40 < band 0.2 * target 300 = 60 -> skip
    orders = diff_orders([_tp("X", 300)], {"X": Position("X", 260)}, INSTS, PRICES,
                         _tier(band=0.2), 0.0)
    assert orders == []
    # full exit ignores the band
    orders2 = diff_orders([], {"X": Position("X", 10)}, INSTS, PRICES, _tier(), 0.0)
    assert len(orders2) == 1


def test_min_notional_suppresses_dribbles():
    orders = diff_orders([_tp("X", 1000)], {"X": Position("X", 700)}, INSTS, PRICES,
                         _tier(band=0.01), min_order_notional=50_000.0)
    assert orders == []  # 300 * 100 = 30k < 50k


def test_kill_flattens_at_market():
    pos = {"X": Position("X", 300), "Y": Position("Y", -130)}
    orders = diff_orders([_tp("X", 300)], pos, INSTS, PRICES, _tier(), 0.0, kill=True)
    assert {o.symbol: o.qty_delta for o in orders} == {"X": -300, "Y": 130}
    assert all(o.urgency == Urgency.KILL and o.style == ExecutionStyle.MARKET_SINGLE
               for o in orders)


def test_risk_reducing_overrides_band_and_style():
    orders = diff_orders([], {"X": Position("X", 300)}, INSTS, PRICES, _tier(), 0.0,
                         risk_reducing_symbols={"X"})
    assert orders[0].urgency == Urgency.RISK_REDUCING
    assert orders[0].style == ExecutionStyle.MARKET_SINGLE
