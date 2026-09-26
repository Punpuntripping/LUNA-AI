import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { preferencesApi } from "@/lib/api";
import type { UserPreferencesData } from "@/types";

export const preferencesKeys = {
  all: ["preferences"] as const,
  marketingEmail: ["preferences", "marketing-email"] as const,
};

export function useMarketingEmail(enabled = true) {
  return useQuery({
    queryKey: preferencesKeys.marketingEmail,
    queryFn: () => preferencesApi.getMarketingEmail(),
    enabled,
  });
}

export function useUpdateMarketingEmail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (optIn: boolean) => preferencesApi.setMarketingEmail(optIn),
    onSuccess: (data) => {
      qc.setQueryData(preferencesKeys.marketingEmail, data);
    },
  });
}

export function usePreferences() {
  return useQuery({
    queryKey: preferencesKeys.all,
    queryFn: () => preferencesApi.get(),
  });
}

export function useUpdatePreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preferences: UserPreferencesData) =>
      preferencesApi.update(preferences),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: preferencesKeys.all });
    },
  });
}
