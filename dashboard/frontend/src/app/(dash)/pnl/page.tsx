"use client";

/** P&L & performance: equity curve, drawdown, attribution, metrics. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtCompact, fmtNum, fmtPct } from "@/lib/format";
import { EChart, barOption, lineOption } from "@/components/charts";
import { Button, Card, Stat } from "@/components/ui";

type EqPoint = {
  ts: string; equity: number; gross_exposure: number; net_exposure: number;
  realized_pnl: number; unrealized_pnl: number;
};
type Metrics = Record<string, number | boolean | null>;
type Attr = { bucket: string; gross_pnl: number; fees: number; net_pnl: number; n_trades: number };

export default function PnlPage() {
  const [by, setBy] = useState<"strategy" | "symbol" | "regime" | "day">("strategy");
  const { data: eq } = useQuery<EqPoint[]>({
    queryKey: ["pnl", "equity"],
    queryFn: () => apiGet("/equity-curve?max_points=3000"),
  });
  const { data: m } = useQuery<Metrics>({
    queryKey: ["pnl", "metrics"],
    queryFn: () => apiGet("/pnl/metrics"),
  });
  const { data: attr } = useQuery<Attr[]>({
    queryKey: ["pnl", "attr", by],
    queryFn: () => apiGet(`/pnl/attribution?by=${by}`),
  });

  const dd: [string, number][] = [];
  if (eq && eq.length) {
    let peak = eq[0].equity;
    for (const p of eq) {
      peak = Math.max(peak, p.equity);
      dd.push([p.ts, peak > 0 ? (p.equity - peak) / peak : 0]);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label="Sharpe (ann)" v={m?.sharpe as number | null} fmt={(x) => fmtNum(x, 2)} />
        <MetricCard label="Sortino" v={m?.sortino as number | null} fmt={(x) => fmtNum(x, 2)} />
        <MetricCard label="Calmar" v={m?.calmar as number | null} fmt={(x) => fmtNum(x, 2)} />
        <MetricCard label="Max DD" v={m?.max_drawdown as number | null} fmt={(x) => fmtPct(x)} bad />
        <MetricCard label="Hit rate" v={m?.hit_rate as number | null} fmt={(x) => fmtPct(x)} />
        <MetricCard label="Profit factor" v={m?.profit_factor as number | null} fmt={(x) => fmtNum(x, 2)} />
        <MetricCard label="Closed trades" v={m?.n_closed_trades as number | null} fmt={(x) => String(x)} />
        <MetricCard label="Cost drag" v={m?.cost_drag_bps as number | null} fmt={(x) => `${fmtNum(x, 1)} bps`} bad />
      </div>

      <Card title="Equity curve">
        {eq && eq.length > 1 ? (
          <EChart height={260} option={lineOption(
            [{ name: "equity", data: eq.map((p) => [p.ts, p.equity]), area: true }],
            (v) => fmtCompact(v))} />
        ) : <Empty />}
      </Card>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title="Drawdown">
          {dd.length > 1 ? (
            <EChart height={200} option={lineOption(
              [{ name: "drawdown", data: dd, color: "#e5484d", area: true }],
              (v) => fmtPct(v))} />
          ) : <Empty />}
        </Card>
        <Card title="Exposure">
          {eq && eq.length > 1 ? (
            <EChart height={200} option={lineOption(
              [
                { name: "gross", data: eq.map((p) => [p.ts, p.gross_exposure]) },
                { name: "net", data: eq.map((p) => [p.ts, p.net_exposure]), color: "#f5a524" },
              ],
              (v) => fmtCompact(v))} />
          ) : <Empty />}
        </Card>
      </div>

      <Card
        title={`P&L attribution by ${by}`}
        right={
          <div className="flex gap-1">
            {(["strategy", "symbol", "regime", "day"] as const).map((b) => (
              <Button key={b} tone={by === b ? "primary" : "default"} onClick={() => setBy(b)}>
                {b}
              </Button>
            ))}
          </div>
        }
      >
        {attr && attr.length ? (
          <EChart height={240} option={barOption(
            attr.map((a) => a.bucket?.slice(0, 10) || "(none)"),
            attr.map((a) => a.net_pnl),
            (v) => fmtCompact(v))} />
        ) : <Empty />}
        {attr && attr.length > 0 && (
          <div className="mt-2 text-[11px] text-muted">
            gross {fmtCompact(attr.reduce((s, a) => s + a.gross_pnl, 0))} · fees{" "}
            {fmtCompact(attr.reduce((s, a) => s + a.fees, 0))} · net{" "}
            {fmtCompact(attr.reduce((s, a) => s + a.net_pnl, 0))} ·{" "}
            {attr.reduce((s, a) => s + a.n_trades, 0)} trades
          </div>
        )}
      </Card>
    </div>
  );
}

function MetricCard({ label, v, fmt, bad }: {
  label: string; v: number | null | undefined;
  fmt: (x: number) => string; bad?: boolean;
}) {
  return (
    <Card>
      <Stat label={label}
        value={v == null ? "—" : fmt(v)}
        valueClass={v == null ? "" : bad ? (v > 0 ? "text-down" : "") : v > 0 ? "text-up" : v < 0 ? "text-down" : ""} />
    </Card>
  );
}

function Empty() {
  return <div className="py-12 text-center text-xs text-muted">insufficient data</div>;
}
