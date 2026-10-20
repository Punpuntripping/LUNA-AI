import type { PreferencesBlob, PreferencesResponse, UpdatePreferencesBody } from "@contracts";

import { apiFetch } from "./client";

// /preferences — the user's JSONB blob (backend/app/api/preferences.py). The
// server merges a PATCH shallowly: send only the keys being changed.

export function getPreferences(): Promise<PreferencesResponse> {
  return apiFetch<PreferencesResponse>("/preferences");
}

export function updatePreferences(patch: PreferencesBlob): Promise<PreferencesResponse> {
  const body: UpdatePreferencesBody = { preferences: patch };
  return apiFetch<PreferencesResponse>("/preferences", { method: "PATCH", body });
}
