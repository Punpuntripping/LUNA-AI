import {
  parseRefusal,
  type LibraryRefusal,
  type Reference,
  type ReferenceSourceError,
  type ReferenceSourceResponse,
  type WorkspaceItem,
  type WorkspaceItemListResponse,
} from "@contracts";

import { getAccessToken } from "@/auth/token-store";
import { API_PREFIX, API_URL } from "@/config/env";
import { useUpdateGate } from "@/stores/update-gate";

import { apiFetch } from "./client";
import { appHeaders } from "./headers";

// Workspace items + references — backend/app/api/workspace.py.

/** Server cap is 200 per page; a conversation never gets near it. */
const WORKSPACE_LIMIT = 200;

export function listConversationWorkspace(conversationId: string): Promise<WorkspaceItemListResponse> {
  return apiFetch<WorkspaceItemListResponse>(
    `/conversations/${encodeURIComponent(conversationId)}/workspace?limit=${WORKSPACE_LIMIT}`,
  );
}

export function getWorkspaceItem(itemId: string): Promise<WorkspaceItem> {
  return apiFetch<WorkspaceItem>(`/workspace/${encodeURIComponent(itemId)}`);
}

/** The citation mesh of an `agent_search` item — FREE (no source bodies;
 *  `source_view` is always null here). `usedOnly` = only the cited ones. */
export async function listReferences(itemId: string, opts: { usedOnly?: boolean } = {}): Promise<Reference[]> {
  const qs = opts.usedOnly ? "?used=true" : "";
  const res = await apiFetch<{ references: Reference[] }>(`/workspace/${encodeURIComponent(itemId)}/references${qs}`);
  return res.references;
}

/** A refusal and a 429 are ANSWERS (rendered as cards), not exceptions. */
export type ReferenceSourceResult =
  | { ok: true; data: ReferenceSourceResponse }
  | { ok: false; kind: "refusal"; refusal: LibraryRefusal }
  | { ok: false; kind: "error"; error: ReferenceSourceError; status: number | null };

/**
 * Reveal ONE reference's source — METERED. Call only from a user gesture
 * (opening the reader), never on mount / prefetch.
 *
 * Plain fetch, not `apiFetch`: a 401 here renders «انتهت جلستك» in the reader
 * instead of refreshing/ejecting mid-read (plan 05 §R2, web lib/api.ts).
 */
export async function getReferenceSource(itemId: string, n: number): Promise<ReferenceSourceResult> {
  const token = getAccessToken();
  if (!token) return { ok: false, kind: "error", error: "no_token", status: null };

  const { headers } = appHeaders();
  let res: Response;
  try {
    res = await fetch(`${API_URL}${API_PREFIX}/workspace/${encodeURIComponent(itemId)}/references/${n}/source`, {
      headers: { ...headers, Accept: "application/json", Authorization: `Bearer ${token}` },
    });
  } catch {
    return { ok: false, kind: "error", error: "network", status: null };
  }

  if (res.status === 402) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    return { ok: false, kind: "refusal", refusal: parseRefusal(body) };
  }

  if (!res.ok) {
    if (res.status === 426) useUpdateGate.getState().markUpdateRequired();
    const error: ReferenceSourceError =
      res.status === 401 || res.status === 403
        ? "unauthorized"
        : res.status === 404
          ? "not_found"
          : res.status === 429
            ? "rate_limited"
            : "server";
    return { ok: false, kind: "error", error, status: res.status };
  }

  try {
    return { ok: true, data: (await res.json()) as ReferenceSourceResponse };
  } catch {
    return { ok: false, kind: "error", error: "network", status: res.status };
  }
}
