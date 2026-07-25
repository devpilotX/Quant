"""End-to-end: real DecisionEngine on synthetic bars -> recorder + paper
broker -> rows in (sqlite) DB, with the accounting identity holding at every
step: equity == cash + MTM.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from qsdash.bridge.runner import Runner, synthetic_bars
from qsdash.db import now_ist
from qsdash.models import (
    Command,
    DecisionRow,
    EngineStatus,
    EquityPoint,
    PositionRow,
    RuntimeConfig,
)
from tests.conftest import REPO_ROOT

CFG = str(REPO_ROOT / "config" / "base.yaml")
N_BARS = 700  # enough bars for warmup + some trading on 5-min clock


@pytest.fixture(scope="module")
def ran_runner():
    runner = Runner(CFG, mode="paper", paper_capital=2_000_000.0)
    symbols = list(runner.engine.instruments)
    start = now_ist() - timedelta(days=30)
    bar_minutes = runner.cfg.engine.decision_bar_minutes
    for ts, bars in synthetic_bars(symbols, start, N_BARS, bar_minutes, seed=3):
        runner.step(ts, bars, "synthetic")
    return runner


def test_decisions_persisted(ran_runner, db):
    n = db.query(DecisionRow).filter(DecisionRow.mode == "paper").count()
    assert n == N_BARS
    d = db.query(DecisionRow).order_by(DecisionRow.id.desc()).first()
    assert d.equity > 0 and d.tier_name
    assert isinstance(d.audit, list)


def test_equity_accounting_identity(ran_runner, db):
    last = (db.query(EquityPoint).filter(EquityPoint.mode == "paper")
            .order_by(EquityPoint.id.desc()).first())
    assert last is not None
    assert abs(last.equity - (last.cash + last.mtm)) < 1e-6


def test_reported_realized_pnl_is_net_of_fees(db, monkeypatch):
    """The reported realized P&L must reconcile to the equity change.

    Regression for the reporting defect found 2026-07-26. `realized` is the raw
    price difference — closing_qty * (px - avg_price) * point_value, with no
    costs in it — so accumulating it alone made every headline understate the
    truth by the entire fee bill. On the live paper book the dashboard showed
    -Rs 1.74 lakh realized while the actual net was -Rs 9.15 lakh: a 5.3x
    understatement. Fees were deducted from `cash` correctly but never reached
    the number a human reads.

    Exploration is forced ON here purely so there IS a fee bill to check — the
    shipped config has it off, which is why the module fixture above trades
    nothing.
    """
    from quantsys.config import load_config as _load

    import qsdash.bridge.runner as R

    def _forced(path):
        cfg = _load(path)
        cfg.kelly.explore_floor = 0.35      # force fills regardless of edge
        cfg.sizing.enforce_cost_gate = False
        return cfg

    monkeypatch.setattr(R, "load_config", _forced)
    cap = 5_000_000.0
    runner = R.Runner(CFG, mode="paper", paper_capital=cap)
    symbols = list(runner.engine.instruments)
    start = now_ist() - timedelta(days=30)
    bar_minutes = runner.cfg.engine.decision_bar_minutes
    for ts, bars in synthetic_bars(symbols, start, 400, bar_minutes, seed=7):
        runner.step(ts, bars, "synthetic")

    b = runner.broker
    assert b.fees_total > 0.0, "forced exploration must produce fills to pay for"

    prices = runner.current_prices()
    # equity - starting_capital == realized_NET + unrealized
    lhs = b.equity(prices) - cap
    rhs = b.realized_total + b.unrealized(prices)
    assert abs(lhs - rhs) < 1e-6, (
        f"net P&L must reconcile: equity change {lhs:,.2f} vs "
        f"realized+unrealized {rhs:,.2f}"
    )

    # the pre-fix behaviour reported GROSS; pin the size of the lie so a
    # regression that reintroduces it fails loudly rather than silently
    gross = b.realized_total + b.fees_total
    assert gross > b.realized_total
    stale = gross + b.unrealized(prices)
    assert abs((stale - lhs) - b.fees_total) < 1e-6, (
        "reporting gross would overstate P&L by exactly the fee bill "
        f"({b.fees_total:,.2f})"
    )


def test_orders_and_positions_consistent(ran_runner, db):
    open_rows = (db.query(PositionRow)
                 .filter(PositionRow.mode == "paper",
                         PositionRow.status == "open").all())
    broker_pos = ran_runner.broker.positions
    assert {r.symbol for r in open_rows} == set(broker_pos)
    for r in open_rows:
        assert r.qty == broker_pos[r.symbol].qty
        assert r.entry_decision_id is not None
        assert r.entry_rationale.get("sizing") is not None  # explainability


def test_heartbeat_written(ran_runner, db):
    es = db.get(EngineStatus, 1)
    assert es is not None and es.last_heartbeat is not None
    assert es.detail.get("data_source") == "synthetic"


def test_command_kill_flattens(ran_runner, db):
    if not ran_runner.broker.positions:
        pytest.skip("no open positions at end of synthetic run")
    db.add(Command(created_by="test", kind="kill", payload={}))
    db.commit()
    ran_runner.commands.poll()
    assert ran_runner.engine.risk.dd_killed is True
    # next bar: engine emits flatten orders via its own kill path
    symbols = list(ran_runner.engine.instruments)
    start_ts = now_ist()
    gen = synthetic_bars(symbols, start_ts, 1,
                         ran_runner.cfg.engine.decision_bar_minutes, seed=9)
    ts, bars = next(iter(gen))
    ran_runner.step(ts, bars, "synthetic")
    assert ran_runner.broker.positions == {}
    d = db.query(DecisionRow).order_by(DecisionRow.id.desc()).first()
    assert d.kill_reason == "max_drawdown"  # manual kill uses the dd latch
    # rearm via command
    db.add(Command(created_by="test", kind="rearm_dd_kill", payload={}))
    db.commit()
    ran_runner.commands.poll()
    assert ran_runner.engine.risk.dd_killed is False


def test_set_mode_live_rejected_without_adapter(ran_runner, db):
    db.add(Command(created_by="test", kind="set_mode",
                   payload={"target_mode": "live"}))
    db.commit()
    ran_runner.commands.poll()
    cmd = (db.query(Command).filter(Command.kind == "set_mode")
           .order_by(Command.id.desc()).first())
    assert cmd.status == "rejected"
    assert "adapter" in cmd.result["reason"]
    mode = db.query(RuntimeConfig).filter(RuntimeConfig.key == "mode").first()
    assert mode.value["v"] == "paper"
