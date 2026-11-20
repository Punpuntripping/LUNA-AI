"use client";

import type { ReactNode } from "react";
import { notFound } from "next/navigation";
import { useFilesStatus } from "@/hooks/use-files";

/**
 * The per-user half of the `/files` gate. `app/files/layout.tsx` already
 * answers not-found when the build flag is off; this answers it when the
 * feature is off for THIS user (`GET /files/status` → `false`, or the status
 * call failed), so a direct URL shows the app's not-found state rather than
 * an error from the backend's 404s. Nothing renders until the answer is in —
 * neither the page nor not-found flashes first.
 */
export function FilesFeatureGate({ children }: { children: ReactNode }) {
  const { enabled, settled } = useFilesStatus();
  if (!settled) return null;
  if (!enabled) notFound();
  return <>{children}</>;
}
