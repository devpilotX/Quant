"use client";

/** Settings: mode switch (paper <-> LIVE), capital, risk overrides, command
 * history. Every mutation: re-auth modal -> command queue -> engine ack.
 * Going LIVE additionally requires typing the confirmation phrase. */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { fmtCompact, fmtTs } from "@/lib/format";
import { useReauthAction } from "@/components/reauth";
import { useOverview } from "@/components/shell";
import { Badge, Button, Card, Input, Table, Td } from "@/components/ui";

type Command = {
  id: number; created_at: string; created_by: string; kind: string;
  payload: Record<string, unknown>; status: string;
  result: Record<string, unknown> | null;
};

const TUNABLE_KEYS = [
  "sizing.base_risk_frac",
  "vol_target.annual_vol_target",
  "drawdown.daily_loss_limit",
  "drawdown.max_drawdown",
  "exposure.gross_cap",
  "exposure.net_cap",
  "exposure.instrument_cap",
  "exposure.sector_cap",
  "engine.min_order_notional",
];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: ov } = useOverview();
  const { guard, modal } = useReauthAction();
  const { data: commands } = useQuery<Command[]>({
    queryKey: ["commands"],
    queryFn: () => apiGet("/control/commands?limit=50"),
  });

  const [phrase, setPhrase] = useState("");
  const [capFrac, setCapFrac] = useState("0.5");
  const [capAbs, setCapAbs] = useState("");
  const [flatten, setFlatten] = useState(false);
  const [paperCap, setPaperCap] = useState("");
  const [cfgKey, setCfgKey] = useState(TUNABLE_KEYS[0]);
  const [cfgVal, setCfgVal] = useState("");

  const live = ov?.mode === "live";
  const targetMode = live ? "paper" : "live";
  const invalidate = () => qc.invalidateQueries();

  return (
    <div className="space-y-3">
      {modal}

      <Card
        title={
          <span className={live ? "text-down" : "text-up"}>
            Trading mode — currently {ov?.mode?.toUpperCase() ?? "…"}
          </span>
        }
      >
        <div className="space-y-3">
          {!live && (
            <div className="rounded border border-down/40 bg-down/10 p-3 text-xs">
              <b className="text-down">Switching to LIVE trades real money.</b>{" "}
              Requirements enforced server-side: fresh re-auth, the typed phrase{" "}
              <code className="rounded bg-surface2 px-1">GO LIVE REAL MONEY</code>, an
              explicit deployable-capital cap, and no open paper positions (or
              explicit flatten). The engine completes the switch and flips the badge —
              not this page. <b>Note:</b> the live broker adapter is not installed
              yet; the engine will reject the command until the execution layer
              ships. The full safety chain is still exercised end-to-end.
            </div>
          )}
          <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
            {!live && (
              <Input placeholder='type: GO LIVE REAL MONEY' value={phrase}
                onChange={(e) => setPhrase(e.target.value)} />
            )}
            <Input placeholder="deployable cap fraction (0–1)" value={capFrac}
              onChange={(e) => setCapFrac(e.target.value)} />
            <Input placeholder="deployable cap ₹ (optional)" value={capAbs}
              onChange={(e) => setCapAbs(e.target.value)} />
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={flatten}
                onChange={(e) => setFlatten(e.target.checked)} />
              flatten open positions first
            </label>
          </div>
          <Button
            tone={live ? "primary" : "danger"}
            disabled={!live && phrase !== "GO LIVE REAL MONEY"}
            onClick={() =>
              guard({
                title: `Switch to ${targetMode.toUpperCase()}`,
                danger: true,
                summary: (
                  <div className="space-y-1">
                    <div>mode: <b>{ov?.mode}</b> → <b>{targetMode.toUpperCase()}</b></div>
                    <div>deployable cap: frac {capFrac || "—"} · abs {capAbs || "—"}</div>
                    <div>flatten first: <b>{String(flatten)}</b></div>
                    <div>The engine acks the command and performs the transition.</div>
                  </div>
                ),
                run: () =>
                  apiPost("/control/mode", {
                    target_mode: targetMode,
                    confirmation_phrase: phrase,
                    flatten_first: flatten,
                    deployable_cap_frac: capFrac ? Number(capFrac) : null,
                    deployable_cap_abs: capAbs ? Number(capAbs) : null,
                    reason: "operator mode switch",
                  }),
                onDone: invalidate,
              })
            }
          >
            {live ? "Switch back to PAPER" : "⚠ GO LIVE (REAL MONEY)"}
          </Button>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title="Paper capital (simulate any tier)">
          <div className="flex gap-2">
            <Input placeholder="e.g. 50000000 for ₹5cr" value={paperCap}
              onChange={(e) => setPaperCap(e.target.value)} />
            <Button
              tone="primary"
              disabled={!paperCap || Number.isNaN(Number(paperCap))}
              onClick={() =>
                guard({
                  title: "Set paper capital",
                  summary: (
                    <span>
                      Virtual starting capital becomes <b>{fmtCompact(Number(paperCap))}</b>.
                      Engine rejects this while paper positions are open.
                    </span>
                  ),
                  run: () => apiPost("/control/paper-capital", { capital: Number(paperCap) }),
                  onDone: invalidate,
                })
              }
            >
              Apply
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-muted">
            current: {fmtCompact(ov?.capital_controls?.paper_capital)} · range ₹1L – ₹20cr
          </p>
        </Card>

        <Card title="Risk parameter override (versioned, allowlisted)">
          <div className="flex gap-2">
            <select
              value={cfgKey}
              onChange={(e) => setCfgKey(e.target.value)}
              className="rounded border border-line bg-surface2 px-2 py-1.5 text-xs"
            >
              {TUNABLE_KEYS.map((k) => <option key={k}>{k}</option>)}
            </select>
            <Input placeholder="new value" value={cfgVal}
              onChange={(e) => setCfgVal(e.target.value)} />
            <Button
              tone="primary"
              disabled={!cfgVal || Number.isNaN(Number(cfgVal))}
              onClick={() =>
                guard({
                  title: `Override ${cfgKey}`,
                  danger: true,
                  summary: (
                    <span>
                      <code>{cfgKey}</code> → <b>{cfgVal}</b>. Engine rebuilds with the
                      new config and state round-trips. Recorded in config_versions.
                    </span>
                  ),
                  run: () => apiPost("/control/config", {
                    key: cfgKey, value: Number(cfgVal), reason: "operator override",
                  }),
                  onDone: invalidate,
                })
              }
            >
              Apply
            </Button>
          </div>
        </Card>
      </div>

      <Card title="Command queue (operator → engine)" pad={false}>
        <Table cols={["#", "Created", "By", "Command", "Payload", "Status", "Result"]}
          empty="no commands issued yet">
          {(commands ?? []).map((c) => (
            <tr key={c.id} className="hover:bg-surface2/60">
              <Td>#{c.id}</Td>
              <Td>{fmtTs(c.created_at).slice(5, 19)}</Td>
              <Td>{c.created_by}</Td>
              <Td className="font-medium">{c.kind}</Td>
              <Td className="num max-w-xs truncate text-muted">{JSON.stringify(c.payload)}</Td>
              <Td>
                <Badge tone={
                  c.status === "done" ? "green"
                    : c.status === "rejected" ? "red"
                    : c.status === "acked" ? "blue" : "amber"
                }>{c.status}</Badge>
              </Td>
              <Td className="num max-w-xs truncate text-muted">
                {c.result ? JSON.stringify(c.result) : "—"}
              </Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
