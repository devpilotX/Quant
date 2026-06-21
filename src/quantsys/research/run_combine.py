"""Single pre-registered combine test (see docs/COMBINE_SURVIVORS.md).

Computes S1, S2, COMBO daily returns; confirms the low-correlation premise on IS;
evaluates all three (IS/OOS/deflated/PBO/net CAGR/DD/beta); prints the verdict
against the pre-registered gate. No tuning, one test.

Run:  python -m quantsys.research.run_combine
"""

from __future__ import annotations

import json

import pandas as pd

from quantsys.research import combine as C
from quantsys.research import factors as F
from quantsys.research import validation as V
from quantsys.research.xs_backtest import prepare_matrices

# pre-registered gate
G_SHARPE, G_DEFLATED, G_PSRNEG, G_PBO = 0.80, 0.95, 0.10, 0.50
CM_PANEL = "data_cache/bhavcopy/panel_2016_2026.parquet"
FNO_SERIES = "data_cache/fno/bnf_fno_2016_2026.parquet"


def main() -> dict:
    panel = pd.read_parquet(CM_PANEL)
    panel["date"] = pd.to_datetime(panel["date"])
    fno = pd.read_parquet(FNO_SERIES)
    fno["date"] = pd.to_datetime(fno["date"])
    print(f"CM panel {panel.shape}, FNO {fno.shape} {str(fno.date.min())[:10]}..{str(fno.date.max())[:10]}")

    P = prepare_matrices(panel)
    rets = P["rets"]
    med_turn = F.wide(panel, "turnover").median()
    top100 = med_turn.sort_values(ascending=False).head(100).index
    market = rets[top100].mean(axis=1).rename("market")

    print("computing S1 (BANKNIFTY PCR)...")
    r1 = C.s1_returns(fno)
    print("computing S2 (factor momentum, SSF shorts + futures costs)...")
    r2 = C.s2_returns(panel, fno, prepared=P)
    combo = C.inverse_vol_combine(r1, r2, vol_lookback=63)
    print(f"  S1 {len(r1)}d {str(r1.index.min())[:10]}..{str(r1.index.max())[:10]} | "
          f"S2 {len(r2)}d | COMBO {len(combo)}d")

    # ---- correlation premise (IS) ----
    j = pd.concat([r1, r2], axis=1, sort=True).dropna()
    j_is = j[j.index <= pd.Timestamp(C.IS_END)]
    corr_is = float(j_is["S1"].corr(j_is["S2"])) if len(j_is) > 30 else float("nan")
    corr_all = float(j["S1"].corr(j["S2"]))
    print(f"\nS1–S2 correlation: IS {corr_is:+.3f}  | full {corr_all:+.3f}  "
          f"(premise = low/uncorrelated)")

    # ---- metrics ----
    rows = {"S1": C.evaluate(r1, market), "S2": C.evaluate(r2, market),
            "COMBO": C.evaluate(combo, market)}
    print(f"\n{'sleeve':6} {'IS Sh':>6} {'OOS Sh':>7} {'defl':>6} {'P(SR<0)':>8} "
          f"{'CAGR':>7} {'maxDD':>7} {'beta':>6}")
    for k, m in rows.items():
        print(f"{k:6} {m['is_sharpe']:>6.2f} {m['oos_sharpe']:>7.2f} {m['oos_deflated']:>6.3f} "
              f"{m['oos_p_sr_neg']:>8.2f} {m['oos_cagr']*100:>6.1f}% {m['oos_maxdd']*100:>6.1f}% "
              f"{m['beta'] if m['beta'] is None else round(m['beta'],2):>6}")

    # ---- PBO across {S1,S2,COMBO} ----
    M = pd.concat([r1, r2, combo], axis=1, sort=True).dropna()
    pbo = V.pbo_cscv(M.values, n_splits=10)
    print(f"\nPBO across {{S1,S2,COMBO}}: {pbo['pbo']:.3f}")

    # ---- verdict (combo OOS vs pre-registered gate) ----
    c = rows["COMBO"]
    checks = {
        "OOS Sharpe>=0.80": (c["oos_sharpe"], c["oos_sharpe"] is not None and c["oos_sharpe"] >= G_SHARPE),
        "deflated>=0.95": (c["oos_deflated"], c["oos_deflated"] is not None and c["oos_deflated"] >= G_DEFLATED),
        "P(SR<0)<=0.10": (c["oos_p_sr_neg"], c["oos_p_sr_neg"] is not None and c["oos_p_sr_neg"] <= G_PSRNEG),
        "PBO<=0.50": (pbo["pbo"], pbo["pbo"] <= G_PBO),
        "net CAGR>0": (c["oos_cagr"], c["oos_cagr"] is not None and c["oos_cagr"] > 0),
    }
    passed = all(ok for _, ok in checks.values())
    print("\n" + "=" * 60)
    for name, (val, ok) in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:18} = {val}")
    print(f"\n  VERDICT: {'PASS — candidate' if passed else 'FAIL — combination dead (stop-rule)'}")
    print("=" * 60)

    out = {"corr_is": corr_is, "corr_all": corr_all, "pbo": pbo["pbo"],
           "metrics": rows, "checks": {k: v[1] for k, v in checks.items()}, "passed": passed}
    with open("data_cache/combine_results.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out


if __name__ == "__main__":
    main()
