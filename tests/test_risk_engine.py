from datetime import datetime, timedelta

import pytest

from quantsys.config.schema import DrawdownConfig
from quantsys.core.types import TargetPosition
from quantsys.risk.engine import RiskEngine, StopTracker
from tests.conftest import gbm, make_hist, make_inst, make_state

T0 = datetime(2026, 1, 5, 9, 20)


def _engine(**kw) -> RiskEngine:
    cfg = DrawdownConfig(**kw)
    return RiskEngine(cfg, base_risk_frac=0.006, trailing_strategies={"trend"},
                      stop_cooldown_bars=3)


def test_drawdown_throttle_linear_and_floor():
    eng = _engine(max_drawdown=0.18)
    eng.pre_decide(T0, 100.0)                      # sets HWM=100
    pre = eng.pre_decide(T0 + timedelta(minutes=5), 91.0)  # dd = 9%
    assert pre.throttle == pytest.approx(0.5)
    assert pre.risk_frac_eff == pytest.approx(0.003)
    pre2 = eng.pre_decide(T0 + timedelta(minutes=10), 82.0)  # dd = 18%
    assert pre2.throttle == 0.0
    assert pre2.risk_frac_eff == 0.0


def test_daily_loss_kill_and_auto_rearm_next_session():
    eng = _engine(daily_loss_limit=0.025, auto_rearm_daily=True)
    eng.pre_decide(T0, 100.0)
    pre = eng.pre_decide(T0 + timedelta(minutes=30), 97.4)  # -2.6% on the day
    assert pre.kill_reason == "daily_loss_limit"
    # still killed later the same session even if price recovers
    pre2 = eng.pre_decide(T0 + timedelta(hours=2), 99.5)
    assert pre2.kill_reason == "daily_loss_limit"
    # next session: re-armed, new anchor
    pre3 = eng.pre_decide(T0 + timedelta(days=1), 97.4)
    assert pre3.kill_reason is None


def test_max_drawdown_kill_requires_manual_rearm():
    eng = _engine(kill_drawdown=0.20)
    eng.pre_decide(T0, 100.0)
    pre = eng.pre_decide(T0 + timedelta(days=1), 79.0)  # dd 21%
    assert pre.kill_reason == "max_drawdown"
    pre2 = eng.pre_decide(T0 + timedelta(days=2), 95.0)  # recovery does NOT rearm
    assert pre2.kill_reason == "max_drawdown"
    eng.rearm()
    pre3 = eng.pre_decide(T0 + timedelta(days=2, minutes=5), 95.0)
    assert pre3.kill_reason is None


def test_equity_wipeout_kills():
    eng = _engine()
    assert eng.pre_decide(T0, -5.0).kill_reason == "max_drawdown"


def test_reconciliation_mismatch_halts_until_rearm():
    eng = _engine()
    mism = eng.reconcile({"X": 100}, {"X": 50, "Y": 10})
    assert len(mism) == 2
    assert eng.pre_decide(T0, 100.0).halted_reason is not None
    eng.rearm()
    assert eng.pre_decide(T0 + timedelta(minutes=5), 100.0).halted_reason is None


def _stop_state(symbol: str, price: float):
    bars = {symbol: make_hist(gbm(30, start=price, vol=1e-6, seed=9))}
    return make_state(bars, {symbol: make_inst(symbol)})


def test_hard_stop_long_and_short():
    st = StopTracker(trailing_strategies=set())
    st.refresh([
        TargetPosition("L", 100, "s", "g1", stop_distance=5.0, ref_price=100.0),
        TargetPosition("S", -100, "s", "g2", stop_distance=5.0, ref_price=100.0),
    ])
    assert st.breached(_stop_state("L", 95.5)) == {}
    hits = st.breached(_stop_state("L", 94.9))
    assert ("s", "L") in hits
    hits2 = st.breached(_stop_state("S", 105.2))
    assert ("s", "S") in hits2


def test_trailing_stop_ratchets():
    st = StopTracker(trailing_strategies={"trend"})
    st.refresh([TargetPosition("X", 100, "trend", "g", stop_distance=5.0, ref_price=100.0)])
    st.update_prices(_stop_state("X", 110.0))        # best -> 110, stop -> 105
    assert st.breached(_stop_state("X", 105.5)) == {}
    hits = st.breached(_stop_state("X", 104.9))
    assert ("trend", "X") in hits  # would NOT have hit the static 95 stop


def test_stop_cooldown_lockout():
    eng = _engine()
    eng.register_stop_hits({("trend", "X"): "stop"})
    assert eng.in_cooldown("trend", "X")
    for i in range(3):
        eng.pre_decide(T0 + timedelta(minutes=5 * i), 100.0)
    assert not eng.in_cooldown("trend", "X")


def test_state_roundtrip():
    eng = _engine()
    eng.pre_decide(T0, 100.0)
    eng.pre_decide(T0 + timedelta(minutes=5), 90.0)
    eng.register_stop_hits({("s", "X"): "stop"})
    d = eng.state_dict()
    eng2 = _engine()
    eng2.load_state(d)
    assert eng2.hwm == 100.0
    assert eng2.in_cooldown("s", "X")
