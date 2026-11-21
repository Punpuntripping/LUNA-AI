import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiClientError, authApi, paymentsApi } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { usageKeys } from "@/hooks/use-usage";
import type {
  PaymentCheckoutResponse,
  PaymentConsentResponse,
  PaymentHistoryResponse,
  PaymentMethodRevokeResponse,
  PaymentMethodState,
  PaymentRefundResponse,
  PaymentVerifyResponse,
  ScheduleDowngradeRequest,
  SubscriptionState,
} from "@/types";

export const paymentKeys = {
  all: ["payments"] as const,
  history: () => [...paymentKeys.all, "history"] as const,
  method: () => [...paymentKeys.all, "method"] as const,
  subscription: () => [...paymentKeys.all, "subscription"] as const,
};

/** What "no card on file" looks like — the shape `DELETE` leaves behind, and
 *  the fallback whenever the read fails or the endpoint does not exist yet. */
const NO_PAYMENT_METHOD: PaymentMethodState = {
  has_method: false,
  brand: null,
  last4: null,
  exp_month: null,
  exp_year: null,
  consent_given_at: null,
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

/**
 * Record the pre-purchase recurring consent for an open checkout
 * (`.claude/plans/subscription_auto_renewal.md` §6 + §9).
 *
 * ⚠ THE CARD FORM MUST NOT EXIST UNTIL THIS RESOLVES. `/pay` mounts Moyasar
 * with `credit_card.save_card` only after a success here, so that a stored
 * token always has a consent row behind it — the renewal job refuses to charge
 * one that does not.
 *
 * Never retried automatically: a second call would write a second consent
 * artefact for the same payment, and the failure the user needs to see is the
 * first one. The checkbox re-arms itself instead.
 */
export function useRecurringConsent() {
  return useMutation<PaymentConsentResponse, ApiClientError, string>({
    mutationFn: (paymentId: string) =>
      paymentsApi.acceptRecurringConsent(paymentId),
    retry: false,
  });
}

/**
 * What the CLIENT knows about the stored card, which is not the same as what
 * the server said. `unavailable` is set only when the read itself failed, and
 * it is deliberately NOT part of `PaymentMethodState`: the server never sends
 * it, and putting a read artefact in the API response type would invite
 * somebody to trust it as an answer from the backend.
 */
export type PaymentMethodRead = PaymentMethodState & { unavailable?: boolean };

/**
 * The stored card for إعدادات الحساب — fetched only while the dialog is open.
 *
 * Fails QUIET by design: a rejected read (404 on a backend that predates the
 * feature, a hiccup, a locked account) resolves to "no method", so the section
 * disappears rather than blocking passwords and account deletion behind a
 * billing error. `retry:false` for the same reason — three round trips to
 * re-confirm a 404 only delay the same empty answer.
 *
 * ⚠ But "we could not read it" is NOT "there is no card", and since 2026-09-23
 * the difference is user-visible: the dialog now warns a pro/max subscriber
 * with no stored card that nothing will renew. Resolving a network hiccup to
 * that warning would tell a paying subscriber their billing is broken when it
 * is not. So the failure still resolves (nothing is blocked, nothing throws)
 * but carries `unavailable`, and the warning refuses to fire on it.
 */
export function usePaymentMethod(enabled: boolean) {
  return useQuery<PaymentMethodRead>({
    queryKey: paymentKeys.method(),
    queryFn: async () => {
      try {
        return await paymentsApi.getPaymentMethod();
      } catch {
        return { ...NO_PAYMENT_METHOD, unavailable: true };
      }
    },
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/**
 * Remove the stored card («إزالة البطاقة»).
 *
 * Touches no subscription and no term — only the credential. The Arabic copy
 * at the call site therefore says «لن يُجدَّد اشتراكك تلقائياً بعد إزالة
 * البطاقة» and never «سيتم إيقاف الدفع التلقائي»: forward-looking, and true
 * both before and after the renewal engine exists.
 */
export function useRemovePaymentMethod() {
  const queryClient = useQueryClient();

  return useMutation<PaymentMethodRevokeResponse, ApiClientError, void>({
    mutationFn: () => paymentsApi.removePaymentMethod(),
    retry: false,
    onSuccess: () => {
      // The body is deliberately not trusted (a 204 arrives as `{}`): write the
      // known-empty state so the section vanishes at once, then re-read for the
      // server's own version of the truth.
      queryClient.setQueryData<PaymentMethodState>(
        paymentKeys.method(),
        NO_PAYMENT_METHOD,
      );
      queryClient.invalidateQueries({ queryKey: paymentKeys.method() });
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

// -----------------------------------------------
// الاشتراك — the subscription read + scheduled downgrade
// (.claude/plans/pro_plus_tier_and_downgrade.md §4.4 + §8)
// -----------------------------------------------

/**
 * `GET /payments/subscription` for إعدادات الحساب — fetched only while the
 * dialog is open, and re-read on EVERY opening (`staleTime: 0`): the term end,
 * a cancellation or a pending downgrade may have changed from another device.
 *
 * Fails QUIET, like the card read above: the dialog renders no subscription
 * section while `data` is undefined, so a first read that fails blocks nothing
 * (passwords, sessions and account deletion live in the same dialog). A later
 * re-read that fails keeps the last good answer on screen instead of blanking
 * a section the user is looking at. `retry: false` for the same reason as
 * `usePaymentMethod`.
 */
export function useSubscription(enabled: boolean) {
  return useQuery<SubscriptionState>({
    queryKey: paymentKeys.subscription(),
    queryFn: paymentsApi.getSubscription,
    enabled,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/**
 * Every subscription verb (cancel, reactivate, schedule/undo a downgrade)
 * answers with the GET shape, so the answer is written straight into the
 * cache — the section re-renders from the server's own state without a blank
 * in between — and the query is then invalidated for a confirming re-read.
 *
 * A body that is not a subscription object (a proxy's empty 200, say) is NOT
 * written: `{}` in the cache would read as "no plan" and hide the section.
 */
export function useWriteSubscription() {
  const queryClient = useQueryClient();
  return (next: SubscriptionState | null | undefined) => {
    if (next && typeof next === "object" && "plan_id" in next) {
      queryClient.setQueryData<SubscriptionState>(
        paymentKeys.subscription(),
        next,
      );
    }
    void queryClient.invalidateQueries({
      queryKey: paymentKeys.subscription(),
    });
  };
}

/**
 * Schedule a downgrade for the next renewal (`POST /subscription/downgrade`).
 * Never retried: the 409s it can return are answers, not hiccups — and each
 * carries the backend's Arabic message, which the caller renders as is. On an
 * error the subscription is re-read too: a 409 usually means the state on
 * screen is stale — cancelled or upgraded from another device, no stored card
 * any more (`SUBSCRIPTION_DOWNGRADE_NO_METHOD`), or a quote that moved
 * (`SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED`, after which the confirm step shows
 * the re-read price and waits for a new confirm — never a silent retry at the
 * new price).
 */
export function useScheduleDowngrade() {
  const queryClient = useQueryClient();
  const writeSubscription = useWriteSubscription();
  // The variable IS the request body, built by `scheduleDowngradeBody()` from
  // the option on screen (plan id + the price and offer months shown).
  return useMutation<SubscriptionState, ApiClientError, ScheduleDowngradeRequest>({
    mutationFn: (body: ScheduleDowngradeRequest) =>
      paymentsApi.scheduleDowngrade(body),
    retry: false,
    onSuccess: (state) => writeSubscription(state),
    onError: () => {
      void queryClient.invalidateQueries({
        queryKey: paymentKeys.subscription(),
      });
    },
  });
}

/** Undo the scheduled downgrade («تراجع») — `DELETE /subscription/downgrade`. */
export function useCancelScheduledDowngrade() {
  const queryClient = useQueryClient();
  const writeSubscription = useWriteSubscription();
  return useMutation<SubscriptionState, ApiClientError, void>({
    mutationFn: () => paymentsApi.cancelScheduledDowngrade(),
    retry: false,
    onSuccess: (state) => writeSubscription(state),
    onError: () => {
      void queryClient.invalidateQueries({
        queryKey: paymentKeys.subscription(),
      });
    },
  });
}
