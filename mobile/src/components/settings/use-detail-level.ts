import { coerceDetailLevel, type DetailLevel, type PreferencesResponse } from "@contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { errorMessage } from "@/api/errors";
import { getPreferences, preferencesKeys, updatePreferences } from "@/api/preferences";
import { showToast } from "@/stores/toast";

// مستوى التفصيل — `user_preferences.detail_level`, the same value the web's
// «إعدادات المحادثة» dialog and composer «+» menu edit (preferences-store.ts).
// Server state in TanStack Query (cleared with the cache on logout). A failed
// read falls back to «متوسط» and stays editable, like the web's hydrate; a
// change is optimistic and rolls back with an Arabic toast.

const SAVE_ERROR = "تعذر حفظ مستوى التفصيل. حاول مرة أخرى.";

export interface DetailLevelState {
  level: DetailLevel;
  /** First read still in flight (web `!isHydrated`). */
  loading: boolean;
  saving: boolean;
  setLevel: (level: DetailLevel) => void;
}

export function useDetailLevel(): DetailLevelState {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: preferencesKeys.all, queryFn: getPreferences, staleTime: 60_000 });

  const mutation = useMutation({
    mutationFn: (level: DetailLevel) => updatePreferences({ detail_level: level }),
    onMutate: async (level) => {
      await qc.cancelQueries({ queryKey: preferencesKeys.all });
      const previous = qc.getQueryData<PreferencesResponse>(preferencesKeys.all);
      qc.setQueryData<PreferencesResponse>(preferencesKeys.all, (old) => ({
        user_id: old?.user_id ?? "",
        preferences: { ...(old?.preferences ?? {}), detail_level: level },
      }));
      return { previous };
    },
    onError: (err, _level, ctx) => {
      // A failed first read leaves no snapshot: drop the optimistic entry so
      // the value falls back to the default instead of claiming the new one.
      if (ctx?.previous) qc.setQueryData(preferencesKeys.all, ctx.previous);
      else qc.removeQueries({ queryKey: preferencesKeys.all, exact: true });
      showToast(errorMessage(err, SAVE_ERROR));
    },
    onSuccess: (res) => {
      qc.setQueryData(preferencesKeys.all, res);
    },
  });

  const level = coerceDetailLevel(query.data?.preferences?.detail_level);
  return {
    level,
    loading: query.isPending && query.fetchStatus !== "idle",
    saving: mutation.isPending,
    setLevel: (next) => {
      if (next === level || mutation.isPending) return;
      mutation.mutate(next);
    },
  };
}
