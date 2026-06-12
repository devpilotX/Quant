"""The live-trading gate — engine-side, defence in depth.

The dashboard already enforces the operator chain (re-auth + typed phrase +
deployable cap + clean book) before it queues set_mode(live). This gate is the
SECOND, independent lock the engine itself checks before it will ever flip to
live. All conditions must hold:

1. A live execution adapter is attached and connected (paper runner => never).
2. The operator has explicitly armed live trading out-of-band
   (QS_LIVE_ARMED=1 in the engine's environment — not settable from the UI).
3. The backtester gate passed: a NON-synthetic backtest_runs row exists whose
   metrics clear the robustness bar (positive deflated OOS Sharpe, low P(SR<0)).

Synthetic-only history => condition 3 fails => live is refused. This is the
"do not go live until honest OOS edge is shown" rule, enforced in code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from qsdash.models import BacktestRun

# robustness thresholds (documented in DECISIONS); conservative on purpose
MIN_OOS_SHARPE = 0.8
MIN_DEFLATED = 0.95
MAX_P_SHARPE_NEG = 0.10


@dataclass
class GateStatus:
    allowed: bool
    reasons: list[str]          # why NOT allowed (empty when allowed)
    passing_run_id: int | None = None


def backtest_gate(db: Session) -> GateStatus:
    """Condition 3 only — usable standalone by the dashboard to show status."""
    runs = (db.query(BacktestRun).order_by(BacktestRun.id.desc()).limit(50).all())
    real = [r for r in runs if not (r.metrics or {}).get("is_synthetic", True)]
    if not real:
        return GateStatus(False, ["no non-synthetic backtest run exists — "
                                  "run the walk-forward on real NSE history first"])
    for r in real:
        m = r.metrics or {}
        sharpe = m.get("sharpe_oos")
        deflated = m.get("sharpe_deflated")
        p_neg = (m.get("monte_carlo") or {}).get("p_sharpe_negative")
        if (sharpe is not None and sharpe >= MIN_OOS_SHARPE
                and deflated is not None and deflated >= MIN_DEFLATED
                and (p_neg is None or p_neg <= MAX_P_SHARPE_NEG)):
            return GateStatus(True, [], passing_run_id=r.id)
    return GateStatus(False, [
        f"newest real backtest does not clear the robustness bar "
        f"(need OOS Sharpe>={MIN_OOS_SHARPE}, deflated>={MIN_DEFLATED}, "
        f"P(SR<0)<={MAX_P_SHARPE_NEG})"])


def live_gate(db: Session, *, adapter_present: bool,
              adapter_connected: bool) -> GateStatus:
    reasons: list[str] = []
    if not adapter_present:
        reasons.append("no live execution adapter attached (paper runner cannot "
                       "trade real money)")
    elif not adapter_connected:
        reasons.append("live execution adapter not connected to the broker")
    if os.environ.get("QS_LIVE_ARMED", "") not in ("1", "true", "TRUE", "yes"):
        reasons.append("QS_LIVE_ARMED is not set in the engine environment "
                       "(operator must arm live trading out-of-band)")
    bt = backtest_gate(db)
    if not bt.allowed:
        reasons.extend(bt.reasons)
    return GateStatus(not reasons, reasons, passing_run_id=bt.passing_run_id)
