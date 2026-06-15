"use client";

/** Logs & alerts: decision log, alert history with delivery status,
 * operator audit log, config versions. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtCompact, fmtNum, fmtTs } from "@/lib/format";
import { Badge, Button, Card, Table, Td } from "@/components/ui";

type Decision = {
  id: number; ts: string; equity: number; tier: string; regime: string;
  vol_scaler: number; risk_frac_eff: number; halted: boolean;
  kill_reason: string | null; n_signals: number; n_orders: number; n_audit: number;
};
type Alert = {
  id: number; ts: string; severity: string; kind: string; title: string;
  body: string; channels: string[]; delivered: boolean;
};
type Audit = { id: number; ts: string; username: string; action: string; detail: Record<string, unknown>; ip: string };
type ConfigV = { id: number; ts: string; username: string; key: string; old_value: unknown; new_value: unknown; reason: string };

const TABS = ["decisions", "alerts", "audit", "config"] as const;

export default function LogsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("decisions");
  return (
    <div className="space-y-3">
      <div className="flex gap-1">
        {TABS.map((t) => (
          <Button key={t} tone={tab === t ? "primary" : "default"} onClick={() => setTab(t)}>
            {t}
          </Button>
        ))}
      </div>
      {tab === "decisions" && <Decisions />}
      {tab === "alerts" && <Alerts />}
      {tab === "audit" && <AuditLog />}
      {tab === "config" && <ConfigVersions />}
    </div>
  );
}

function Decisions() {
  const [offset, setOffset] = useState(0);
  const { data } = useQuery<{ total: number; rows: Decision[] }>({
    queryKey: ["decisions", "log", offset],
    queryFn: () => apiGet(`/decisions?limit=50&offset=${offset}`),
  });
  return (
    <Card title={`Decision log (${data?.total ?? 0} passes)`} pad={false}
      right={
        <div className="flex gap-1">
          <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>‹ newer</Button>
          <Button disabled={!data || offset + 50 >= data.total} onClick={() => setOffset(offset + 50)}>older ›</Button>
        </div>
      }>
      <Table cols={["#", "Time", { label: "Equity", align: "right" }, "Tier", "Regime",
        { label: "vol×", align: "right" }, { label: "risk_frac", align: "right" },
        { label: "signals", align: "right" }, { label: "orders", align: "right" }, "State"]}
        empty="no decisions yet">
        {(data?.rows ?? []).map((d) => (
          <tr key={d.id} className="hover:bg-surface2/60">
            <Td>#{d.id}</Td>
            <Td>{fmtTs(d.ts)}</Td>
            <Td right>{fmtCompact(d.equity)}</Td>
            <Td>{d.tier}</Td>
            <Td>{d.regime}</Td>
            <Td right>{fmtNum(d.vol_scaler, 2)}</Td>
            <Td right>{fmtNum(d.risk_frac_eff, 4)}</Td>
            <Td right>{d.n_signals}</Td>
            <Td right>{d.n_orders}</Td>
            <Td>
              {d.kill_reason ? <Badge tone="red">{d.kill_reason}</Badge>
                : d.halted ? <Badge tone="red">halted</Badge>
                : <Badge tone="green">ok</Badge>}
            </Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function Alerts() {
  const { data } = useQuery<Alert[]>({
    queryKey: ["alerts", "log"],
    queryFn: () => apiGet("/alerts?limit=200"),
  });
  return (
    <Card title="Alert history" pad={false}>
      <Table cols={["Time", "Severity", "Kind", "Title", "Channels", "Delivered"]}
        empty="no alerts raised yet">
        {(data ?? []).map((a) => (
          <tr key={a.id} className="hover:bg-surface2/60">
            <Td>{fmtTs(a.ts)}</Td>
            <Td><Badge tone={a.severity === "crit" ? "red" : a.severity === "warn" ? "amber" : "neutral"}>{a.severity}</Badge></Td>
            <Td>{a.kind}</Td>
            <Td className="max-w-md truncate">{a.title}</Td>
            <Td>{a.channels?.join(", ") || "—"}</Td>
            <Td>{a.delivered ? <Badge tone="green">yes</Badge> : <Badge tone="amber">no</Badge>}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function AuditLog() {
  const { data } = useQuery<Audit[]>({
    queryKey: ["audit", "log"],
    queryFn: () => apiGet("/audit-log?limit=300"),
  });
  return (
    <Card title="Operator audit log (every login & control action)" pad={false}>
      <Table cols={["Time", "User", "Action", "Detail", "IP"]} empty="empty">
        {(data ?? []).map((r) => (
          <tr key={r.id} className="hover:bg-surface2/60">
            <Td>{fmtTs(r.ts)}</Td>
            <Td>{r.username}</Td>
            <Td><Badge tone={r.action.includes("failed") || r.action.includes("locked") ? "red" : "neutral"}>{r.action}</Badge></Td>
            <Td className="num max-w-md truncate text-muted">{JSON.stringify(r.detail)}</Td>
            <Td>{r.ip}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function ConfigVersions() {
  const { data } = useQuery<ConfigV[]>({
    queryKey: ["config", "versions"],
    queryFn: () => apiGet("/config-versions?limit=200"),
  });
  return (
    <Card title="Config versions (every change, with diff)" pad={false}>
      <Table cols={["Time", "User", "Key", "Old", "New", "Reason"]} empty="no config changes yet">
        {(data ?? []).map((r) => (
          <tr key={r.id} className="hover:bg-surface2/60">
            <Td>{fmtTs(r.ts)}</Td>
            <Td>{r.username}</Td>
            <Td className="font-medium">{r.key}</Td>
            <Td className="num text-muted">{JSON.stringify(r.old_value)}</Td>
            <Td className="num">{JSON.stringify(r.new_value)}</Td>
            <Td className="max-w-xs truncate text-muted">{r.reason || "—"}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}
