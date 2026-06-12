"""Position reconciliation: broker is ground truth.

Compares the engine's intended/known book against the broker's reported
positions. Any mismatch beyond a lot tolerance is fed into
RiskEngine.reconcile(), which FREEZES the engine (no orders) — flattening on
top of an untrusted book could double the error. This mirrors the core's
kill-vs-halt semantics exactly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from quantsys.execution.broker import Broker, BrokerError

log = logging.getLogger("quantsys.reconcile")


@dataclass
class ReconResult:
    ok: bool
    internal: dict[str, int]
    broker: dict[str, int]
    mismatches: list[str]


def reconcile_positions(broker: Broker, internal: dict[str, int]
                        ) -> ReconResult:
    """Returns the maps the RiskEngine.reconcile() expects + a human summary.
    On a broker read failure we report NOT-ok with an explicit cause so the
    caller freezes rather than trusting a stale internal view."""
    try:
        bpos = {p.symbol: p.qty for p in broker.positions() if p.qty != 0}
    except BrokerError as e:
        return ReconResult(False, internal, {}, [f"broker positions unreadable: {e}"])

    mismatches: list[str] = []
    for sym in set(internal) | set(bpos):
        iq, bq = internal.get(sym, 0), bpos.get(sym, 0)
        if iq != bq:
            mismatches.append(f"{sym}: internal={iq} broker={bq}")
    return ReconResult(not mismatches, internal, bpos, mismatches)
