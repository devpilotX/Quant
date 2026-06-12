"use client";

/** Risk dashboard: live limit state, kill switches, manual kill/flatten. */

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { fmtCompact, fmtNum, fmtPct, fmtTs } from "@/lib/format";
import { AuditTrail, type AuditEvent } from "@/components/audit";
import { useReauthAction } from "@/components/reauth";
import { Badge, Button, Card, Gauge, Table, Td } from "@/components/ui";

type Limits = {
  decision_id: number | null; decision_ts: string | null;
  halted: boolean; kill_reason: string | null;
  risk_frac_eff: number | null; vol_scaler: number | null;
  gross_exposure: number | null; net_exposure: number | null;
  equity: number | null;
  config_overrides: Record<string, number>;
  audit_events: AuditEvent[];
};

type RiskEv = {
  id: number; ts: string; mode: string; kind: string; rule: string;
  symbol: string | null; severity: string; cause: string;
  decision_id: number | null;
};

export default function RiskPage() {
  const qc = useQueryClient();
  const { guard, modal } = useReauthAction();
  const { data: rl } = useQuery<Limits>({
    queryKey: ["risk", "limits"],
    queryFn: () => apiGet("/risk/limits"),
  });
  const { data: events } = useQuery<RiskEv[]>({
    queryKey: ["risk", "events"],
    queryFn: () => apiGet("/risk/events?limit=200"),
  });

  const gross = rl?.gross_exposure ?? null;
  const eq = rl?.equity ?? null;

  return (
    <div className="space-y-3">
      {modal}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Card title="Kill / halt state">
          <div className="space-y-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-muted">kill switch:</span>
              {rl?.kill_reason ? (
                <Badge tone="red">ACTIVE — {rl.kill_reason}</Badge>
              ) : (
                <Badge tone="green">armed, not triggered</Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-muted">reconciliation:</span>
              {rl?.halted ? (
                <Badge tone="red">HALTED — state not trusted</Badge>
              ) : (
                <Badge tone="green">ok</Badge>
              )}
            </div>
            <div className="text-muted">
              last decision #{rl?.decision_id ?? "—"} · {fmtTs(rl?.decision_ts)}
            </div>
            <div className="flex gap-2 pt-1">
              <Button tone="danger" onClick={() =>
                guard({
                  title: "KILL — flatten everything",
                  danger: true,
                  summary: <span>Engine flattens <b>all positions at market</b> next bar and refuses new risk until re-armed.</span>,
                  run: () => apiPost("/control/kill", { action: "kill", reason: "operator kill from risk page" }),
                  onDone: () => qc.invalidateQueries(),
                })}>⛔ KILL</Button>
              <Button tone="danger" onClick={() =>
                guard({
                  title: "Flatten now (without arming kill)",
                  danger: true,
                  summary: <span>Closes every open position at market immediately. The engine may re-enter on the next bar unless you also kill.</span>,
                  run: () => apiPost("/control/kill", { action: "flatten", reason: "operator flatten" }),
                  onDone: () => qc.invalidateQueries(),
                })}>Flatten</Button>
              <Button onClick={() =>
                guard({
                  title: "Re-arm / clear halt",
                  summary: <span>Clears the kill latch and reconciliation halt. Only do this once you understand the cause.</span>,
                  run: () => apiPost("/control/kill", { action: "rearm_dd_kill", reason: "operator rearm" }),
                  onDone: () => qc.invalidateQueries(),
                })}>Re-arm</Button>
            </div>
          </div>
        </Card>

        <Card title="Live utilisation">
          <div className="space-y-2.5">
            <Gauge label={`Gross exposure (${fmtCompact(gross)})`}
              value={gross != null && eq ? gross / eq : null}
              max={3} format={(v) => `${fmtNum(v, 2)}× eq`} />
            <Gauge label="Net / equity"
              value={rl?.net_exposure != null && eq ? Math.abs(rl.net_exposure) / eq : null}
              max={1.5} format={(v) => `${fmtNum(v, 2)}×`} />
            <Gauge label="Risk throttle (risk_frac_eff vs base)"
              value={rl?.risk_frac_eff ?? null}
              max={Math.max(rl?.config_overrides?.["sizing.base_risk_frac"] ?? 0.005, rl?.risk_frac_eff ?? 0.005)}
              format={(v) => fmtNum(v, 4)} danger={2} />
            <div className="text-[11px] text-muted">
              vol scaler {fmtNum(rl?.vol_scaler, 2)} — the engine already scaled
              the book; gauges show post-scaling reality.
            </div>
          </div>
        </Card>

        <Card title="Operator risk overrides (versioned)">
          {rl && Object.keys(rl.config_overrides).length ? (
            <div className="space-y-1 text-xs">
              {Object.entries(rl.config_overrides).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-muted">{k}</span>
                  <span className="num">{typeof v === "number" ? fmtNum(v, 4) : String(v)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-4 text-center text-xs text-muted">
              no overrides — engine runs on base.yaml values
            </div>
          )}
        </Card>
      </div>

      <Card title="Risk pipeline — latest decision audit (caps, vetoes, scalers)">
        <AuditTrail events={rl?.audit_events ?? []} />
      </Card>

      <Card title="Risk events" pad={false}>
        <Table cols={["Time", "Kind", "Severity", "Symbol", "Cause", "Decision"]}
          empty="no risk events">
          {(events ?? []).map((e) => (
            <tr key={e.id} className="hover:bg-surface2/60">
              <Td>{fmtTs(e.ts).slice(5, 19)}</Td>
              <Td><Badge tone={e.severity === "crit" ? "red" : e.severity === "warn" ? "amber" : "neutral"}>{e.kind}</Badge></Td>
              <Td>{e.severity}</Td>
              <Td>{e.symbol ?? "—"}</Td>
              <Td className="max-w-md truncate">{e.cause}</Td>
              <Td>{e.decision_id ? `#${e.decision_id}` : "—"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
