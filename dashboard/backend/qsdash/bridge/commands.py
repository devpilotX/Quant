"""CommandConsumer — the engine-side half of the control plane.

Runs inside the engine process between decision bars. Commands transition
pending -> acked -> done|rejected and every outcome lands back in the row's
``result`` so the UI can show exactly what happened. The engine is the ONLY
thing that flips runtime_config['mode'] — the badge shows engine truth.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from qsdash.bus import SyncPublisher
from qsdash.db import SessionLocal, now_ist
from qsdash.models import Command, RuntimeConfig

if TYPE_CHECKING:  # pragma: no cover
    from qsdash.bridge.runner import Runner

log = logging.getLogger(__name__)


class CommandConsumer:
    def __init__(self, runner: "Runner", publisher: SyncPublisher):
        self.runner = runner
        self.publisher = publisher

    def poll(self) -> None:
        sess = SessionLocal()
        try:
            pending = (
                sess.query(Command)
                .filter(Command.status == "pending")
                .order_by(Command.id.asc())
                .all()
            )
            for cmd in pending:
                cmd.status = "acked"
                cmd.acked_at = now_ist()
                sess.commit()
                self._publish(cmd)
                try:
                    result = self._apply(cmd, sess)
                    cmd.status = "done"
                    cmd.result = result or {"ok": True}
                except _Reject as r:
                    cmd.status = "rejected"
                    cmd.result = {"ok": False, "reason": str(r)}
                    log.warning("command %s rejected: %s", cmd.kind, r)
                except Exception as e:  # noqa: BLE001
                    cmd.status = "rejected"
                    cmd.result = {"ok": False, "reason": f"error: {e}"}
                    log.exception("command %s failed", cmd.kind)
                cmd.completed_at = now_ist()
                sess.commit()
                self._publish(cmd)
        finally:
            sess.close()

    def _publish(self, cmd: Command) -> None:
        self.publisher.publish("commands", {
            "id": cmd.id, "kind": cmd.kind, "status": cmd.status,
            "result": cmd.result,
        })

    def _set_rc(self, sess, key: str, value) -> None:
        row = sess.query(RuntimeConfig).filter(RuntimeConfig.key == key).first()
        if row is None:
            row = RuntimeConfig(key=key, value={"v": value}, updated_by="engine")
            sess.add(row)
        else:
            row.value = {"v": value}
            row.version += 1
            row.updated_at = now_ist()
            row.updated_by = "engine"

    # ---------------------------------------------------------------- apply
    def _apply(self, cmd: Command, sess) -> dict:
        r = self.runner
        kind = cmd.kind
        p = cmd.payload or {}

        if kind == "set_mode":
            target = p.get("target_mode")
            if target not in ("paper", "live"):
                raise _Reject(f"bad target mode {target!r}")
            if target == r.mode:
                raise _Reject(f"already in {target}")
            if target == "live":
                # Execution adapter for Angel One is the next phase; refuse
                # loudly rather than pretend. Paper -> live becomes possible
                # only when a real broker adapter is wired in.
                raise _Reject(
                    "live execution adapter not installed yet — "
                    "engine cannot trade real money in this build"
                )
            # live -> paper or paper -> paper(capital change) path
            if p.get("flatten_first") and r.broker is not None:
                r.broker.flatten_all(r.current_prices(), now_ist(),
                                     reason="mode switch flatten")
            r.mode = target
            self._set_rc(sess, "mode", target)
            return {"ok": True, "mode": target}

        if kind == "set_paper_capital":
            cap = float(p.get("capital", 0))
            if cap <= 0:
                raise _Reject("capital must be positive")
            if r.broker is not None and r.broker.positions:
                raise _Reject("close all paper positions before changing capital")
            r.reset_paper_capital(cap)
            return {"ok": True, "capital": cap}

        if kind == "set_deployable_cap":
            if "deployable_cap_frac" in p:
                r.deployable_cap_frac = float(p["deployable_cap_frac"])
            if "deployable_cap_abs" in p:
                r.deployable_cap_abs = float(p["deployable_cap_abs"])
            return {"ok": True,
                    "deployable_cap_frac": r.deployable_cap_frac,
                    "deployable_cap_abs": r.deployable_cap_abs}

        if kind == "kill":
            r.engine.risk.dd_killed = True
            return {"ok": True, "note": "kill armed — engine will flatten on next bar"}

        if kind == "flatten":
            if r.broker is None:
                raise _Reject("no broker attached")
            r.broker.flatten_all(r.current_prices(), now_ist(),
                                 reason=p.get("reason") or "operator flatten")
            return {"ok": True}

        if kind == "rearm_dd_kill" or kind == "clear_halt":
            r.engine.risk.rearm()
            return {"ok": True}

        if kind == "strategy_toggle":
            name = p.get("strategy", "")
            enabled = bool(p.get("enabled", True))
            r.set_strategy_enabled(name, enabled)
            return {"ok": True, "strategy": name, "enabled": enabled}

        if kind == "set_config":
            key, value = p.get("key", ""), p.get("value")
            r.apply_config_override(key, value)
            return {"ok": True, "key": key, "value": value}

        raise _Reject(f"unknown command kind {kind!r}")


class _Reject(Exception):
    pass
