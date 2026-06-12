"use client";

/** High-risk action flow: every control mutation goes through this modal,
 * which (1) re-authenticates password+TOTP, then (2) runs the action.
 * There is no code path to a control endpoint that skips this. */

import { useState } from "react";

import { apiPost } from "@/lib/api";
import { Button, ErrorNote, Input, Modal } from "@/components/ui";

export function useReauthAction() {
  const [pending, setPending] = useState<{
    title: string;
    summary: React.ReactNode;
    danger?: boolean;
    run: () => Promise<unknown>;
    onDone?: () => void;
  } | null>(null);

  const guard = (opts: NonNullable<typeof pending>) => setPending(opts);

  const modal = (
    <ReauthModal pending={pending} close={() => setPending(null)} />
  );
  return { guard, modal };
}

function ReauthModal({
  pending,
  close,
}: {
  pending: {
    title: string;
    summary: React.ReactNode;
    danger?: boolean;
    run: () => Promise<unknown>;
    onDone?: () => void;
  } | null;
  close: () => void;
}) {
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<string | null>(null);

  const reset = () => {
    setPassword("");
    setTotp("");
    setError(null);
    setResult(null);
    setBusy(false);
  };

  const submit = async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost("/auth/reauth", { password, totp });
      const r = (await pending.run()) as { command_id?: number; note?: string };
      setResult(
        r?.command_id
          ? `queued as command #${r.command_id} — engine will ack`
          : "done"
      );
      pending.onDone?.();
    } catch (e) {
      setError(e);
      setBusy(false);
      return;
    }
    setBusy(false);
  };

  return (
    <Modal
      open={pending !== null}
      onClose={() => {
        reset();
        close();
      }}
      title={pending?.title ?? ""}
    >
      {result ? (
        <div className="space-y-3">
          <div className="rounded border border-up/40 bg-up/10 px-3 py-2 text-xs text-up">
            {result}
          </div>
          <Button
            onClick={() => {
              reset();
              close();
            }}
          >
            Close
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="rounded border border-line bg-surface2 p-3 text-xs">
            {pending?.summary}
          </div>
          {pending?.danger && (
            <div className="rounded border border-down/40 bg-down/10 px-3 py-2 text-xs text-down">
              High-risk action. Re-authentication required.
            </div>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Input
              type="password"
              placeholder="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
            <Input
              inputMode="numeric"
              placeholder="TOTP code"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          {error != null && <ErrorNote error={error} />}
          <div className="flex justify-end gap-2">
            <Button onClick={() => { reset(); close(); }}>Cancel</Button>
            <Button
              tone={pending?.danger ? "danger" : "primary"}
              disabled={busy || !password || totp.length < 6}
              onClick={submit}
            >
              {busy ? "verifying…" : "Confirm"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
