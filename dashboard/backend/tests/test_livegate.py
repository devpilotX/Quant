"""The go-live gate: real money stays impossible until every lock opens.
These encode the project's hardest safety rule — do not weaken them."""

from __future__ import annotations

import os

import pytest

from qsdash.bridge.livegate import backtest_gate, live_gate
from qsdash.models import BacktestRun


@pytest.fixture(autouse=True)
def _clear_runs(db):
    db.query(BacktestRun).delete()
    db.commit()
    os.environ.pop("QS_LIVE_ARMED", None)
    yield
    db.query(BacktestRun).delete()
    db.commit()
    os.environ.pop("QS_LIVE_ARMED", None)


def _add_run(db, *, synthetic, sharpe=1.5, deflated=0.99, p_neg=0.02):
    db.add(BacktestRun(
        label="t", git_rev="x",
        metrics={
            "is_synthetic": synthetic, "sharpe_oos": sharpe,
            "sharpe_deflated": deflated,
            "monte_carlo": {"p_sharpe_negative": p_neg},
        },
    ))
    db.commit()


def test_backtest_gate_refuses_with_no_runs(db):
    g = backtest_gate(db)
    assert not g.allowed and "no non-synthetic" in g.reasons[0]


def test_backtest_gate_refuses_synthetic_only(db):
    _add_run(db, synthetic=True)
    g = backtest_gate(db)
    assert not g.allowed  # synthetic never counts as edge


def test_backtest_gate_refuses_weak_real_run(db):
    _add_run(db, synthetic=False, sharpe=0.2, deflated=0.5, p_neg=0.4)
    assert not backtest_gate(db).allowed


def test_backtest_gate_allows_robust_real_run(db):
    _add_run(db, synthetic=False, sharpe=1.4, deflated=0.98, p_neg=0.03)
    g = backtest_gate(db)
    assert g.allowed and g.passing_run_id is not None


def test_live_gate_refuses_without_adapter_even_with_good_backtest(db):
    _add_run(db, synthetic=False)
    os.environ["QS_LIVE_ARMED"] = "1"
    g = live_gate(db, adapter_present=False, adapter_connected=False)
    assert not g.allowed
    assert any("adapter" in r for r in g.reasons)


def test_live_gate_refuses_without_arm(db):
    _add_run(db, synthetic=False)
    g = live_gate(db, adapter_present=True, adapter_connected=True)
    assert not g.allowed
    assert any("QS_LIVE_ARMED" in r for r in g.reasons)


def test_live_gate_refuses_synthetic_only_fully_armed(db):
    _add_run(db, synthetic=True)
    os.environ["QS_LIVE_ARMED"] = "1"
    g = live_gate(db, adapter_present=True, adapter_connected=True)
    assert not g.allowed  # the backtest condition still fails


def test_live_gate_allows_only_when_everything_passes(db):
    _add_run(db, synthetic=False, sharpe=1.4, deflated=0.98, p_neg=0.03)
    os.environ["QS_LIVE_ARMED"] = "1"
    g = live_gate(db, adapter_present=True, adapter_connected=True)
    assert g.allowed and not g.reasons
