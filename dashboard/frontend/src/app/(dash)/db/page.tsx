"use client";

/** Database browser: read-only table viewer + SELECT-only query console.
 * (Full pgweb runs at /db/ behind nginx on the VPS.) */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { Button, Card, ErrorNote, Spinner } from "@/components/ui";

type TableInfo = { table: string; columns: string[] };
type Rows = { table?: string; columns: string[]; rows: unknown[][]; total?: number };

export default function DbPage() {
  const [table, setTable] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [sql, setSql] = useState("SELECT count(*) AS decisions FROM decisions");
  const [queryResult, setQueryResult] = useState<Rows | null>(null);
  const [queryError, setQueryError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const { data: tables } = useQuery<TableInfo[]>({
    queryKey: ["db", "tables"],
    queryFn: () => apiGet("/db/tables"),
  });
  const active = table ?? tables?.[0]?.table ?? null;
  const { data: rows, isLoading } = useQuery<Rows>({
    queryKey: ["db", "rows", active, offset],
    queryFn: () => apiGet(`/db/tables/${active}?limit=50&offset=${offset}`),
    enabled: active !== null,
  });

  const runQuery = async () => {
    setBusy(true);
    setQueryError(null);
    try {
      setQueryResult(await apiPost<Rows>("/db/query", { sql, limit: 200 }));
    } catch (e) {
      setQueryError(e);
      setQueryResult(null);
    }
    setBusy(false);
  };

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[220px_1fr]">
      <Card title="Tables" pad={false} className="max-h-[80vh] overflow-y-auto">
        <ul>
          {(tables ?? []).map((t) => (
            <li key={t.table}>
              <button
                onClick={() => { setTable(t.table); setOffset(0); }}
                className={`w-full px-3 py-1.5 text-left text-xs hover:bg-surface2 ${
                  active === t.table ? "bg-accent/10 text-accent" : ""
                }`}
              >
                {t.table}
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <div className="min-w-0 space-y-3">
        <Card
          title={active ? `${active} (${rows?.total ?? "…"} rows)` : "table"}
          right={
            <div className="flex gap-1">
              <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>‹</Button>
              <Button disabled={!rows?.total || offset + 50 >= rows.total}
                onClick={() => setOffset(offset + 50)}>›</Button>
            </div>
          }
          pad={false}
        >
          {isLoading ? <Spinner /> : rows ? <Grid data={rows} /> : null}
        </Card>

        <Card title="Query console — single SELECT, read-only transaction">
          <div className="space-y-2">
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              rows={3}
              spellCheck={false}
              className="num w-full rounded border border-line bg-surface2 p-2 text-xs focus:border-accent focus:outline-none"
            />
            <div className="flex items-center gap-2">
              <Button tone="primary" onClick={runQuery} disabled={busy}>
                {busy ? "running…" : "Run (read-only)"}
              </Button>
              <span className="text-[11px] text-muted">
                writes are rejected server-side; use pgweb on the VPS for admin
              </span>
            </div>
            {queryError != null && <ErrorNote error={queryError} />}
            {queryResult && <Grid data={queryResult} />}
          </div>
        </Card>
      </div>
    </div>
  );
}

function Grid({ data }: { data: Rows }) {
  return (
    <div className="max-h-[55vh] overflow-auto">
      <table className="w-full text-left text-xs">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-line text-[10px] uppercase tracking-wider text-muted">
            {data.columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-2 py-1.5 font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line/60">
          {data.rows.map((r, i) => (
            <tr key={i} className="hover:bg-surface2/60">
              {r.map((v, j) => (
                <td key={j} className="num max-w-64 truncate px-2 py-1 text-muted">
                  {v === null ? <span className="opacity-40">null</span>
                    : typeof v === "object" ? JSON.stringify(v) : String(v)}
                </td>
              ))}
            </tr>
          ))}
          {data.rows.length === 0 && (
            <tr><td colSpan={data.columns.length} className="px-2 py-4 text-center text-muted">empty</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
