"use client";

/** AppShell: sidebar nav + always-visible status bar.
 * The status bar is the safety surface: MODE badge (paper/live), engine
 * heartbeat, market session, WS connection — visible on every page, always.
 */

import clsx from "clsx";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { fmtAge, fmtCompact, pnlClass } from "@/lib/format";
import { useLive } from "@/lib/live";
import { Badge } from "@/components/ui";

const NAV: [string, string][] = [
  ["/", "Overview"],
  ["/positions", "Positions & Orders"],
  ["/pnl", "P&L"],
  ["/explain", "Explainability"],
  ["/strategies", "Strategies"],
  ["/regime", "Regime"],
  ["/risk", "Risk"],
  ["/capital", "Capital & Tiers"],
  ["/backtests", "Backtests"],
  ["/downshock", "Down-shock"],
  ["/logs", "Logs & Alerts"],
  ["/db", "Database"],
  ["/settings", "Settings"],
];

export type Overview = {
  server_ts: string;
  mode: string;
  mode_requested?: string | null;
  engine: {
    status: string;
    heartbeat_age_s: number | null;
    stale: boolean;
    market_open: boolean;
    host: string;
    detail: Record<string, unknown>;
  };
  equity: {
    value: number | null;
    ts: string | null;
    cash: number | null;
    gross_exposure: number | null;
    net_exposure: number | null;
    unrealized_pnl: number | null;
    today_pnl: number | null;
  };
  open_positions: number;
  decision: {
    id: number;
    ts: string;
    tier: string;
    regime: string;
    regime_probs: Record<string, number>;
    vol_scaler: number;
    risk_frac_eff: number;
    halted: boolean;
    kill_reason: string | null;
  } | null;
  capital_controls: {
    paper_capital: number | null;
    deployable_cap_frac: number | null;
    deployable_cap_abs: number | null;
  };
  recent_alerts: {
    id: number; ts: string; severity: string; kind: string;
    title: string; delivered: boolean;
  }[];
  recent_risk_events: {
    id: number; ts: string; kind: string; rule: string;
    severity: string; cause: string; symbol: string | null;
  }[];
};

export function useOverview() {
  return useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: () => apiGet("/overview"),
    refetchInterval: 5000, // fallback when WS is down; WS invalidates sooner
  });
}

export function ModeBadge({ mode, requested }: { mode?: string; requested?: string | null }) {
  if (!mode) return <Badge>…</Badge>;
  const live = mode === "live";
  return (
    <span className="flex items-center gap-1.5">
      <Badge
        tone={live ? "red" : "green"}
        className={clsx("px-2.5 py-1 text-xs font-bold", live && "live-dot")}
      >
        {live ? "● LIVE / REAL MONEY" : "● PAPER"}
      </Badge>
      {requested && requested !== mode && (
        <Badge tone="amber">switch to {requested} pending</Badge>
      )}
    </span>
  );
}

function ConnDot() {
  const { status } = useLive();
  const map = {
    live: ["bg-up", "stream live"],
    connecting: ["bg-warn", "connecting…"],
    down: ["bg-down", "stream DOWN — data may be stale"],
  } as const;
  const [cls, label] = map[status];
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted" title={label}>
      <span className={clsx("h-2 w-2 rounded-full", cls, status === "live" && "live-dot")} />
      {label}
    </span>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { data: ov } = useOverview();

  const engineTone = !ov
    ? "neutral"
    : ov.engine.stale
      ? "red"
      : ov.engine.status === "running"
        ? "green"
        : ov.engine.status === "idle"
          ? "blue"
          : "amber";

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-44 shrink-0 flex-col border-r border-line bg-surface">
        <div className="border-b border-line px-3 py-3">
          <div className="text-sm font-bold tracking-tight">quantsys</div>
          <div className="text-[10px] text-muted">control plane</div>
        </div>
        <nav className="flex-1 overflow-y-auto py-2">
          {NAV.map(([href, label]) => (
            <Link
              key={href}
              href={href}
              className={clsx(
                "block px-3 py-1.5 text-xs transition-colors",
                path === href
                  ? "border-r-2 border-accent bg-accent/10 font-medium text-accent"
                  : "text-muted hover:bg-surface2 hover:text-foreground"
              )}
            >
              {label}
            </Link>
          ))}
        </nav>
        <button
          onClick={async () => {
            try {
              await apiPost("/auth/logout");
            } finally {
              router.push("/login");
            }
          }}
          className="border-t border-line px-3 py-2 text-left text-xs text-muted hover:text-foreground"
        >
          Sign out
        </button>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex items-center gap-4 border-b border-line bg-background/95 px-4 py-2 backdrop-blur">
          <ModeBadge mode={ov?.mode} requested={ov?.mode_requested} />
          <Badge tone={engineTone}>
            engine: {ov ? ov.engine.status : "…"}
            {ov?.engine.stale && " (STALE)"}
          </Badge>
          <Badge tone={ov?.engine.market_open ? "blue" : "neutral"}>
            {ov?.engine.market_open ? "market open" : "market closed"}
          </Badge>
          <span className="text-[11px] text-muted">
            hb {ov ? fmtAge(ov.engine.heartbeat_age_s) : "…"}
          </span>
          <div className="ml-auto flex items-center gap-4">
            <span className="num text-sm font-semibold">
              {fmtCompact(ov?.equity.value)}
            </span>
            <span className={clsx("num text-xs", pnlClass(ov?.equity.today_pnl))}>
              {ov?.equity.today_pnl != null && ov.equity.today_pnl >= 0 ? "+" : ""}
              {fmtCompact(ov?.equity.today_pnl)} today
            </span>
            <ConnDot />
          </div>
        </header>
        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>
    </div>
  );
}
