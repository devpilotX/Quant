"use client";

/** Trade explainability — the centerpiece. Pick any trade: why entered
 * (signal, regime, full sizing math) and why exited, straight from the
 * persisted decision audit trail. */

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtInr, fmtNum, fmtPct, fmtQty, fmtTs, pnlClass } from "@/lib/format";
import { AuditTrail, type AuditEvent } from "@/components/audit";
import { Badge, Card, Spinner, Table, Td } from "@/components/ui";

type PositionLite = {
  id: number; symbol: string; strategy: string; qty: number; status: string;
  opened_at: string; closed_at: string | null; realized_pnl: number;
};

type Explain = {
  position: {
    id: number; mode: string; symbol: string; strategy: string; qty: number;
    avg_price: number; status: string; opened_at: string; closed_at: string | null;
    realized_pnl: number; fees_paid: number; stop_distance: number;
  };
  entry: { rationale: Rationale | null; decision: DecisionSide | null };
  exit: { rationale: ExitRationale | null; decision: DecisionSide | null };
  fills: { ts: string; qty: number; price: number; fees_total: number; slippage: number }[];
};

type Rationale = {
  strategy?: string;
  signal?: {
    direction: number; stop_distance: number; expected_edge_R: number;
    tag: string; horizon_bars: number | null;
  } | null;
  regime?: { label: string; probs: Record<string, number>; risk_scaler?: number };
  sizing?: {
    risk_frac_eff: number; kelly: Record<string, number>;
    vol_scaler: number; tier: string;
  };
  audit?: AuditEvent[];
};

type ExitRationale = {
  urgency?: string; reason?: string; kill_reason?: string | null;
  regime?: { label: string; probs: Record<string, number> };
};

type DecisionSide = {
  id: number; ts: string; equity: number; tier: string; regime: string;
  regime_probs: Record<string, number>; regime_source: string;
  risk_frac_eff: number; vol_scaler: number; kelly: Record<string, number>;
  signals_for_symbol: unknown[]; audit_for_symbol: AuditEvent[];
  full_audit: AuditEvent[];
};

export default function ExplainPageWrapper() {
  return (
    <Suspense fallback={<Spinner />}>
      <ExplainPage />
    </Suspense>
  );
}

function ExplainPage() {
  const params = useSearchParams();
  const preselect = params.get("position");
  const [selected, setSelected] = useState<number | null>(
    preselect ? Number(preselect) : null
  );

  const { data: closed } = useQuery<PositionLite[]>({
    queryKey: ["positions", "closed-explain"],
    queryFn: () => apiGet("/positions?status=closed&limit=100"),
  });
  const { data: open } = useQuery<PositionLite[]>({
    queryKey: ["positions", "open-explain"],
    queryFn: () => apiGet("/positions?status=open"),
  });
  const all = [...(open ?? []), ...(closed ?? [])];
  const active = selected ?? all[0]?.id ?? null;

  const { data: ex, isLoading } = useQuery<Explain>({
    queryKey: ["decisions", "explain", active],
    queryFn: () => apiGet(`/trades/${active}/explain`),
    enabled: active !== null,
  });

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[320px_1fr]">
      <Card title="Trades" pad={false} className="max-h-[80vh] overflow-y-auto">
        <ul className="divide-y divide-line/60">
          {all.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => setSelected(p.id)}
                className={`w-full px-3 py-2 text-left text-xs hover:bg-surface2 ${
                  active === p.id ? "bg-accent/10" : ""
                }`}
              >
                <div className="flex justify-between">
                  <span className="font-medium">{p.symbol}</span>
                  <span className={pnlClass(p.status === "open" ? null : p.realized_pnl)}>
                    {p.status === "open" ? <Badge tone="blue">open</Badge> : fmtInr(p.realized_pnl)}
                  </span>
                </div>
                <div className="flex justify-between text-muted">
                  <span>{p.strategy} · qty {fmtQty(p.qty)}</span>
                  <span>{fmtTs(p.opened_at).slice(5, 16)}</span>
                </div>
              </button>
            </li>
          ))}
          {all.length === 0 && (
            <li className="px-3 py-6 text-center text-xs text-muted">
              no trades yet — they appear as soon as the engine trades
            </li>
          )}
        </ul>
      </Card>

      <div className="min-w-0 space-y-3">
        {isLoading && <Spinner />}
        {ex && (
          <>
            <Card title={`${ex.position.symbol} — ${ex.position.status} · ${ex.position.mode}`}>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
                <KV k="Strategy" v={ex.position.strategy || "—"} />
                <KV k="Qty" v={fmtQty(ex.position.qty)} />
                <KV k="Avg price" v={fmtNum(ex.position.avg_price)} />
                <KV k="Realized P&L" v={fmtInr(ex.position.realized_pnl)}
                  cls={pnlClass(ex.position.realized_pnl)} />
                <KV k="Fees paid" v={fmtInr(ex.position.fees_paid)} />
                <KV k="Stop distance" v={fmtNum(ex.position.stop_distance)} />
              </div>
            </Card>

            <SideCard
              title="WHY ENTERED"
              tone="entry"
              rationale={ex.entry.rationale}
              decision={ex.entry.decision}
              symbol={ex.position.symbol}
            />

            {(ex.exit.rationale || ex.exit.decision) && (
              <SideCard
                title="WHY EXITED"
                tone="exit"
                exitRationale={ex.exit.rationale}
                decision={ex.exit.decision}
                symbol={ex.position.symbol}
              />
            )}

            <Card title={`Fills (${ex.fills.length})`} pad={false}>
              <Table cols={["Time", { label: "Qty", align: "right" },
                { label: "Price", align: "right" }, { label: "Fees", align: "right" },
                { label: "Slippage", align: "right" }]}>
                {ex.fills.map((f, i) => (
                  <tr key={i}>
                    <Td>{fmtTs(f.ts)}</Td>
                    <Td right className={f.qty > 0 ? "text-up" : "text-down"}>{fmtQty(f.qty)}</Td>
                    <Td right>{fmtNum(f.price)}</Td>
                    <Td right>{fmtInr(f.fees_total, true)}</Td>
                    <Td right>{fmtNum(f.slippage)}</Td>
                  </tr>
                ))}
              </Table>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

function KV({ k, v, cls }: { k: string; v: React.ReactNode; cls?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">{k}</div>
      <div className={`num text-sm font-medium ${cls ?? ""}`}>{v}</div>
    </div>
  );
}

function SideCard({
  title, tone, rationale, exitRationale, decision, symbol,
}: {
  title: string;
  tone: "entry" | "exit";
  rationale?: Rationale | null;
  exitRationale?: ExitRationale | null;
  decision: DecisionSide | null;
  symbol: string;
}) {
  const sig = rationale?.signal;
  const sizing = rationale?.sizing;
  return (
    <Card
      title={
        <span className={tone === "entry" ? "text-up" : "text-warn"}>{title}</span>
      }
      right={
        decision && (
          <span className="text-[11px] text-muted">
            decision #{decision.id} · {fmtTs(decision.ts)}
          </span>
        )
      }
    >
      <div className="space-y-3">
        {tone === "exit" && exitRationale && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge tone={exitRationale.kill_reason ? "red" : "amber"}>
              {exitRationale.kill_reason
                ? `kill: ${exitRationale.kill_reason}`
                : exitRationale.urgency ?? "exit"}
            </Badge>
            <span>{exitRationale.reason}</span>
          </div>
        )}

        {sig && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <KV k="Signal direction" v={fmtNum(sig.direction, 3)}
              cls={sig.direction > 0 ? "text-up" : "text-down"} />
            <KV k="Stop distance" v={fmtNum(sig.stop_distance)} />
            <KV k="Expected edge (R)" v={fmtNum(sig.expected_edge_R, 2)} />
            <KV k="Horizon bars" v={sig.horizon_bars ?? "—"} />
            <KV k="Tag" v={sig.tag || "—"} />
          </div>
        )}

        {(rationale?.regime || decision) && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted">regime:</span>
            <Badge tone="blue">
              {rationale?.regime?.label ?? decision?.regime}
              {decision?.regime_source ? ` (${decision.regime_source})` : ""}
            </Badge>
            {Object.entries(rationale?.regime?.probs ?? decision?.regime_probs ?? {}).map(
              ([k, v]) => (
                <span key={k} className="num text-muted">
                  {k} {fmtPct(v as number, 0)}
                </span>
              )
            )}
          </div>
        )}

        {sizing && (
          <div className="rounded border border-line bg-surface2/60 p-2">
            <div className="mb-1 text-[10px] uppercase tracking-wider text-muted">
              Sizing math (risk_frac → Kelly → vol scaler → caps → lots)
            </div>
            <div className="num flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <span>tier <b>{sizing.tier}</b></span>
              <span>risk_frac_eff <b>{fmtNum(sizing.risk_frac_eff, 4)}</b></span>
              <span>
                kelly[{rationale?.strategy}] <b>{fmtNum(sizing.kelly?.[rationale?.strategy ?? ""], 4)}</b>
              </span>
              <span>vol_scaler <b>{fmtNum(sizing.vol_scaler, 3)}</b></span>
            </div>
          </div>
        )}

        <details open={tone === "entry"}>
          <summary className="cursor-pointer text-[11px] uppercase tracking-wider text-muted">
            Audit trail for {symbol} (every adjustment & veto in this pass)
          </summary>
          <div className="mt-2">
            <AuditTrail
              events={decision?.audit_for_symbol ?? rationale?.audit ?? []}
            />
          </div>
        </details>

        {decision && (
          <details>
            <summary className="cursor-pointer text-[11px] uppercase tracking-wider text-muted">
              Full decision audit ({decision.full_audit.length} events)
            </summary>
            <div className="mt-2">
              <AuditTrail events={decision.full_audit} />
            </div>
          </details>
        )}
      </div>
    </Card>
  );
}
