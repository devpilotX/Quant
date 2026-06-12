"use client";

/** Minimal dense component kit (cards, stats, tables, badges, modal, inputs).
 * Hand-rolled instead of a UI library: ~200 lines we fully control beats a
 * dependency tree for a single-operator terminal. */

import clsx from "clsx";
import { useEffect } from "react";

export function Card({
  title,
  right,
  children,
  className,
  pad = true,
}: {
  title?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section
      className={clsx(
        "rounded-md border border-line bg-surface",
        className
      )}
    >
      {(title || right) && (
        <header className="flex items-center justify-between border-b border-line px-3 py-1.5">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">
            {title}
          </h3>
          {right}
        </header>
      )}
      <div className={pad ? "p-3" : ""}>{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  valueClass?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={clsx("num truncate text-lg font-semibold", valueClass)}>
        {value}
      </div>
      {sub !== undefined && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: "neutral" | "green" | "red" | "amber" | "blue";
  children: React.ReactNode;
  className?: string;
}) {
  const tones = {
    neutral: "bg-surface2 text-muted border-line",
    green: "bg-up/10 text-up border-up/40",
    red: "bg-down/15 text-down border-down/50",
    amber: "bg-warn/10 text-warn border-warn/40",
    blue: "bg-accent/10 text-accent border-accent/40",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium",
        tones[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  tone = "default",
  disabled,
  type = "button",
  className,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const tones = {
    default: "border-line bg-surface2 hover:bg-[#1c2333] text-foreground",
    primary: "border-accent/50 bg-accent/15 hover:bg-accent/25 text-accent",
    danger: "border-down/50 bg-down/15 hover:bg-down/25 text-down",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "rounded border px-2.5 py-1 text-xs font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40",
        tones[tone],
        className
      )}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx(
        "w-full rounded border border-line bg-surface2 px-2 py-1.5 text-sm",
        "placeholder:text-muted/60 focus:border-accent focus:outline-none",
        props.className
      )}
    />
  );
}

export function Table({
  cols,
  children,
  empty,
}: {
  cols: (string | { label: string; align?: "right" | "left" })[];
  children: React.ReactNode;
  empty?: string;
}) {
  const hasRows = Array.isArray(children) ? children.length > 0 : !!children;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-line text-[10px] uppercase tracking-wider text-muted">
            {cols.map((c, i) => {
              const col = typeof c === "string" ? { label: c } : c;
              return (
                <th
                  key={i}
                  className={clsx(
                    "px-2 py-1.5 font-medium",
                    col.align === "right" && "text-right"
                  )}
                >
                  {col.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-line/60">
          {hasRows ? (
            children
          ) : (
            <tr>
              <td colSpan={cols.length} className="px-2 py-6 text-center text-muted">
                {empty ?? "no data"}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function Td({
  children,
  right,
  className,
}: {
  children: React.ReactNode;
  right?: boolean;
  className?: string;
}) {
  return (
    <td className={clsx("px-2 py-1.5", right && "num text-right", className)}>
      {children}
    </td>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-8"
      onClick={onClose}
    >
      <div
        className={clsx("w-full rounded-lg border border-line bg-surface", width)}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button onClick={onClose} className="text-muted hover:text-foreground">
            ✕
          </button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Gauge({
  label,
  value,
  max = 1,
  format,
  danger = 0.85,
}: {
  label: string;
  value: number | null | undefined;
  max?: number;
  format?: (v: number) => string;
  danger?: number;
}) {
  const frac =
    value === null || value === undefined || max === 0
      ? null
      : Math.min(1, Math.abs(value) / max);
  const tone =
    frac === null ? "bg-muted" : frac >= danger ? "bg-down" : frac >= 0.6 ? "bg-warn" : "bg-up";
  return (
    <div>
      <div className="mb-0.5 flex justify-between text-[11px]">
        <span className="text-muted">{label}</span>
        <span className="num">
          {value === null || value === undefined
            ? "—"
            : format
              ? format(value)
              : `${(frac! * 100).toFixed(0)}%`}
        </span>
      </div>
      <div className="h-1.5 w-full rounded bg-surface2">
        {frac !== null && (
          <div
            className={clsx("h-1.5 rounded transition-all", tone)}
            style={{ width: `${frac * 100}%` }}
          />
        )}
      </div>
    </div>
  );
}

export function Spinner() {
  return (
    <div className="flex justify-center p-6">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-line border-t-accent" />
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded border border-down/40 bg-down/10 px-3 py-2 text-xs text-down">
      {msg}
    </div>
  );
}
