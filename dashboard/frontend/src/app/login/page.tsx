"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiPost } from "@/lib/api";
import { Button, ErrorNote, Input } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPost("/auth/login", { username, password, totp });
      router.push("/");
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center">
      <form
        onSubmit={submit}
        className="w-80 space-y-3 rounded-lg border border-line bg-surface p-6"
      >
        <div>
          <h1 className="text-base font-bold">quantsys</h1>
          <p className="text-xs text-muted">operator sign-in · 2FA required</p>
        </div>
        <Input
          placeholder="operator id"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          autoComplete="username"
        />
        <Input
          type="password"
          placeholder="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <Input
          inputMode="numeric"
          placeholder="TOTP code"
          value={totp}
          onChange={(e) => setTotp(e.target.value)}
          autoComplete="one-time-code"
        />
        {error != null && <ErrorNote error={error} />}
        <Button
          type="submit"
          tone="primary"
          className="w-full py-2"
          disabled={busy || !username || !password || totp.length < 6}
        >
          {busy ? "signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
