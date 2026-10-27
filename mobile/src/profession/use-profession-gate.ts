import { useQuery } from "@tanstack/react-query";

import { getPreferences, preferencesKeys } from "@/api/preferences";
import { useSession } from "@/auth/session-store";

import { prefsGate, professionHold, shouldAutoOpen } from "./logic";
import { useProfessionPrompt } from "./store";

export interface ProfessionGate {
  /** The question should open by itself now. */
  autoOpen: boolean;
  /** Self-presenting popups wait while this is true. */
  hold: boolean;
}

/** Signed-in only. Reads the preferences blob right away (the web kicks its
 *  hydrate after login for the same reason: the re-ask flag lives there). */
export function useProfessionGate(): ProfessionGate {
  const professionGroup = useSession((s) => s.user?.profession_group);
  const prefs = useQuery({ queryKey: preferencesKeys.all, queryFn: getPreferences, staleTime: 60_000 });
  const isOpen = useProfessionPrompt((s) => s.isOpen);
  const resolvedThisSession = useProfessionPrompt((s) => s.resolvedThisSession);

  const { ready, reaskDone } = prefsGate(prefs.status, prefs.data?.preferences);
  const input = { prefsReady: ready, professionGroup, reaskDone, resolvedThisSession };
  return { autoOpen: shouldAutoOpen(input), hold: professionHold({ ...input, isOpen }) };
}
