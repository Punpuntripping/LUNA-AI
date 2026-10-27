import type { PreferencesResponse } from "@contracts";
import type { QueryClient } from "@tanstack/react-query";

import { preferencesKeys, updatePreferences } from "@/api/preferences";

import { prefsGate } from "./logic";

/**
 * Close the «declined» re-ask for good (web `markProfessionReaskDone`): runs on
 * every exit, writes once. Skipped while the flag already reads done — which
 * includes an unread or failed preferences read (fail-closed, as on the web).
 * The cached blob flips first; a failed PATCH is swallowed and the local flag
 * kept — worst case the question is asked once more on a later launch.
 */
export async function markProfessionReaskDone(qc: QueryClient): Promise<void> {
  const state = qc.getQueryState<PreferencesResponse>(preferencesKeys.all);
  if (prefsGate(state?.status ?? "pending", state?.data?.preferences).reaskDone) return;
  // A read still in flight must not land on top of the flag with the old value.
  void qc.cancelQueries({ queryKey: preferencesKeys.all });
  qc.setQueryData<PreferencesResponse>(preferencesKeys.all, (old) =>
    old ? { ...old, preferences: { ...old.preferences, profession_reask_done: true } } : old,
  );
  try {
    // FLAT key — the server merges a PATCH shallowly.
    await updatePreferences({ profession_reask_done: true });
  } catch {
    // Keep the local flag (see above).
  }
}
