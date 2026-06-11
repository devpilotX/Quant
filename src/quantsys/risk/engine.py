"""Risk engine: account-level kill switches, the continuous drawdown
throttle, per-position hard stops (with trailing for trend-tagged positions),
stop cooldowns, and broker reconciliation.

Semantics of the two halt classes (deliberately different):
- KILL (daily loss / max drawdown): our state is trusted, the risk is the
  market -> FLATTEN everything, halt new risk.
- HALT (reconciliation mismatch): our state is NOT trusted -> freeze (no
  orders at all; flattening on top of a wrong book could double the error)
  and demand human attention. Broker is ground truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from quantsys.config.schema import DrawdownConfig
from quantsys.core.market_state import MarketState
from quantsys.core.types import Position, TargetPosition, session_date


@dataclass(frozen=True)
class RiskPre:
    risk_frac_eff: float
    drawdown: float
    throttle: float
    day_pnl: float
    kill_reason: str | None
    halted_reason: str | None


class StopTracker:
    """Hard catastrophic stops per (strategy, symbol); trailing optional.

    Strategy-level exits (z-score, hysteresis, time stops) are the primary
    exit path; this tracker is the independent backstop that fires even if a
    strategy misbehaves.
    """

    def __init__(self, trailing_strategies: set[str]):
        self.trailing = trailing_strategies
        self.entries: dict[str, dict] = {}  # "strategy|symbol" -> entry data

    @staticmethod
    def _key(strategy: str, symbol: str) -> str:
        return f"{strategy}|{symbol}"

    def refresh(self, targets: list[TargetPosition]) -> None:
        wanted: set[str] = set()
        for t in targets:
            if t.qty == 0 or t.stop_distance <= 0:
                continue
            k = self._key(t.strategy, t.symbol)
            wanted.add(k)
            e = self.entries.get(k)
            if e is None or (e["direction"] > 0) != (t.qty > 0):
                self.entries[k] = {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "direction": 1 if t.qty > 0 else -1,
                    "entry_price": t.ref_price,
                    "stop_distance": t.stop_distance,
                    "best_price": t.ref_price,
                }
        for k in list(self.entries):
            if k not in wanted:
                del self.entries[k]

    def update_prices(self, state: MarketState) -> None:
        for e in self.entries.values():
            if e["strategy"] not in self.trailing:
                continue
            px = state.price(e["symbol"])
            if not math.isfinite(px):
                continue
            e["best_price"] = max(e["best_price"], px) if e["direction"] > 0 else min(e["best_price"], px)

    def breached(self, state: MarketState) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for e in self.entries.values():
            px = state.price(e["symbol"])
            if not math.isfinite(px):
                continue
            anchor = e["best_price"] if e["strategy"] in self.trailing else e["entry_price"]
            if e["direction"] > 0 and px <= anchor - e["stop_distance"]:
                out[(e["strategy"], e["symbol"])] = f"stop: {px:.2f} <= {anchor - e['stop_distance']:.2f}"
            elif e["direction"] < 0 and px >= anchor + e["stop_distance"]:
                out[(e["strategy"], e["symbol"])] = f"stop: {px:.2f} >= {anchor + e['stop_distance']:.2f}"
        return out

    def to_dict(self) -> dict:
        return {"entries": self.entries}

    def load(self, d: dict) -> None:
        self.entries = {k: dict(v) for k, v in d.get("entries", {}).items()}


class RiskEngine:
    def __init__(self, cfg: DrawdownConfig, base_risk_frac: float,
                 trailing_strategies: set[str], stop_cooldown_bars: int = 12):
        self.cfg = cfg
        self.base_risk_frac = base_risk_frac
        self.stop_cooldown_bars = stop_cooldown_bars
        self.stops = StopTracker(trailing_strategies)
        self.hwm: float | None = None
        self.day_anchor: float | None = None
        self.day_date: str | None = None
        self.day_killed = False
        self.dd_killed = False
        self.halted_reason: str | None = None
        self.cooldowns: dict[str, int] = {}  # "strategy|symbol" -> bars left

    # ----------------------------------------------------------------- core
    def pre_decide(self, ts: datetime, equity: float) -> RiskPre:
        d = str(session_date(ts))
        if d != self.day_date:
            self.day_date = d
            self.day_anchor = equity
            if self.cfg.auto_rearm_daily:
                self.day_killed = False
        self.hwm = max(self.hwm or equity, equity)
        self.day_anchor = self.day_anchor or equity

        for k in list(self.cooldowns):
            self.cooldowns[k] -= 1
            if self.cooldowns[k] <= 0:
                del self.cooldowns[k]

        dd = max(0.0, 1.0 - equity / self.hwm) if self.hwm and self.hwm > 0 else 0.0
        day_pnl = equity - self.day_anchor
        if equity <= 0:
            self.dd_killed = True
        if day_pnl <= -self.cfg.daily_loss_limit * self.day_anchor:
            self.day_killed = True
        if dd >= self.cfg.kill_drawdown:
            self.dd_killed = True

        throttle = float(min(max(1.0 - dd / self.cfg.max_drawdown, 0.0), 1.0))
        kill = ("max_drawdown" if self.dd_killed else
                "daily_loss_limit" if self.day_killed else None)
        return RiskPre(
            risk_frac_eff=self.base_risk_frac * throttle,
            drawdown=dd,
            throttle=throttle,
            day_pnl=day_pnl,
            kill_reason=kill,
            halted_reason=self.halted_reason,
        )

    def register_stop_hits(self, hits: dict[tuple[str, str], str]) -> None:
        for (strategy, symbol) in hits:
            self.cooldowns[f"{strategy}|{symbol}"] = self.stop_cooldown_bars

    def in_cooldown(self, strategy: str, symbol: str) -> bool:
        return f"{strategy}|{symbol}" in self.cooldowns

    def reconcile(self, internal: dict[str, int], broker: dict[str, int]) -> list[str]:
        """Broker is ground truth. Any mismatch freezes the engine."""
        mismatches = []
        for sym in sorted(set(internal) | set(broker)):
            a, b = internal.get(sym, 0), broker.get(sym, 0)
            if a != b:
                mismatches.append(f"{sym}: internal={a} broker={b}")
        if mismatches:
            self.halted_reason = "reconciliation: " + "; ".join(mismatches)
        return mismatches

    def rearm(self) -> None:
        """Manual operator action: clears hard kill and reconciliation halt."""
        self.dd_killed = False
        self.halted_reason = None

    # ---------------------------------------------------------- persistence
    def state_dict(self) -> dict:
        return {
            "hwm": self.hwm,
            "day_anchor": self.day_anchor,
            "day_date": self.day_date,
            "day_killed": self.day_killed,
            "dd_killed": self.dd_killed,
            "halted_reason": self.halted_reason,
            "cooldowns": dict(self.cooldowns),
            "stops": self.stops.to_dict(),
        }

    def load_state(self, d: dict) -> None:
        self.hwm = d.get("hwm")
        self.day_anchor = d.get("day_anchor")
        self.day_date = d.get("day_date")
        self.day_killed = d.get("day_killed", False)
        self.dd_killed = d.get("dd_killed", False)
        self.halted_reason = d.get("halted_reason")
        self.cooldowns = {k: int(v) for k, v in d.get("cooldowns", {}).items()}
        self.stops.load(d.get("stops", {}))


def positions_net(positions: dict[str, Position]) -> dict[str, int]:
    return {s: p.qty for s, p in positions.items() if p.qty != 0}
