import { useMutation, useQueryClient } from "@tanstack/react-query";
import { plansApi, authApi, ApiClientError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { usageKeys } from "@/hooks/use-usage";
import type { FreeTrialClaimResponse } from "@/types";

/**
 * Claim the free week → the caller is on the trial plan as soon as this resolves.
 *
 * Same aftermath as `useRedeemCode`, except /auth/me is not awaited: the blocked
 * message is sent straight after this succeeds, and it depends only on the grant
 * the server has already made.
 */
export function useClaimFreeTrial() {
  const queryClient = useQueryClient();

  return useMutation<FreeTrialClaimResponse, ApiClientError, void>({
    mutationFn: () => plansApi.claimFreeTrial(),
    onSuccess: () => {
      void authApi
        .me()
        .then((user) => useAuthStore.getState().setUser(user))
        .catch(() => {
          // Non-fatal: the plan is applied, the next page load reflects it.
        });
      queryClient.invalidateQueries({ queryKey: usageKeys.all });
    },
  });
}
