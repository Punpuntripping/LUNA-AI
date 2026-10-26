import type { MarkAnnouncementsReadRequest } from "@contracts";

import { apiFetch } from "./client";

// «رسائل ريحان» (.claude/plans/announcements.md). The server decides the
// audience; the app never filters. The raw GET body is validated in
// src/announcements/logic.ts before anything renders it.

/** `GET /announcements` → `AnnouncementsResponse`, newest first, ≤ 50. */
export function getAnnouncements(): Promise<unknown> {
  return apiFetch<unknown>("/announcements");
}

/** `POST /announcements/read` (1–50 ids) → 204. Idempotent. */
export async function markAnnouncementsRead(ids: string[]): Promise<void> {
  const body: MarkAnnouncementsReadRequest = { ids };
  await apiFetch<void>("/announcements/read", { method: "POST", body });
}
