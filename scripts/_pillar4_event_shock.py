"""Pillar 4 / sec.8a — PRICE-SHOCK proxy event study (data-available, pre-registered).

Large abnormal-return + volume days proxy for unobserved news/earnings events
(announcement feeds are absent; brief sec.3 permits a disclosed proxy). Screens
whether ANY post-event drift exists before sourcing a real feed. Uses the engine
in research.event_study and CA-adjusted returns from research.factors. Locked
definitions in docs/PILLAR4_EVENT_DRIVEN.md sec.8a.
"""
from __future__ import annotations

from datetime import date


from quantsys.config import load_config
from quantsys.core.types import InstrumentKind
from quantsys.research.bhavcopy import build_panel, close_panel
from quantsys.research.event_study import Event, EventWindows, run_event_study
from quantsys.research.factors import daily_returns

Z_THRESH, V_THRESH, DECLUSTER = 4.0, 2.0, 30      # locked
RT_COST = 0.0030                                   # ~30 bps round-trip screen


def _events_for(rets, vol, syms, idx):
    sigma = rets.rolling(60).std().shift(1)
    z = rets / sigma
    vratio = vol / vol.rolling(20).mean().shift(1)
    up, dn = [], []
    rm = rets[syms].mean(axis=1).to_numpy()
    pos = {d: i for i, d in enumerate(idx)}
    for s in syms:
        zs = z[s]
        mask = (zs.abs() >= Z_THRESH) & (vratio[s] >= V_THRESH)
        last = -10**9
        ri = rets[s].to_numpy()
        for d in idx[mask.to_numpy(na_value=False)]:
            i = pos[d]
            if i - last < DECLUSTER:
                continue
            last = i
            (up if zs.loc[d] > 0 else dn).append(Event(s, ri, rm, i))
    return up, dn


def _report(tag, events, windows):
    res = run_event_study(events, windows)
    caar_bps = res.caar * 1e4
    sig = "SIG" if (res.bmp_p < 0.05 and min(res.rank_p, res.sign_p) < 0.05) else "ns"
    print(f"  {tag:<26} N={res.n_events:<4} CAAR={caar_bps:+7.0f}bps  "
          f"BMP_p={res.bmp_p:.3f} rank_p={res.rank_p:.3f} sign_p={res.sign_p:.3f}  "
          f"pos={res.frac_positive*100:3.0f}%  [{sig}]")
    return res


def main():
    cfg = load_config("config/base.yaml")
    syms_all = sorted(u.symbol for u in cfg.universe
                      if u.kind == InstrumentKind.EQUITY and u.sector)
    print("loading panel 2016..2026 (cache)...")
    tidy = build_panel(date(2016, 1, 1), date(2026, 6, 19), progress_every=0)
    rets = daily_returns(tidy)
    vol = close_panel(tidy, "volume").reindex(rets.index)
    syms = [s for s in syms_all if s in rets.columns and s in vol.columns]
    rets, vol = rets[syms], vol[syms]
    idx = rets.index
    print(f"universe={len(syms)} equities, {len(idx)} days "
          f"{idx.min().date()}..{idx.max().date()}")

    up, dn = _events_for(rets, vol, syms, idx)
    print(f"events: up={len(up)} down={len(dn)} (|z|>={Z_THRESH}, vol>={V_THRESH}x, "
          f"de-clustered {DECLUSTER}d)\n")

    react = EventWindows(est=(-250, -30), event=(-1, 1))
    d5 = EventWindows(est=(-250, -30), event=(1, 5))
    d20 = EventWindows(est=(-250, -30), event=(1, 20))
    cands = []
    for name, evs in (("UP-shock", up), ("DOWN-shock", dn)):
        print(name)
        _report("reaction [-1,+1]", evs, react)
        _report("drift [+1,+5]", evs, d5)
        r20 = _report("drift [+1,+20]", evs, d20)
        # screen: significant AND |CAAR| clearly beats round-trip cost
        if (r20.n_events >= 30 and r20.bmp_p < 0.05
                and min(r20.rank_p, r20.sign_p) < 0.05 and abs(r20.caar) > RT_COST):
            cands.append((name, r20))
        print()

    print("==== SCREEN (sec.8a) ====")
    if cands:
        for name, r in cands:
            print(f"  CANDIDATE: {name} drift [+1,+20] CAAR={r.caar*1e4:+.0f}bps "
                  f"(> {RT_COST*1e4:.0f}bps RT, significant) -> build sleeve + full gate (sec.6)")
    else:
        print("  NO direction shows significant post-event drift beating ~30bps RT cost.")
        print("  VERDICT: no event-drift edge on available data -> STOP (no sleeve). The")
        print("  event-study engine stays inert, ready for a real announcement feed.")


if __name__ == "__main__":
    main()
