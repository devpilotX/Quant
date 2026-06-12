from qsdash.db import SessionLocal
from qsdash.models import BacktestRun

s = SessionLocal()
r = s.query(BacktestRun).order_by(BacktestRun.id.desc()).first()
if r is None:
    print("no backtest runs persisted")
else:
    m = r.metrics or {}
    full = m.get("full") or {}
    print(f"RUN {r.id}: {r.label}")
    print(f"  is_synthetic: {m.get('is_synthetic')}")
    print(f"  full:  sharpe={full.get('sharpe')}  cagr={full.get('cagr')}  "
          f"max_dd={full.get('max_dd')}  n_trades={full.get('n_trades')}  "
          f"cost_drag_bps={full.get('cost_drag_bps')}")
    print(f"  OOS:   sharpe={m.get('sharpe_oos')}  deflated={m.get('sharpe_deflated')}  "
          f"max_dd={m.get('max_dd')}")
    print(f"  n_trials={m.get('n_trials')}  curve_points={len(r.equity_curve)}")
    print(f"  VERDICT: {m.get('verdict')}")
s.close()
