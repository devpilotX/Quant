"use client";

/** Backtest viewer: browse runs, IS-vs-OOS metrics, equity curves.
 * Populates when the backtest harness (next phase) writes backtest_runs. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtCompact, fmtNum, fmtTs } from "@/lib/format";
import { EChart, lineOption } from "@/components/charts";
import { Card, Spinner, Table, Td } from "@/components/ui";

type Run = { id: number; created_at: string; label: string; git_rev: string; metrics: Record<string, unknown> };
type RunDetail = Run & {
  params: Record<string, unknown>;
  equity_curve: { ts: string; equity: number }[] | [string, number][];
};

export default function BacktestsPage() {
  const [sel, setSel] = useState<number | null>(null);
  const { data: runs, isLoading } = useQuery<Run[]>({
    queryKey: ["backtests"],
    queryFn: () => apiGet("/backtests"),
  });
  const active = sel ?? runs?.[0]?.id ?? null;
  const { data: detail } = useQuery<RunDetail>({
    queryKey: ["backtests", active],
    queryFn: () => apiGet(`/backtests/${active}`),
    enabled: active !== null,
  });

  const curve: [string, number][] = (detail?.equity_curve ?? []).map((p) =>
    Array.isArray(p) ? (p as [string, number]) : [p.ts, p.equity]
  );

  return (
    <div className="space-y-3">
      <Card title="Walk-forward runs" pad={false}>
        {isLoading ? (
          <Spinner />
        ) : (
          <Table cols={["Run", "Created", "Label", "Git rev",
            { label: "Sharpe (OOS)", align: "right" },
            { label: "Deflated", align: "right" },
            { label: "Max DD", align: "right" }]}
            empty="no backtest runs yet — the walk-forward harness is the next engine phase; runs land here automatically">
            {(runs ?? []).map((r) => (
              <tr key={r.id}
                className={`cursor-pointer hover:bg-surface2/60 ${active === r.id ? "bg-accent/5" : ""}`}
                onClick={() => setSel(r.id)}>
                <Td>#{r.id}</Td>
                <Td>{fmtTs(r.created_at).slice(0, 16)}</Td>
                <Td className="font-medium">{r.label || "—"}</Td>
                <Td className="num">{r.git_rev.slice(0, 8) || "—"}</Td>
                <Td right>{fmtNum(r.metrics?.sharpe_oos as number, 2)}</Td>
                <Td right>{fmtNum(r.metrics?.sharpe_deflated as number, 2)}</Td>
                <Td right>{fmtNum(r.metrics?.max_dd as number, 3)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {detail && (
        <>
          {(() => {
            const m = detail.metrics as Record<string, unknown>;
            const verdict = m?.verdict as string | undefined;
            if (!verdict) return null;
            const synth = m?.is_synthetic === true;
            const eligible = verdict.includes("Eligible for tiny-capital");
            const tone = synth ? "border-warn/40 bg-warn/10 text-warn"
              : eligible ? "border-up/40 bg-up/10 text-up"
              : "border-down/40 bg-down/10 text-down";
            return (
              <div className={`rounded border px-3 py-2 text-xs ${tone}`}>
                <b>Verdict:</b> {verdict}
              </div>
            );
          })()}
          <Card title={`Run #${detail.id} — equity curve`}>
            {curve.length > 1 ? (
              <EChart height={260} option={lineOption(
                [{ name: "equity", data: curve, area: true }],
                (v) => fmtCompact(v))} />
            ) : (
              <div className="py-10 text-center text-xs text-muted">no curve stored</div>
            )}
          </Card>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Card title="Metrics (IS vs OOS)">
              <pre className="num overflow-x-auto text-xs text-muted">
                {JSON.stringify(detail.metrics, null, 2)}
              </pre>
            </Card>
            <Card title="Parameters">
              <pre className="num overflow-x-auto text-xs text-muted">
                {JSON.stringify(detail.params, null, 2)}
              </pre>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
