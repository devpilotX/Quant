"use client";

/** Down-shock forward tracker — read-only research monitor for the Pillar-4
 * down-shock underreaction lead. It trades nothing; it shows the signal's honest
 * forward, out-of-sample record (the in-sample backtest passed 4/5 gate criteria
 * but failed the deflated Sharpe, so it must prove itself forward). */

import { useQuery } from "@tanstack/react-query";

import { EChart, lineOption } from "@/components/charts";
import { Card, Spinner, Stat } from "@/components/ui";
import { apiGet } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";

type DS = {
  available: boolean;
  state?: {
    tracking_start: string;
    forward_events: number;
    forward_days: number;
    forward_cum_return: number;
    forward_sharpe_ann: number | null;
    last_run?: string;
    recent_events?: [string, string][];
  };
  history?: { date: string; cum_return: number; sharpe: number | null }[];
};

export default function DownshockPage() {
  const { data, isLoading } = useQuery<DS>({
    queryKey: ["downshock"],
    queryFn: () => apiGet("/downshock"),
    refetchInterval: 60_000,
  });

  if (isLoading) return <Spinner />;
  if (!data?.available) {
    return (
      <Card title="Down-shock forward tracker">
        <p className="text-sm text-muted">
          Tracker not running in this environment — the daily VPS job writes its
          record. Nothing to show yet.
        </p>
      </Card>
    );
  }

  const s = data.state!;
  const cum = s.forward_cum_return;
  const curve: [string, number][] = (data.history ?? []).map((h) => [
    h.date,
    h.cum_return * 100,
  ]);

  return (
    <div className="space-y-3">
      <Card title="Down-shock forward tracker — research monitor (trades nothing)">
        <p className="mb-3 text-[11px] leading-relaxed text-muted">
          Frozen-config (z3.5 / hold10, market-neutral short) down-shock
          underreaction lead. It FAILED the deflation gate in-sample (0.46 &lt;
          0.95) — this is its honest, OUT-OF-SAMPLE forward-only record. Zero
          risk; nothing deploys until it earns it forward.
        </p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="Tracking since"
            value={s.tracking_start}
            sub={`last run ${s.last_run ?? "—"}`}
          />
          <Stat
            label="Forward events"
            value={s.forward_events}
            sub={`${s.forward_days} active days`}
          />
          <Stat
            label="Cum. return"
            value={fmtPct(cum, 2)}
            valueClass={cum > 0 ? "text-up" : cum < 0 ? "text-down" : ""}
          />
          <Stat
            label="Forward Sharpe"
            value={s.forward_sharpe_ann == null ? "—" : fmtNum(s.forward_sharpe_ann, 2)}
          />
        </div>
      </Card>

      <Card title="Forward equity curve (cumulative %)" pad={false}>
        {curve.length > 1 ? (
          <div className="p-2">
            <EChart
              height={300}
              option={lineOption(
                [{ name: "Forward cum %", data: curve, area: true }],
                (v) => `${fmtNum(v, 1)}%`
              )}
            />
          </div>
        ) : (
          <p className="p-3 text-sm text-muted">
            No forward down-shock events yet — watching. The curve appears once
            events occur.
          </p>
        )}
      </Card>

      {s.recent_events && s.recent_events.length > 0 && (
        <Card title="Recent forward events">
          <ul className="space-y-1 text-sm">
            {s.recent_events
              .slice()
              .reverse()
              .map(([sym, d], i) => (
                <li key={`${sym}-${d}-${i}`} className="flex justify-between">
                  <span className="font-medium">{sym}</span>
                  <span className="num text-muted">{d}</span>
                </li>
              ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
