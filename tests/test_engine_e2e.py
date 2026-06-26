"""End-to-end: a synthetic market driven through the full DecisionEngine with
naive fills. Proves the acceptance criteria that belong to the core:

- the same engine trades, respects every cap bar-by-bar, and survives;
- kill switch flattens and locks the session;
- changing ONLY equity re-sizes and re-gates the whole book (tier ladder);
- engine state round-trips through JSON with identical subsequent decisions.
"""

from __future__ import annotations

import json

import numpy as np

from quantsys.core.market_state import MarketState
from quantsys.core.types import Bar, InstrumentKind, Position
from quantsys.data.history import BarHistory
from quantsys.engine.decision import DecisionEngine
from tests.conftest import cointegrated_pair, gbm, make_inst, ts_seq

N = 700
WARM = 320


def _make_market(seed=0):
    rng = np.random.default_rng(seed)
    nifty = gbm(N, start=22000, drift=0.0002, vol=0.0015, seed=seed + 1)
    trend = 100 * np.exp(np.cumsum(rng.normal(0.0012, 0.003, N)))
    a, b = cointegrated_pair(N, beta=1.0, kappa=0.10, seed=seed + 2)
    chop = gbm(N, start=500, vol=0.004, seed=seed + 3)
    return {"NIFTY": nifty, "TRND": trend, "AAA": a, "BBB": b, "CHOP": chop}


# futures: realistic vehicle for systematic trend/pairs (delivery-STT-free)
_FUT = {"kind": InstrumentKind.FUTURE, "margin_rate": 0.2}
INSTS = {
    "NIFTY": make_inst("NIFTY", kind=InstrumentKind.INDEX),
    "TRND": make_inst("TRND", sector="mom", adv=5_000_000, **_FUT),
    "AAA": make_inst("AAA", sector="fin", adv=4_000_000, **_FUT),
    "BBB": make_inst("BBB", sector="fin", adv=4_000_000, **_FUT),
    "CHOP": make_inst("CHOP", sector="other", adv=3_000_000, **_FUT),
}


class NaiveBroker:
    """Instant fills at close; cash accounting with the engine's cost model."""

    def __init__(self, equity: float, cost_model):
        self.cash = equity
        self.positions: dict[str, Position] = {}
        self.cm = cost_model

    def execute(self, orders, prices):
        for o in orders:
            px = prices[o.symbol]
            inst = INSTS[o.symbol]
            cost = self.cm.order_cost(inst, o.qty_delta, px, is_buy=o.qty_delta > 0,
                                      delivery=True).total
            self.cash -= o.qty_delta * px + cost
            pos = self.positions.get(o.symbol) or Position(o.symbol, 0, 0.0)
            pos.qty += o.qty_delta
            pos.avg_price = px
            if pos.qty == 0:
                self.positions.pop(o.symbol, None)
            else:
                self.positions[o.symbol] = pos

    def equity(self, prices) -> float:
        return self.cash + sum(p.qty * prices[s] for s, p in self.positions.items())


def _run(cfg, equity0: float, seed=0, n=N, override_equity=None):
    market = _make_market(seed)
    times = ts_seq(n)
    hists = {s: BarHistory(capacity=n + 8) for s in market}
    engine = DecisionEngine(cfg, dict(INSTS))
    broker = NaiveBroker(equity0, engine.cost_model)
    decisions, violations, pos_log = [], [], {}

    for t in range(n):
        for s, px in market.items():
            o = market[s][t - 1] if t else px[t]
            c = px[t]
            hists[s].append(Bar(times[t], o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1e6))
        if t < WARM:
            continue
        prices = {s: market[s][t] for s in market}
        eq = broker.equity(prices) if override_equity is None else override_equity(t, broker, prices)
        state = MarketState(ts=times[t], equity=eq, bars=hists, instruments=INSTS,
                            positions=dict(broker.positions))
        engine.post_bar(state)
        d = engine.decide(state)
        decisions.append(d)
        broker.execute(d.orders, prices)
        pos_log[t] = {s: p.qty for s, p in broker.positions.items()}

        # ---- invariants checked EVERY bar on the actually-held book
        post = {s: p.qty * prices[s] for s, p in broker.positions.items()}
        gross = sum(abs(v) for v in post.values())
        cap = 2.5 * max(eq, 1.0) * 1.10  # max ladder leverage + fill-drift tolerance
        if gross > cap:
            violations.append((t, "gross", gross, cap))
        for s, v in post.items():
            if abs(v) > 0.25 * max(eq, 1.0) * 1.15:
                violations.append((t, f"per_instrument:{s}", abs(v), 0.25 * eq))
    return engine, broker, decisions, violations, pos_log


def test_engine_trades_and_respects_caps(small_cfg):
    engine, broker, decisions, violations, _pos = _run(small_cfg, 1_000_000.0)
    assert violations == []
    assert any(d.targets for d in decisions), "engine never engaged"
    assert any(o.reason == "entry" for d in decisions for o in d.orders)
    # the audit trail is populated and serialisable
    assert any(d.audit for d in decisions)
    json.dumps(engine.state_dict(), default=str)


def test_kill_switch_flattens_and_locks_session(small_cfg):
    # crash equity to -4% of the session anchor at bar CRASH_T (within one session)
    CRASH_T = WARM + 100  # bar 420, session spans bars 375..449
    anchor: dict[str, float] = {}

    def crash(t, broker, prices):
        eq = broker.equity(prices)
        anchor.setdefault(f"d{t // 75}", eq)  # first equity seen each session
        return anchor[f"d{t // 75}"] * 0.96 if t >= CRASH_T else eq

    engine, broker, decisions, _v, pos_log = _run(small_cfg, 1_000_000.0,
                                                  override_equity=crash)
    killed = [d for d in decisions if d.kill_reason == "daily_loss_limit"]
    assert killed, "daily kill never fired"
    assert all(not d.targets for d in decisions if d.kill_reason)
    # book is flat from the kill bar to the end of that session
    for t in range(CRASH_T, 450):
        assert pos_log.get(t, {}) == {}, f"positions held at bar {t} while killed"


def test_capital_adaptation_only_E_changes(small_cfg):
    _, _, d_small, v1, _p1 = _run(small_cfg, 100_000.0, seed=5)
    _, _, d_big, v2, _p2 = _run(small_cfg, 20_000_000.0, seed=5)
    assert v1 == [] and v2 == []
    assert {d.tier_name for d in d_small} == {"T1"}
    assert {d.tier_name for d in d_big} <= {"T4", "T5"}
    g_small = max((sum(abs(t.qty * t.ref_price) for t in d.targets) for d in d_small),
                  default=0.0)
    g_big = max((sum(abs(t.qty * t.ref_price) for t in d.targets) for d in d_big),
                default=0.0)
    assert g_big > 20 * g_small  # the book actually re-sizes with E
    syms_small = {t.symbol for d in d_small for t in d.targets}
    syms_big = {t.symbol for d in d_big for t in d.targets}
    assert len(syms_big) >= len(syms_small)  # and re-gates the universe


def test_state_roundtrip_determinism(small_cfg):
    market = _make_market(7)
    times = ts_seq(N)
    hists = {s: BarHistory(capacity=N + 8) for s in market}
    e1 = DecisionEngine(small_cfg, dict(INSTS))
    snapshot = None
    last_state = None
    for t in range(N):
        for s, px in market.items():
            o = market[s][t - 1] if t else px[t]
            c = px[t]
            hists[s].append(Bar(times[t], o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1e6))
        if t < WARM:
            continue
        state = MarketState(ts=times[t], equity=1_000_000.0, bars=hists,
                            instruments=INSTS, positions={})
        if t == N - 1:
            snapshot = json.loads(json.dumps(e1.state_dict(), default=str))
            last_state = state
            d1 = e1.decide(state)
        else:
            e1.post_bar(state)
            e1.decide(state)

    e2 = DecisionEngine(small_cfg, dict(INSTS))
    e2.load_state(snapshot)
    d2 = e2.decide(last_state)
    assert [(t.symbol, t.qty) for t in d1.targets] == [(t.symbol, t.qty) for t in d2.targets]
    assert [(o.symbol, o.qty_delta) for o in d1.orders] == [(o.symbol, o.qty_delta) for o in d2.orders]
