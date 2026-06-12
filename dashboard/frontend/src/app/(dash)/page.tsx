"use client";

/** Overview / command center: one glance = full system state. */

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { fmtCompact, fmtInr, fmtNum, fmtPct, fmtTs, pnlClass } from "@/lib/format";
import { useReauthAction } from "@/components/reauth";
import {
  Badge as UIBadge,
  Button as UIButton,
  Card as UICard,
  Gauge as UIGauge,
  Stat as UIStat,
} from "@/components/ui";
import { EChart, lineOption } from "@/components/charts";
import { useOverview } from "@/components/shell";

type EquityPoint = { ts: string; equity: number };

export default function OverviewPage() {
  const { data: ov } = useOverview();
  const qc = useQueryClient();
  const { guard, modal } = useReauthAction();
  const { data: equity } = useQuery<EquityPoint[]>({
    queryKey: ["equity", "curve-overview"],
    queryFn: () => apiGet("/equity-curve?max_points=600"),
  });

  const dec = ov?.decision as
    | {
        ts: string; tier: string; regime: string;
        regime_probs: Record<string, number>;
        vol_scaler: number; risk_frac_eff: number;
        halted: boolean; kill_reason: string | null;
      }
    | null
    | undefined;

  return (
    <div className="space-y-3">
      {modal}
      {(dec?.halted || dec?.kill_reason) && (
        <div className="rounded border border-down bg-down/15 px-3 py-2 text-sm font-semibold text-down">
          {dec.kill_reason
            ? `KILL SWITCH ACTIVE: ${dec.kill_reason}`
            : "ENGINE HALTED (reconciliation) — no orders will be placed"}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <UICard><UIStat label="Equity" value={fmtCompact(ov?.equity.value)}
          sub={ov?.equity.ts ? fmtTs(ov.equity.ts as string) : "no data"} /></UICard>
        <UICard><UIStat label="Today P&L"
          value={fmtCompact(ov?.equity.today_pnl)}
          valueClass={pnlClass(ov?.equity.today_pnl)} /></UICard>
        <UICard><UIStat label="Unrealized"
          value={fmtCompact(ov?.equity.unrealized_pnl as number | null)}
          valueClass={pnlClass(ov?.equity.unrealized_pnl as number | null)} /></UICard>
        <UICard><UIStat label="Open positions" value={ov?.open_positions ?? "—"} /></UICard>
        <UICard><UIStat label="Tier" value={dec?.tier ?? "—"}
          sub={`risk_frac_eff ${dec ? fmtNum(dec.risk_frac_eff, 4) : "—"}`} /></UICard>
        <UICard><UIStat label="Regime" value={dec?.regime ?? "—"}
          sub={dec ? Object.entries(dec.regime_probs ?? {})
            .map(([k, v]) => `${k.split("_")[1] ?? k} ${fmtPct(v, 0)}`)
            .join(" · ") : ""} /></UICard>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <UICard title="Equity (live)" className="lg:col-span-2">
          {equity && equity.length > 1 ? (
            <EChart
              height={240}
              option={lineOption(
                [{ name: "equity", data: equity.map((p) => [p.ts, p.equity]), area: true }],
                (v) => fmtCompact(v)
              )}
            />
          ) : (
            <div className="py-16 text-center text-xs text-muted">
              no equity history yet
            </div>
          )}
        </UICard>

        <div className="space-y-3">
          <UICard title="Exposure">
            <div className="space-y-2.5">
              <UIGauge label="Gross / equity"
                value={ratio(ov?.equity.gross_exposure as number | null, ov?.equity.value)}
                format={(v) => fmtPct(v)} />
              <UIGauge label="Net / equity"
                value={ratio(ov?.equity.net_exposure as number | null, ov?.equity.value)}
                format={(v) => fmtPct(v)} />
              <div className="flex justify-between text-[11px] text-muted">
                <span>vol scaler {dec ? fmtNum(dec.vol_scaler, 2) : "—"}</span>
                <span>cash {fmtCompact(ov?.equity.cash as number | null)}</span>
              </div>
            </div>
          </UICard>

          <UICard title="Controls">
            <div className="flex flex-wrap gap-2">
              <UIButton
                tone="danger"
                onClick={() =>
                  guard({
                    title: "KILL — flatten everything",
                    danger: true,
                    summary: (
                      <span>
                        Arms the kill switch: the engine flattens <b>all positions
                        at market</b> on the next bar and stops taking risk until
                        manually re-armed. Mode: <b>{ov?.mode}</b>.
                      </span>
                    ),
                    run: () => apiPost("/control/kill", { action: "kill", reason: "operator kill from overview" }),
                    onDone: () => qc.invalidateQueries(),
                  })
                }
              >
                ⛔ KILL
              </UIButton>
              <UIButton
                onClick={() =>
                  guard({
                    title: "Re-arm after kill",
                    summary: <span>Clears the max-drawdown / manual kill latch. The engine resumes normal risk-taking next bar.</span>,
                    run: () => apiPost("/control/kill", { action: "rearm_dd_kill", reason: "operator rearm" }),
                    onDone: () => qc.invalidateQueries(),
                  })
                }
              >
                Re-arm
              </UIButton>
              <Link href="/settings"><UIButton>Mode & capital →</UIButton></Link>
            </div>
          </UICard>

          <UICard title="Capital controls">
            <div className="space-y-1 text-xs">
              <Row k="Paper capital" v={fmtInr(ov?.capital_controls?.paper_capital as number | null)} />
              <Row k="Deployable cap (frac)" v={ov?.capital_controls?.deployable_cap_frac != null ? fmtPct(ov.capital_controls.deployable_cap_frac as number) : "—"} />
              <Row k="Deployable cap (abs)" v={fmtInr(ov?.capital_controls?.deployable_cap_abs as number | null)} />
            </div>
          </UICard>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <UICard title="Recent risk events">
          <EventList
            rows={(ov?.recent_risk_events ?? []).map((r) => ({
              id: r.id as number, ts: r.ts as string,
              badge: r.kind as string,
              tone: (r.severity === "crit" ? "red" : r.severity === "warn" ? "amber" : "neutral") as "red" | "amber" | "neutral",
              text: `${r.symbol ?? ""} ${r.cause ?? r.rule ?? ""}`,
            }))}
            empty="no risk events"
          />
        </UICard>
        <UICard title="Recent alerts">
          <EventList
            rows={(ov?.recent_alerts ?? []).map((a) => ({
              id: a.id as number, ts: a.ts as string,
              badge: a.kind as string,
              tone: (a.severity === "crit" ? "red" : a.severity === "warn" ? "amber" : "neutral") as "red" | "amber" | "neutral",
              text: `${a.title}${a.delivered ? "" : " (undelivered)"}`,
            }))}
            empty="no alerts"
          />
        </UICard>
      </div>
    </div>
  );
}

function ratio(a: number | null | undefined, b: number | null | undefined) {
  if (a == null || b == null || b === 0) return null;
  return a / b;
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted">{k}</span>
      <span className="num">{v}</span>
    </div>
  );
}

function EventList({
  rows,
  empty,
}: {
  rows: { id: number; ts: string; badge: string; tone: "red" | "amber" | "neutral"; text: string }[];
  empty: string;
}) {
  if (!rows.length)
    return <div className="py-4 text-center text-xs text-muted">{empty}</div>;
  return (
    <ul className="space-y-1">
      {rows.map((r) => (
        <li key={r.id} className="flex items-baseline gap-2 text-xs">
          <span className="num shrink-0 text-muted">{fmtTs(r.ts).slice(5, 16)}</span>
          <UIBadge tone={r.tone}>{r.badge}</UIBadge>
          <span className="truncate">{r.text}</span>
        </li>
      ))}
    </ul>
  );
}
