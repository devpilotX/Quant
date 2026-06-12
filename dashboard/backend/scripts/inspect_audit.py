import json

from sqlalchemy import text

from qsdash.db import SessionLocal

s = SessionLocal()
n_sig = s.execute(text(
    "SELECT count(*) FROM decisions WHERE jsonb_array_length(signals) > 0"
)).scalar()
n_ord = s.execute(text(
    "SELECT count(*) FROM decisions WHERE jsonb_array_length(orders) > 0"
)).scalar()
print(f"decisions with signals: {n_sig}, with orders: {n_ord}")

sig = s.execute(text(
    "SELECT id, ts, tier_name, signals, audit FROM decisions "
    "WHERE jsonb_array_length(signals) > 0 ORDER BY id DESC LIMIT 1"
)).fetchone()
if sig:
    print(f"decision {sig.id} @ {sig.ts} tier={sig.tier_name}")
    print("signals:", json.dumps(sig.signals)[:400])
    print("audit:")
    for a in sig.audit:
        print(f"  [{a['stage']}/{a['rule']}] {a.get('symbol')} "
              f"{a.get('before')}->{a.get('after')} {a['detail'][:90]}")
else:
    last = s.execute(text(
        "SELECT audit FROM decisions ORDER BY id DESC LIMIT 1")).scalar()
    print("no signal decisions; last audit:")
    for a in last:
        print(f"  [{a['stage']}/{a['rule']}] {a['detail'][:120]}")
s.close()
