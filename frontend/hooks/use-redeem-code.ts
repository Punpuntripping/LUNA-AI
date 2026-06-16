import { useMutation, useQueryClient } from "@tanstack/react-query";
import { plansApi, authApi, ApiClientError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { usageKeys } from "@/hooks/use-usage";
import type { RedeemCodeResponse } from "@/types";

/**
 * Redeem an activation code → assign its plan to the current user.
 *
 * On success we refresh /auth/me (so the store's ``user.plan_id`` updates and
 * any locked-account banner clears) and invalidate the usage query (so the
 * Settings → حدود الاستخدام bars reflect the new plan immediately).
 *
 * The component reads ``error`` (an ApiClientError) to render the Arabic
 * message the backend returned — invalid/used code (400), already-active plan
 * (409), or too-many-attempts (429).
 */
export function useRedeemCode() {
  const queryClient = useQueryClient();

  return useMutation<RedeemCodeResponse, ApiClientError, string>({
    mutationFn: (code: string) => plansApi.redeem(code.trim()),
    onSuccess: async () => {
      try {
        const user = await authApi.me();
        useAuthStore.getState().setUser(user);
      } catch {
        // Non-fatal — the plan is already applied server-side; the next page
        // load will reflect it even if this refresh hiccuped.
      }
      queryClient.invalidateQueries({ queryKey: usageKeys.all });
    },
  });
}
