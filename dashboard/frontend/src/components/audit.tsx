"use client";

/** Renders an engine audit trail (AuditEvent list) — the raw decision story.
 * Shown verbatim: stage/rule/symbol/before->after/detail. */

import { Badge } from "@/components/ui";

export type AuditEvent = {
  stage: string;
  rule: string;
  detail: string;
  symbol?: string | null;
  before?: number | null;
  after?: number | null;
};

const STAGE_TONE: Record<string, "neutral" | "green" | "red" | "amber" | "blue"> = {
  engine: "red",
  risk: "amber",
  "risk.gross_cap": "amber",
  sizing: "blue",
  vol_target: "blue",
  regime: "green",
  kelly: "green",
  orders: "neutral",
};

export function AuditTrail({ events }: { events: AuditEvent[] }) {
  if (!events?.length) {
    return <div className="py-3 text-center text-xs text-muted">no audit events</div>;
  }
  return (
    <ol className="space-y-1">
      {events.map((e, i) => (
        <li
          key={i}
          className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded border border-line/60 bg-surface2/50 px-2 py-1 text-xs"
        >
          <Badge tone={STAGE_TONE[e.stage] ?? STAGE_TONE[e.stage.split(".")[0]] ?? "neutral"}>
            {e.stage}
          </Badge>
          <span className="font-medium">{e.rule}</span>
          {e.symbol && <span className="num text-muted">{e.symbol}</span>}
          {e.before != null && e.after != null && (
            <span className="num text-muted">
              {fmtShort(e.before)} → {fmtShort(e.after)}
            </span>
          )}
          <span className="text-muted">{e.detail}</span>
        </li>
      ))}
    </ol>
  );
}

function fmtShort(v: number): string {
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(4);
}
