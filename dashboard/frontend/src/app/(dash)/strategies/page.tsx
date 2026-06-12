"use client";

/** Strategy monitor: live Kelly allocation, rolling edge stats, enable/disable. */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { fmtNum, fmtTs } from "@/lib/format";
import { EChart, lineOption } from "@/components/charts";
import { useReauthAction } from "@/components/reauth";
import { Badge, Button, Card, Spinner, Table, Td } from "@/components/ui";

type Strat = {
  strategy: string; enabled: boolean; ts: string | null; mu: number;
  var: number; n_eff: number; kelly_f: number; sharpe_ann: number;
  incubating: boolean;
};
type Hist = { ts: string; mu: number; var: number; kelly_f: number; sharpe_ann: number; n_eff: number };

export default function StrategiesPage() {
  const qc = useQueryClient();
  const { guard, modal } = useReauthAction();
  const [sel, setSel] = useState<string | null>(null);
  const { data: strats, isLoading } = useQuery<Strat[]>({
    queryKey: ["strategies"],
    queryFn: () => apiGet("/strategies"),
  });
  const active = sel ?? strats?.[0]?.strategy ?? null;
  const { data: hist } = useQuery<Hist[]>({
    queryKey: ["strategies", "history", active],
    queryFn: () => apiGet(`/strategies/${active}/history?limit=2000`),
    enabled: active !== null,
  });

  return (
    <div className="space-y-3">
      {modal}
      <Card title="Strategies" pad={false}>
        {isLoading ? (
          <Spinner />
        ) : (
          <Table cols={["Strategy", "Status", { label: "Kelly f", align: "right" },
            { label: "edge μ/bar", align: "right" }, { label: "var", align: "right" },
            { label: "Sharpe (ann)", align: "right" }, { label: "n_eff", align: "right" },
            "Updated", "Toggle"]} empty="no strategy stats yet — engine must run first">
            {(strats ?? []).map((s) => (
              <tr key={s.strategy}
                className={`cursor-pointer hover:bg-surface2/60 ${active === s.strategy ? "bg-accent/5" : ""}`}
                onClick={() => setSel(s.strategy)}>
                <Td className="font-medium">{s.strategy}</Td>
                <Td>
                  <span className="flex gap-1">
                    <Badge tone={s.enabled ? "green" : "red"}>
                      {s.enabled ? "enabled" : "disabled"}
                    </Badge>
                    {s.incubating && <Badge tone="amber">incubating</Badge>}
                  </span>
                </Td>
                <Td right className={s.kelly_f > 0 ? "text-up" : "text-muted"}>
                  {fmtNum(s.kelly_f, 4)}
                </Td>
                <Td right>{s.mu.toExponential(2)}</Td>
                <Td right>{s.var.toExponential(2)}</Td>
                <Td right className={s.sharpe_ann > 0 ? "text-up" : "text-down"}>
                  {fmtNum(s.sharpe_ann, 2)}
                </Td>
                <Td right>{fmtNum(s.n_eff, 0)}</Td>
                <Td>{s.ts ? fmtTs(s.ts).slice(5, 16) : "—"}</Td>
                <Td>
                  <Button
                    tone={s.enabled ? "danger" : "primary"}
                    onClick={() =>
                      guard({
                        title: `${s.enabled ? "Disable" : "Enable"} strategy ${s.strategy}`,
                        danger: s.enabled,
                        summary: s.enabled ? (
                          <span>
                            The engine stops generating signals from <b>{s.strategy}</b> next
                            bar. Existing positions are unwound by the normal order diff.
                          </span>
                        ) : (
                          <span><b>{s.strategy}</b> resumes signal generation next bar.</span>
                        ),
                        run: () => apiPost("/control/strategy", {
                          strategy: s.strategy, enabled: !s.enabled,
                          reason: "operator toggle",
                        }),
                        onDone: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
                      })
                    }
                  >
                    {s.enabled ? "disable" : "enable"}
                  </Button>
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {active && hist && hist.length > 1 && (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <Card title={`${active} — Kelly fraction`}>
            <EChart height={200} option={lineOption(
              [{ name: "kelly_f", data: hist.map((h) => [h.ts, h.kelly_f]) }],
              (v) => fmtNum(v, 4))} />
          </Card>
          <Card title={`${active} — annualised Sharpe (EWMA)`}>
            <EChart height={200} option={lineOption(
              [{ name: "sharpe", data: hist.map((h) => [h.ts, h.sharpe_ann]), color: "#2fbf71" }],
              (v) => fmtNum(v, 2))} />
          </Card>
        </div>
      )}
    </div>
  );
}
