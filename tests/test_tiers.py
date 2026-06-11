from quantsys.config.schema import TiersConfig
from quantsys.core.types import ExecutionStyle
from quantsys.portfolio.tiers import TierLadder


def test_full_ladder_sweep_1L_to_20cr():
    ladder = TierLadder(TiersConfig())
    seen = []
    for e in [1e5, 5e5, 2e6, 1.2e7, 6e7, 2e9 / 10]:
        seen.append(ladder.resolve(e).name)
    assert seen == ["T1", "T2", "T3", "T4", "T5", "T6"]


def test_gates_change_with_capital():
    ladder = TierLadder(TiersConfig())
    t1 = ladder.resolve(1e5)
    assert t1.max_instruments == 2 and t1.max_strategies == 1
    assert t1.execution_style == ExecutionStyle.LIMIT_SINGLE
    ladder2 = TierLadder(TiersConfig())
    t6 = ladder2.resolve(2e8)
    assert t6.max_instruments >= 30 and t6.max_strategies >= 4
    assert t6.execution_style == ExecutionStyle.ALMGREN_CHRISS
    assert t6.adv_cap_pct > t1.adv_cap_pct
    assert t6.min_cost_multiple < t1.min_cost_multiple


def test_hysteresis_no_flapping():
    ladder = TierLadder(TiersConfig(hysteresis=0.10))
    assert ladder.resolve(5.2e5).name == "T2"
    assert ladder.resolve(4.8e5).name == "T2"   # within 10% band: hold
    assert ladder.resolve(4.4e5).name == "T1"   # below band: demote
    assert ladder.resolve(5.0e5).name == "T2"   # promotion immediate


def test_crash_demotes_through_multiple_tiers():
    ladder = TierLadder(TiersConfig())
    assert ladder.resolve(6e7).name == "T5"
    assert ladder.resolve(8e5).name == "T2"  # straight down, no ratchet


def test_interpolation_is_monotone_between_anchors():
    ladder = TierLadder(TiersConfig())
    prev = None
    for e in [1.5e6, 3e6, 6e6, 9e6]:
        cm = ladder.resolve(e).min_cost_multiple
        if prev is not None:
            assert cm <= prev  # cost gate relaxes smoothly as E grows
        prev = cm
