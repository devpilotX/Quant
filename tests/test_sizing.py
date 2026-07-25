import pytest

from quantsys.config.schema import CostConfig, SizingConfig
from quantsys.core.types import (
    ExecutionStyle,
    InstrumentKind,
    LegSpec,
    Position,
    Signal,
)
from quantsys.costs import CostModel
from quantsys.portfolio.sizing import SizingEngine
from quantsys.portfolio.tiers import TierState
from tests.conftest import gbm, make_hist, make_inst, make_state


def _tier(min_cost_multiple=0.0, adv_cap_pct=1.0, band=0.2, lev=2.0) -> TierState:
    return TierState("T", 0, 10, 4, ExecutionStyle.LIMIT_SMART,
                     adv_cap_pct, min_cost_multiple, band, lev)


def _sizer(min_notional=0.0) -> SizingEngine:
    return SizingEngine(SizingConfig(), CostModel(CostConfig()), min_notional)


def _state_with(symbols: dict[str, float], equity=1_000_000.0, **inst_kw):
    bars = {s: make_hist(gbm(60, start=p, vol=0.0001, seed=i))
            for i, (s, p) in enumerate(symbols.items())}
    insts = {s: make_inst(s, **inst_kw) for s in symbols}
    return make_state(bars, insts, equity=equity)


def test_qty_formula_exact():
    state = _state_with({"X": 100.0})
    sig = Signal("s", "X", direction=1.0, stop_distance=10.0)
    book = _sizer().build_raw([sig], {"s": 0.5}, 1_000_000 * 0.006, state, [])
    # qty = E*risk_frac*f / stop = 6000*0.5/10 = 300
    assert book.net_qty()["X"] == pytest.approx(300.0, rel=1e-3)
    short = Signal("s", "X", direction=-1.0, stop_distance=10.0)
    book2 = _sizer().build_raw([short], {"s": 0.5}, 6000.0, state, [])
    assert book2.net_qty()["X"] == pytest.approx(-300.0, rel=1e-3)


def test_conviction_split_within_strategy():
    state = _state_with({"X": 100.0, "Y": 100.0})
    sigs = [Signal("s", "X", 1.0, 10.0), Signal("s", "Y", 0.5, 10.0)]
    book = _sizer().build_raw(sigs, {"s": 1.0}, 9000.0, state, [])
    nq = book.net_qty()
    assert nq["X"] == pytest.approx(2 * nq["Y"], rel=1e-6)  # 2/3 vs 1/3 risk share
    assert nq["X"] + nq["Y"] == pytest.approx(900.0, rel=1e-6)


def test_pair_legs_hedge_ratio_exact():
    state = _state_with({"A": 100.0, "B": 50.0})
    pa, pb = state.price("A"), state.price("B")  # actual GBM closes near 100/50
    sig = Signal("mr", "A", direction=1.0, stop_distance=5.0,
                 legs=(LegSpec("A", 1.0), LegSpec("B", -2.0)), tag="A|B")
    book = _sizer().build_raw([sig], {"mr": 1.0}, 5000.0, state, [])
    nq = book.net_qty()
    qa, qb = nq["A"], nq["B"]
    assert qa == pytest.approx(1000.0, rel=1e-12)            # 5000/5, price-free
    assert qb == pytest.approx(-2.0 * qa * pa / pb, rel=1e-9)
    assert (qb * pb) / (qa * pa) == pytest.approx(-2.0, rel=1e-9)


def test_lot_rounding_floors_and_drops_zero():
    state = _state_with({"F": 100.0}, **{"kind": InstrumentKind.FUTURE, "lot_size": 65})
    sig = Signal("s", "F", 1.0, stop_distance=10.0)
    book = _sizer().build_raw([sig], {"s": 1.0}, 3000.0, state, [])  # 300 raw
    targets = _sizer().finalize(book, _tier(), state, {}, {}, [])
    assert targets[0].qty == 260  # floor(300/65)=4 lots
    tiny = _sizer().build_raw([sig], {"s": 1.0}, 500.0, state, [])   # 50 raw < 1 lot
    audits = []
    assert _sizer().finalize(tiny, _tier(), state, {}, {}, audits) == []
    assert any(a.rule == "rounds_to_zero" for a in audits)


def test_pair_dropped_whole_if_one_leg_rounds_to_zero():
    state = _state_with({"A": 100.0, "B": 50.0}, **{"kind": InstrumentKind.FUTURE,
                                                    "lot_size": 500})
    sig = Signal("mr", "A", 1.0, 5.0, legs=(LegSpec("A", 1.0), LegSpec("B", -0.01)))
    book = _sizer().build_raw([sig], {"mr": 1.0}, 5000.0, state, [])
    targets = _sizer().finalize(book, _tier(), state, {}, {}, [])
    assert targets == []  # B leg rounds to zero -> no orphaned unhedged A leg


def test_cost_gate_blocks_thin_edges_at_small_tier():
    state = _state_with({"X": 100.0}, equity=100_000.0)
    sig = Signal("s", "X", 1.0, stop_distance=2.0, expected_edge_R=0.10)
    sizer = _sizer()
    book = sizer.build_raw([sig], {"s": 1.0}, 600.0, state, [])  # qty 300
    audits = []
    blocked = sizer.finalize(book, _tier(min_cost_multiple=5.0), state, {}, {}, audits)
    assert blocked == []
    assert any(a.rule == "cost_gate" for a in audits)
    book2 = sizer.build_raw([sig], {"s": 1.0}, 600.0, state, [])
    allowed = sizer.finalize(book2, _tier(min_cost_multiple=0.05), state, {}, {}, [])
    assert allowed and allowed[0].qty == 300


def test_cost_gate_can_be_disabled_for_paper_exploration():
    # paper exploration sets sizing.enforce_cost_gate=False so the order ->
    # fill -> reconcile -> P&L plumbing is exercised even on trades the honest
    # gate would veto. The default (True) is asserted by the test above.
    state = _state_with({"X": 100.0}, equity=100_000.0)
    sig = Signal("s", "X", 1.0, stop_distance=2.0, expected_edge_R=0.10)
    sizer = SizingEngine(SizingConfig(enforce_cost_gate=False), CostModel(CostConfig()), 0.0)
    book = sizer.build_raw([sig], {"s": 1.0}, 600.0, state, [])
    audits = []
    allowed = sizer.finalize(book, _tier(min_cost_multiple=5.0), state, {}, {}, audits)
    assert allowed and allowed[0].qty == 300       # blocked with gate on, allowed off
    assert not any(a.rule == "cost_gate" for a in audits)


def test_cost_gate_skips_held_positions():
    state = _state_with({"X": 100.0}, equity=100_000.0)
    sig = Signal("s", "X", 1.0, stop_distance=2.0, expected_edge_R=0.10)
    sizer = _sizer()
    book = sizer.build_raw([sig], {"s": 1.0}, 600.0, state, [])
    held = {"X": Position("X", 280, 99.0)}  # same direction -> not a new trade
    targets = sizer.finalize(book, _tier(min_cost_multiple=5.0), state, held, {}, [])
    assert targets and targets[0].qty == 300


def test_min_lot_promotion_unlocks_single_future_lot():
    """Index-futures unlock: a FUTURE whose risk budget rounds below one lot
    takes exactly ONE lot when promotion is on and the lot's rupee risk fits
    promotion_max_risk_frac of equity. (Promotion off => stays untraded, see
    test_lot_rounding_floors_and_drops_zero.)"""
    state = _state_with({"F": 100.0}, equity=50_000_000.0,
                        **{"kind": InstrumentKind.FUTURE, "lot_size": 65})
    sizer = SizingEngine(SizingConfig(min_lot_promotion=True),
                         CostModel(CostConfig()), 0.0)
    for direction, want in ((1.0, 65), (-1.0, -65)):
        sig = Signal("s", "F", direction, stop_distance=10.0)
        book = sizer.build_raw([sig], {"s": 1.0}, 500.0, state, [])  # 50 raw < 1 lot
        audits = []
        targets = sizer.finalize(book, _tier(), state, {}, {}, audits)
        assert [t.qty for t in targets] == [want]       # lot risk 650 <= 0.5% of 5e7
        assert any(a.rule == "min_lot_promotion" for a in audits)


def test_min_lot_promotion_respects_equity_risk_cap():
    """The promoted lot must NEVER breach the risk ceiling: at a small float
    one lot is genuinely too big and the honest answer stays no-trade."""
    state = _state_with({"F": 100.0}, equity=100_000.0,
                        **{"kind": InstrumentKind.FUTURE, "lot_size": 65})
    sizer = SizingEngine(SizingConfig(min_lot_promotion=True),
                         CostModel(CostConfig()), 0.0)
    sig = Signal("s", "F", 1.0, stop_distance=10.0)     # lot risk 650 > 0.5% of 1e5
    book = sizer.build_raw([sig], {"s": 1.0}, 50.0, state, [])
    audits = []
    assert sizer.finalize(book, _tier(), state, {}, {}, audits) == []
    assert any(a.rule == "rounds_to_zero" for a in audits)
    assert not any(a.rule == "min_lot_promotion" for a in audits)


def test_min_lot_promotion_rejects_size_manufacture():
    """Promotion must be ROUNDING, not size manufacture.

    Regression for the Study-2 defect found 2026-07-26: promotion_max_risk_frac
    bounds the promoted lot's ABSOLUTE rupee risk but not how far it overshoots
    what the risk model asked for. All 852 promotions over 2026-07-02..25 turned
    a fraction of a lot into a whole one — BANKNIFTY-FUT wanted a median 0.097
    of a lot, NIFTY-FUT 0.113 — a ~10x inflation, and those two symbols were 71%
    of the study's entire gross loss from 14 trades.

    Here the lot's risk (650) sits far inside the cap (0.5% of 5e7 = 250,000),
    so ONLY the new promotion_min_lot_frac test can block it."""
    state = _state_with({"F": 100.0}, equity=50_000_000.0,
                        **{"kind": InstrumentKind.FUTURE, "lot_size": 65})
    sizer = SizingEngine(SizingConfig(min_lot_promotion=True),
                         CostModel(CostConfig()), 0.0)
    sig = Signal("s", "F", 1.0, stop_distance=10.0)
    # budget 65 / stop 10 -> 6.5 units = 0.1 of a 65 lot: NIFTY-FUT's real median
    book = sizer.build_raw([sig], {"s": 1.0}, 65.0, state, [])
    audits = []
    assert sizer.finalize(book, _tier(), state, {}, {}, audits) == []
    assert not any(a.rule == "min_lot_promotion" for a in audits)
    assert any(a.rule == "rounds_to_zero" for a in audits)


def test_min_lot_promotion_still_rounds_up_from_half_a_lot():
    """The complement: once the budget genuinely reaches half a lot, rounding to
    the nearest lot goes UP and promotion still fires."""
    state = _state_with({"F": 100.0}, equity=50_000_000.0,
                        **{"kind": InstrumentKind.FUTURE, "lot_size": 65})
    sizer = SizingEngine(SizingConfig(min_lot_promotion=True),
                         CostModel(CostConfig()), 0.0)
    sig = Signal("s", "F", 1.0, stop_distance=10.0)
    # budget 400 / stop 10 -> 40 units = 0.615 of a lot
    book = sizer.build_raw([sig], {"s": 1.0}, 400.0, state, [])
    audits = []
    assert [t.qty for t in sizer.finalize(book, _tier(), state, {}, {}, audits)] == [65]
    assert any(a.rule == "min_lot_promotion" for a in audits)


def test_min_lot_promotion_threshold_is_configurable():
    """A deployment that accepts more overshoot can say so explicitly, rather
    than the bound being implicit in the risk cap."""
    state = _state_with({"F": 100.0}, equity=50_000_000.0,
                        **{"kind": InstrumentKind.FUTURE, "lot_size": 65})
    sig = Signal("s", "F", 1.0, stop_distance=10.0)
    lenient = SizingEngine(SizingConfig(min_lot_promotion=True,
                                        promotion_min_lot_frac=0.05),
                           CostModel(CostConfig()), 0.0)
    book = lenient.build_raw([sig], {"s": 1.0}, 65.0, state, [])   # 0.1 of a lot
    audits = []
    assert [t.qty for t in lenient.finalize(book, _tier(), state, {}, {}, audits)] == [65]
    assert any(a.rule == "min_lot_promotion" for a in audits)


def test_min_lot_promotion_never_touches_spread_legs():
    """Promoting one leg of a pair would corrupt the hedge ratio — multi-leg
    groups are excluded from promotion and still drop whole."""
    state = _state_with({"A": 100.0, "B": 50.0}, equity=50_000_000.0,
                        **{"kind": InstrumentKind.FUTURE, "lot_size": 500})
    sizer = SizingEngine(SizingConfig(min_lot_promotion=True),
                         CostModel(CostConfig()), 0.0)
    sig = Signal("mr", "A", 1.0, 5.0, legs=(LegSpec("A", 1.0), LegSpec("B", -0.01)))
    book = sizer.build_raw([sig], {"mr": 1.0}, 5000.0, state, [])
    audits = []
    assert sizer.finalize(book, _tier(), state, {}, {}, audits) == []
    assert not any(a.rule == "min_lot_promotion" for a in audits)


def test_bad_price_or_stop_skipped_with_audit():
    state = _state_with({"X": 100.0})
    audits = []
    book = _sizer().build_raw(
        [Signal("s", "MISSING", 1.0, 5.0)], {"s": 1.0}, 6000.0, state, audits)
    assert book.empty
    assert any(a.rule == "bad_price" for a in audits)
