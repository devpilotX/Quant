"use client";

/** LiveProvider: one WebSocket for the whole app.
 *
 * - Auto-reconnect with backoff; on every (re)connect all TanStack queries
 *   are invalidated so REST snapshots resync BEFORE the stream is trusted
 *   (never show stale data as live).
 * - Each event invalidates the queries registered for its topic, and is
 *   fanned out to ad-hoc subscribers (tickers, toasts).
 * - Exposes connection status for the ever-visible health indicator.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

export type LiveEvent = {
  type: string;
  topic?: string;
  ts?: string;
  data?: Record<string, unknown>;
  ref?: Record<string, unknown>;
};

type Status = "connecting" | "live" | "down";

type Sub = (ev: LiveEvent) => void;

const LiveCtx = createContext<{
  status: Status;
  lastEventTs: string | null;
  subscribe: (topics: string[], fn: Sub) => () => void;
}>({ status: "connecting", lastEventTs: null, subscribe: () => () => {} });

// topic -> query key prefixes to invalidate
const INVALIDATES: Record<string, string[][]> = {
  decisions: [["decisions"], ["overview"], ["risk"]],
  orders: [["orders"], ["overview"]],
  fills: [["fills"], ["pnl"]],
  positions: [["positions"], ["overview"]],
  equity: [["equity"], ["overview"], ["pnl"], ["capital"]],
  regime: [["regime"], ["overview"]],
  risk: [["risk"], ["overview"]],
  alerts: [["alerts"], ["overview"]],
  engine: [["overview"]],
  config: [["config"], ["capital"], ["overview"]],
  commands: [["commands"], ["overview"]],
};

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<Status>("connecting");
  const [lastEventTs, setLastEventTs] = useState<string | null>(null);
  const subs = useRef<Map<Sub, Set<string>>>(new Map());
  const retry = useRef(0);

  const subscribe = useCallback((topics: string[], fn: Sub) => {
    subs.current.set(fn, new Set(topics));
    return () => {
      subs.current.delete(fn);
    };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (closed) return;
      setStatus("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);

      ws.onopen = () => {
        retry.current = 0;
        setStatus("live");
        // resync snapshots after any (re)connect — stream is delta-only
        qc.invalidateQueries();
      };
      ws.onmessage = (m) => {
        let ev: LiveEvent;
        try {
          ev = JSON.parse(m.data);
        } catch {
          return;
        }
        if (ev.type === "ping") {
          ws?.send(JSON.stringify({ type: "pong" }));
          return;
        }
        if (ev.type !== "event" || !ev.topic) return;
        setLastEventTs(ev.ts ?? null);
        for (const key of INVALIDATES[ev.topic] ?? []) {
          qc.invalidateQueries({ queryKey: key });
        }
        for (const [fn, topics] of subs.current) {
          if (topics.has(ev.topic)) fn(ev);
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setStatus("down");
        const wait = Math.min(15000, 500 * 2 ** retry.current++);
        timer = setTimeout(connect, wait);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  return (
    <LiveCtx.Provider value={{ status, lastEventTs, subscribe }}>
      {children}
    </LiveCtx.Provider>
  );
}

export function useLive() {
  return useContext(LiveCtx);
}

export function useLiveTopic(topics: string[], fn: Sub) {
  const { subscribe } = useLive();
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(
    () => subscribe(topics, (ev) => ref.current(ev)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [subscribe, topics.join(",")]
  );
}
