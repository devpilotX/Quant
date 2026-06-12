"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import Shell from "@/components/shell";
import { LiveProvider } from "@/lib/live";

export default function DashLayout({ children }: { children: React.ReactNode }) {
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 2000,
            refetchOnWindowFocus: true,
            retry: (count, err) =>
              count < 2 && !(err instanceof Error && err.message === "not authenticated"),
          },
        },
      })
  );
  return (
    <QueryClientProvider client={qc}>
      <LiveProvider>
        <Shell>{children}</Shell>
      </LiveProvider>
    </QueryClientProvider>
  );
}
