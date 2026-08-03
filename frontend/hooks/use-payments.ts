import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiClientError, authApi, paymentsApi } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { usageKeys } from "@/hooks/use-usage";
import type {
  PaymentCheckoutResponse,
  PaymentHistoryResponse,
  PaymentRefundResponse,
  PaymentVerifyResponse,
} from "@/types";

export const paymentKeys = {
  all: ["payments"] as const,
  history: () => [...paymentKeys.all, "history"] as const,
};

/**
 * Refresh everything a granted (or revoked) term invalidates: the store's user
 * (plan badge, locked-account banner) and the usage bars.
 *
 * Shared by verify-success and refund-success because a refund REVOKES the term
 * — the UI has to move in both directions, and the second one is the one that
 * gets forgotten.
 */
async function refreshEntitlements(
  queryClient: ReturnType<typeof useQueryClient>,
): Promise<void> {
  try {
    const user = await authApi.me();
    useAuthStore.getState().setUser(user);
  } catch {
    // Non-fatal — the grant already happened server-side; the next page load
    // reflects it even if this refresh hiccuped.
  }
  queryClient.invalidateQueries({ queryKey: usageKeys.all });
  queryClient.invalidateQueries({ queryKey: paymentKeys.all });
}

/**
 * Open a checkout for `planId`.
 *
 * ⚠ EXACTLY ONE CALL PER PAGE VISIT. Every invocation inserts a
 * `payment_transactions` row, so the caller fires this from a ref-guarded
 * effect — React StrictMode's double-invoked effects would otherwise leave a
 * trail of orphan `initiated` rows in dev, and a genuine double-mount would do
 * it in production.
 */
export function useCheckout() {
  return useMutation<PaymentCheckoutResponse, ApiClientError, string>({
    mutationFn: (planId: string) => paymentsApi.checkout(planId),
    // A checkout inserts a row — never retried automatically.
    retry: false,
  });
}

/**
 * Sync a Moyasar payment id against our row.
 *
 * `pending` is a normal answer, not an error: the pre-3DS `on_completed` call
 * always lands there. Only a transport/4xx failure rejects.
 */
export function useVerifyPayment() {
  const queryClient = useQueryClient();

  return useMutation<PaymentVerifyResponse, ApiClientError, string>({
    mutationFn: (moyasarId: string) => paymentsApi.verify(moyasarId),
    retry: false,
    onSuccess: async (data) => {
      if (data.status === "paid") await refreshEntitlements(queryClient);
    },
  });
}

/** سجل المدفوعات — fetched only while the dialog is open. */
export function usePaymentHistory(enabled: boolean) {
  return useQuery<PaymentHistoryResponse>({
    queryKey: paymentKeys.history(),
    queryFn: paymentsApi.history,
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
}

/**
 * Self-serve refund. The fee is applied server-side; nothing about the amount
 * is sent from here.
 *
 * On success the term is REVOKED, so entitlements are refreshed exactly as they
 * are after a grant — a user who refunds must stop seeing a paid plan
 * immediately, not at the next full page load.
 */
export function useRefundPayment() {
  const queryClient = useQueryClient();

  return useMutation<PaymentRefundResponse, ApiClientError, string>({
    mutationFn: (paymentId: string) => paymentsApi.refund(paymentId),
    retry: false,
    onSuccess: async () => {
      await refreshEntitlements(queryClient);
    },
  });
}
