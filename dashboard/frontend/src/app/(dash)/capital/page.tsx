"use client";

/** Capital & tier monitor: E(t), tier ladder position, hysteresis, caps. */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtCompact, fmtPct } from "@/lib/format";
import { Badge, Card, Stat } from "@/components/ui";

type Capital = {
  mode: string; equity: number | null; equity_ts: string | null;
  tier: string | null; paper_capital: number | null;
  deployable_cap_frac: number | null; deployable_cap_abs: number | null;
  tier_ladder: { name: string; min_equity: number; max_instruments: number; max_strategies: number }[];
  hysteresis: number | null;
};

export default function CapitalPage() {
  const { data: c } = useQuery<Capital>({
    queryKey: ["capital"],
    queryFn: () => apiGet("/capital"),
  });

  const eq = c?.equity ?? null;
  const ladder = c?.tier_ladder ?? [];
  const idx = ladder.findIndex((t) => t.name === c?.tier);
  const cur = idx >= 0 ? ladder[idx] : null;
  const next = idx >= 0 && idx + 1 < ladder.length ? ladder[idx + 1] : null;
  const hyst = c?.hysteresis ?? 0.1;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card><Stat label={`Equity (${c?.mode ?? "…"})`} value={fmtCompact(eq)} /></Card>
        <Card><Stat label="Active tier" value={c?.tier ?? "—"}
          sub={cur ? `${cur.max_instruments} instruments · ${cur.max_strategies} strategies` : ""} /></Card>
        <Card><Stat label="Next tier"
          value={next ? next.name : "max"}
          sub={next && eq != null ? `${fmtCompact(Math.max(0, next.min_equity - eq))} to go` : ""} /></Card>
        <Card><Stat label="Demotion floor"
          value={cur ? fmtCompact((1 - hyst) * cur.min_equity) : "—"}
          sub={`hysteresis ${fmtPct(hyst, 0)} — no tier flapping`} /></Card>
      </div>

      <Card title="Deployable-capital controls">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Stat label="Paper capital (operator-set)" value={fmtCompact(c?.paper_capital)} />
          <Stat label="Deployable cap — fraction"
            value={c?.deployable_cap_frac != null ? fmtPct(c.deployable_cap_frac) : "no cap"} />
          <Stat label="Deployable cap — absolute"
            value={c?.deployable_cap_abs != null ? fmtCompact(c.deployable_cap_abs) : "no cap"} />
        </div>
        <p className="mt-2 text-[11px] text-muted">
          The engine sizes off min(equity, caps). Change these in Settings (re-auth required).
        </p>
      </Card>

      <Card title="Tier ladder" pad={false}>
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-line text-[10px] uppercase tracking-wider text-muted">
              <th className="px-3 py-1.5">Tier</th>
              <th className="px-3 py-1.5 text-right">Min equity</th>
              <th className="px-3 py-1.5 text-right">Max instruments</th>
              <th className="px-3 py-1.5 text-right">Max strategies</th>
              <th className="px-3 py-1.5">Position</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/60">
            {ladder.map((t) => {
              const active = t.name === c?.tier;
              const within = eq != null && eq >= t.min_equity;
              return (
                <tr key={t.name} className={active ? "bg-accent/10" : ""}>
                  <td className="px-3 py-2 font-medium">
                    {t.name} {active && <Badge tone="blue">active</Badge>}
                  </td>
                  <td className="num px-3 py-2 text-right">{fmtCompact(t.min_equity)}</td>
                  <td className="num px-3 py-2 text-right">{t.max_instruments}</td>
                  <td className="num px-3 py-2 text-right">{t.max_strategies}</td>
                  <td className="px-3 py-2">
                    {eq == null ? "—" : (
                      <div className="h-1.5 w-full max-w-48 rounded bg-surface2">
                        <div
                          className={`h-1.5 rounded ${within ? "bg-up" : "bg-surface2"}`}
                          style={{
                            width: `${Math.min(100, Math.max(0, (eq / t.min_equity) * 100))}%`,
                          }}
                        />
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
            {ladder.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-muted">
                ladder publishes when the engine starts
              </td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
