"use client";

/** Live positions + order book. */

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { fmtInr, fmtNum, fmtQty, fmtTs, pnlClass } from "@/lib/format";
import { Badge, Button, Card, Spinner, Table, Td } from "@/components/ui";

type Position = {
  id: number; symbol: string; strategy: string; qty: number;
  avg_price: number; last_price: number | null; unrealized_pnl: number | null;
  realized_pnl: number; fees_paid: number; stop_distance: number;
  status: string; opened_at: string; closed_at: string | null;
};

type Order = {
  id: number; client_order_id: string; ts: string; strategy: string;
  symbol: string; side: string; qty: number; filled_qty: number;
  style: string; urgency: string; ref_price: number; status: string;
  reject_reason: string; reason: string;
};

const ORDER_TONE: Record<string, "neutral" | "green" | "red" | "amber" | "blue"> = {
  FILLED: "green", REJECTED: "red", CANCELLED: "neutral",
  NEW: "blue", SUBMITTED: "blue", PARTIAL: "amber",
};

export default function PositionsPage() {
  const [tab, setTab] = useState<"open" | "closed">("open");
  const { data: positions, isLoading } = useQuery<Position[]>({
    queryKey: ["positions", tab],
    queryFn: () => apiGet(`/positions?status=${tab}`),
  });
  const { data: orders } = useQuery<{ total: number; rows: Order[] }>({
    queryKey: ["orders", "recent"],
    queryFn: () => apiGet("/orders?limit=100"),
  });

  return (
    <div className="space-y-3">
      <Card
        title={`Positions (${tab})`}
        right={
          <div className="flex gap-1">
            {(["open", "closed"] as const).map((t) => (
              <Button key={t} tone={tab === t ? "primary" : "default"} onClick={() => setTab(t)}>
                {t}
              </Button>
            ))}
          </div>
        }
        pad={false}
      >
        {isLoading ? (
          <Spinner />
        ) : (
          <Table
            cols={["Symbol", "Strategy", { label: "Qty", align: "right" },
              { label: "Avg px", align: "right" }, { label: "Last", align: "right" },
              { label: "Unreal P&L", align: "right" }, { label: "Real P&L", align: "right" },
              { label: "Fees", align: "right" }, { label: "Stop dist", align: "right" },
              "Opened", "Why?"]}
            empty={`no ${tab} positions`}
          >
            {(positions ?? []).map((p) => (
              <tr key={p.id} className="hover:bg-surface2/60">
                <Td className="font-medium">{p.symbol}</Td>
                <Td>{p.strategy || "—"}</Td>
                <Td right className={p.qty > 0 ? "text-up" : p.qty < 0 ? "text-down" : ""}>
                  {fmtQty(p.qty)}
                </Td>
                <Td right>{fmtNum(p.avg_price)}</Td>
                <Td right>{p.last_price != null ? fmtNum(p.last_price) : "—"}</Td>
                <Td right className={pnlClass(p.unrealized_pnl)}>{fmtInr(p.unrealized_pnl)}</Td>
                <Td right className={pnlClass(p.realized_pnl)}>{fmtInr(p.realized_pnl)}</Td>
                <Td right>{fmtInr(p.fees_paid)}</Td>
                <Td right>{fmtNum(p.stop_distance)}</Td>
                <Td>{fmtTs(p.opened_at).slice(0, 16)}</Td>
                <Td>
                  <Link className="text-accent hover:underline" href={`/explain?position=${p.id}`}>
                    explain →
                  </Link>
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title={`Order book (latest ${orders?.rows.length ?? 0} of ${orders?.total ?? 0})`} pad={false}>
        <Table
          cols={["Time", "Symbol", "Side", { label: "Qty", align: "right" },
            { label: "Filled", align: "right" }, { label: "Ref px", align: "right" },
            "Style", "Urgency", "Strategy", "Status", "Reason"]}
          empty="no orders yet"
        >
          {(orders?.rows ?? []).map((o) => (
            <tr key={o.id} className="hover:bg-surface2/60">
              <Td>{fmtTs(o.ts).slice(5, 19)}</Td>
              <Td className="font-medium">{o.symbol}</Td>
              <Td className={o.side === "BUY" ? "text-up" : "text-down"}>{o.side}</Td>
              <Td right>{fmtQty(o.qty)}</Td>
              <Td right>{fmtQty(o.filled_qty)}</Td>
              <Td right>{fmtNum(o.ref_price)}</Td>
              <Td>{o.style}</Td>
              <Td>{o.urgency !== "NORMAL" ? <Badge tone="amber">{o.urgency}</Badge> : "—"}</Td>
              <Td>{o.strategy || "—"}</Td>
              <Td><Badge tone={ORDER_TONE[o.status] ?? "neutral"}>{o.status}</Badge></Td>
              <Td className="max-w-56 truncate text-muted" >
                {o.reject_reason || o.reason || "—"}
              </Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
