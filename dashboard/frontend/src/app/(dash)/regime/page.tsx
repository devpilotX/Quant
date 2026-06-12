"use client";

/** Regime dashboard: current blend + timeline + how it scales risk. */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtNum, fmtPct, fmtTs } from "@/lib/format";
import { EChart, lineOption, regimeOption } from "@/components/charts";
import { Badge, Card, Stat } from "@/components/ui";

type Current = {
  available: boolean; ts?: string; label?: string;
  probs?: Record<string, number>; risk_scaler?: number;
  strategy_weights?: Record<string, number>; source?: string;
};
type HistRow = { ts: string; label: string; probs: Record<string, number>; risk_scaler: number; source: string };

const TONE: Record<string, "green" | "blue" | "red"> = {
  calm_trend: "green", calm_range: "blue", turbulent: "red",
};

export default function RegimePage() {
  const { data: cur } = useQuery<Current>({
    queryKey: ["regime", "current"],
    queryFn: () => apiGet("/regime/current"),
  });
  const { data: hist } = useQuery<HistRow[]>({
    queryKey: ["regime", "history"],
    queryFn: () => apiGet("/regime/history?limit=3000"),
  });

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card title="Current regime">
          {cur?.available ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Badge tone={TONE[cur.label!] ?? "blue"} className="px-3 py-1 text-sm">
                  {cur.label}
                </Badge>
                <span className="text-[11px] text-muted">
                  via {cur.source} · {fmtTs(cur.ts)}
                </span>
              </div>
              <div className="space-y-1">
                {Object.entries(cur.probs ?? {}).map(([k, v]) => (
                  <div key={k} className="flex items-center gap-2 text-xs">
                    <span className="w-20 text-muted">{k}</span>
                    <div className="h-1.5 flex-1 rounded bg-surface2">
                      <div className="h-1.5 rounded bg-accent" style={{ width: `${v * 100}%` }} />
                    </div>
                    <span className="num w-12 text-right">{fmtPct(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="py-6 text-center text-xs text-muted">no regime data yet</div>
          )}
        </Card>
        <Card title="Risk scaling now">
          <Stat label="Risk scaler" value={cur?.available ? fmtNum(cur.risk_scaler, 2) + "×" : "—"}
            sub="multiplies total portfolio risk (probability-blended, no cliff edges)" />
        </Card>
        <Card title="Strategy weights (regime)">
          {cur?.available && Object.keys(cur.strategy_weights ?? {}).length ? (
            <div className="space-y-1 text-xs">
              {Object.entries(cur.strategy_weights!).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-muted">{k}</span>
                  <span className="num">{fmtNum(v, 2)}×</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-6 text-center text-xs text-muted">—</div>
          )}
        </Card>
      </div>

      <Card title="Regime probabilities over time (stacked)">
        {hist && hist.length > 1 ? (
          <EChart height={260} option={regimeOption(hist)} />
        ) : (
          <div className="py-12 text-center text-xs text-muted">insufficient history</div>
        )}
      </Card>

      <Card title="Risk scaler over time">
        {hist && hist.length > 1 ? (
          <EChart height={180} option={lineOption(
            [{ name: "risk_scaler", data: hist.map((h) => [h.ts, h.risk_scaler]), color: "#f5a524" }],
            (v) => `${fmtNum(v, 2)}×`)} />
        ) : (
          <div className="py-12 text-center text-xs text-muted">insufficient history</div>
        )}
      </Card>
    </div>
  );
}
