"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { paymentsApi } from "@/lib/api";
import {
  MOYASAR_APPLEPAY_VALIDATE_URL,
  MOYASAR_FORM_ELEMENT_ID,
  loadMoyasarForm,
  type MoyasarPayment,
} from "@/lib/moyasar";
import {
  PAYMENT_TRUST_NOTE,
  findPricingPlan,
  formatHalalas,
  formatSar,
} from "@/lib/pricing";
import { useCheckout } from "@/hooks/use-payments";
import { Button } from "@/components/ui/button";
import { RiyalSymbol } from "@/components/icons/RiyalSymbol";

/**
 * Checkout — `/pay/{plan}`.
 *
 * Sequence, and every step of it matters:
 *   1. `POST /payments/checkout {plan_id}` → the server computes the amount from
 *      `plans.price_sar` (minus any prorated upgrade credit) and inserts an
 *      `initiated` row. NO AMOUNT IS SENT FROM HERE, ever.
 *   2. Load the pinned moyasar.js bundle (CDN, CSP-gated).
 *   2b. IF the plan renews: render the server's disclosure verbatim as a plain
 *      reminder above the form, and pass `save_card`. There is NO checkbox and
 *      nothing to wait for (owner, 2026-08-12) — the affirmative act is
 *      completing the purchase after being shown the reminder, and the backend
 *      stamps the consent artefact at capture. See `capture_payment_method`.
 *   3. `Moyasar.init` with `metadata.payment_id` — the thread that lets the
 *      webhook and the callback both find our row.
 *   4. `on_completed` POSTs the Moyasar id to `/verify` BEFORE the 3DS redirect,
 *      because 3DS is a full-page navigation that destroys this component
 *      (plan trap 9). After that call, an abandoned redirect is still
 *      recoverable — we hold the id server-side.
 *
 * The two confirmation paths (this redirect, and the server-to-server webhook)
 * are independent and both idempotent. Neither alone is sufficient.
 */
// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function PayPlanPage() {
  const params = useParams<{ plan: string }>();
  const planId = params.plan;
  const plan = findPricingPlan(planId ?? "");

  const checkout = useCheckout();
  const { mutate: startCheckout, data: session } = checkout;


  const startedRef = useRef(false);
  const mountedRef = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formLoading, setFormLoading] = useState(false);

  // Does this purchase renew — and therefore show the reminder and tokenize?
  //
  // Two conditions, both from the server: it said the plan renews, AND it
  // supplied the disclosure to show. A flag with no text is a backend bug, and
  // the safe degradation is a plain one-time purchase — the page then behaves
  // exactly as it did before this feature and, crucially, never passes
  // `save_card`, so no credential is stored under a disclosure nobody saw.
  //
  // ⚠ The text is rendered VERBATIM and never composed here. The backend hashes
  // its own copy as the consent artefact; a sentence invented client-side could
  // never be proven against that hash.
  const disclosure = session?.recurring_disclosure_ar?.trim() || null;
  const requiresConsent = Boolean(
    session?.requires_recurring_consent && disclosure,
  );

  // Step 1 — open the checkout exactly once.
  //
  // The ref guard is load-bearing, not defensive dressing: every call inserts a
  // `payment_transactions` row, and React StrictMode double-invokes effects in
  // development. Without it, every dev visit to this page leaves an orphan
  // `initiated` row and a second Moyasar payment could be created against it.
  useEffect(() => {
    if (!plan || startedRef.current) return;
    startedRef.current = true;
    startCheckout(plan.id);
  }, [plan, startCheckout]);

  // Steps 2–4 — mount the form once the server has answered.
  useEffect(() => {
    if (!session || mountedRef.current) return;
    mountedRef.current = true;

    let cancelled = false;
    setFormLoading(true);

    void (async () => {
      try {
        const moyasar = await loadMoyasarForm();
        if (cancelled) return;

        // ⚠ Apple Pay must be CAPABILITY-GATED, not merely configured. Verified
        // on prod 2026-08-04: with `methods: [...,'applepay']` in a browser
        // without `ApplePaySession`, moyasar.js 1.19.0 does not skip the method
        // — it dies mid-render ("Element: null is not a valid element") and the
        // ENTIRE form, card fields included, never appears. Same init without
        // 'applepay' renders fine. So the method is included only where Apple
        // hardware can actually use it (Safari exposes ApplePaySession) AND the
        // server says the merchant side is ready (`applepay_enabled`, off while
        // the Moyasar Apple Pay domain registration is pending — a capable
        // Safari would otherwise render a button that dies at merchant
        // validation).
        const canApplePay =
          session.applepay_enabled &&
          typeof window.ApplePaySession !== "undefined" &&
          window.ApplePaySession.canMakePayments();

        // ⚠ The NODE, never an id selector: moyasar.js clobbers the
        // container's id during mount and lazily re-resolves the selector —
        // an id string self-destructs mid-render (see MoyasarInitOptions).
        const mountNode = document.getElementById(MOYASAR_FORM_ELEMENT_ID);
        if (!mountNode) {
          // The session just rendered the div in this same commit; missing it
          // means the tree unmounted mid-flight. Treat as load failure.
          throw new Error("moyasar mount node missing");
        }

        moyasar.init({
          element: mountNode,
          // ⚠ HALALAS, straight from the server. Never `price * 100` here.
          amount: session.amount_halalas,
          currency: "SAR",
          description: session.description,
          publishable_api_key: session.publishable_key,
          callback_url: session.callback_url,
          metadata: { payment_id: session.payment_id },
          // STC Pay is excluded by omission (decision 2026-08-03) — `methods`
          // is an allowlist, so re-adding it later is one array entry.
          methods: canApplePay ? ["creditcard", "applepay"] : ["creditcard"],
          supported_networks: ["mada", "visa", "mastercard"],
          language: "ar",
          // Tokenize only where the server said the plan renews AND supplied
          // the reminder text — which is rendered above, so no card is ever
          // stored under a disclosure the buyer never saw. The consent artefact
          // itself is stamped server-side at capture, from the completed
          // purchase. `basic` never gets here: the server does not set the flag
          // for a plan that cannot renew, and storing a credential with no
          // purpose is exactly what PDPL data-minimisation forbids.
          ...(requiresConsent ? { credit_card: { save_card: true } } : {}),
          ...(canApplePay
            ? {
                apple_pay: {
                  country: "SA",
                  label: "ريحان",
                  // Moyasar's own endpoint, called straight from the browser —
                  // their documented integration. NOT a route of ours; see
                  // MOYASAR_APPLEPAY_VALIDATE_URL for why proxying it through
                  // our origin silently killed every Apple Pay payment.
                  validate_merchant_url: MOYASAR_APPLEPAY_VALIDATE_URL,
                },
              }
            : {}),
          on_completed: async (payment: MoyasarPayment) => {
            // Fire-and-forget by design. A failure here must NOT abort the
            // payment: the form treats a rejected `on_completed` as a failed
            // checkout, and the webhook plus the callback-page verify both
            // still reach the same grant path. Losing this call costs us the
            // early id binding, nothing more.
            try {
              await paymentsApi.verify(payment.id);
            } catch {
              // Swallowed on purpose — see above.
            }
          },
          on_failure: () => {
            setFormError(
              "تعذّر إتمام الدفع. تأكد من بيانات البطاقة ثم حاول مرة أخرى.",
            );
          },
        });
        if (!cancelled) setFormLoading(false);
      } catch {
        if (!cancelled) {
          setFormLoading(false);
          setFormError(
            "تعذّر تحميل نموذج الدفع. تحقق من اتصالك ثم أعد تحميل الصفحة.",
          );
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [session, requiresConsent]);

  if (!plan) {
    return (
      <div
        className="flex flex-col items-center gap-4 py-16 text-center"
        data-testid="pay-unknown-plan"
      >
        <AlertTriangle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-foreground">
          هذه الباقة غير موجودة.
        </p>
        <Link
          href="/pricing"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          استعرض الباقات المتاحة
        </Link>
      </div>
    );
  }

  // Wire format is a 2-dp string ("77.91") — Number() before comparing.
  const creditSar = Number(session?.credit_sar ?? 0);
  const hasCredit = creditSar > 0;

  return (
    <div className="flex flex-col gap-8" data-testid="pay-page">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-foreground">
          إتمام الاشتراك — باقة {plan.nameAr}
        </h1>
        <p className="text-sm text-muted-foreground">{plan.billingNote}</p>
      </header>

      {/* Order summary. When a prorated upgrade credit applies, the charged
          amount is what gets the visual weight — the catalog price is context,
          the number on the card is what the user is agreeing to. */}
      <section className="flex flex-col gap-3 rounded-2xl border border-border bg-card p-5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm text-muted-foreground">سعر الباقة</span>
          <span className="flex items-center gap-1 text-sm tabular-nums text-foreground">
            {plan.price}
            <RiyalSymbol className="h-3.5 w-auto" />
          </span>
        </div>

        {hasCredit && (
          <div
            className="flex items-baseline justify-between gap-3"
            data-testid="pay-upgrade-credit"
          >
            <span className="text-sm text-muted-foreground">
              خصم القيمة المتبقية من باقتك الحالية
            </span>
            <span className="flex items-center gap-1 text-sm tabular-nums text-success-fg">
              −{formatSar(creditSar)}
              <RiyalSymbol className="h-3.5 w-auto" />
            </span>
          </div>
        )}

        <div className="mt-1 flex items-end justify-between gap-3 border-t border-border pt-3">
          <span className="text-sm font-semibold text-foreground">
            المبلغ المستحق
          </span>
          <span
            className="flex items-center gap-1.5 text-3xl font-bold leading-none tabular-nums text-foreground"
            data-testid="pay-amount-due"
          >
            {session ? formatHalalas(session.amount_halalas) : "—"}
            <RiyalSymbol className="h-6 w-auto" />
          </span>
        </div>
      </section>

      {/* Checkout failed to open — the server's Arabic message is the primary
          text; the fallbacks below only cover a body we could not parse. */}
      {checkout.isError && (
        <div
          className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
          data-testid="pay-checkout-error"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="flex flex-col gap-2">
            <p className="text-sm text-destructive">
              {checkout.error?.message ||
                (checkout.error?.status === 503
                  ? "الدفع غير متاح حالياً. حاول مرة أخرى بعد قليل."
                  : "تعذّر بدء عملية الدفع. حاول مرة أخرى.")}
            </p>
            <Link
              href="/pricing"
              className="text-xs font-medium text-primary underline-offset-4 hover:underline"
            >
              العودة إلى الباقات
            </Link>
          </div>
        </div>
      )}

      {checkout.isPending && (
        <div className="flex items-center justify-center gap-3 py-10">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          <span className="text-sm text-muted-foreground">
            جارٍ تجهيز عملية الدفع…
          </span>
        </div>
      )}

      {/* The renewal reminder — a line, not a box (owner, 2026-08-12).
          Still ABOVE the card fields: the disclosure has to be readable before
          a card is entered for completing the purchase to mean anything as
          consent. Rendered verbatim; never composed here. */}
      {session && requiresConsent && disclosure && (
        <p
          className="flex items-start gap-2 text-sm leading-relaxed text-muted-foreground"
          data-testid="recurring-consent-disclosure"
        >
          <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <span className="whitespace-pre-line">{disclosure}</span>
        </p>
      )}

      {/* The Moyasar form replaces this node's contents. It must stay mounted
          for the whole visit — re-rendering it away would drop the live form,
          so never make this subtree conditional on anything that changes
          mid-visit. A remounted form is a dead form. */}
      {session && (
        <section className="flex flex-col gap-4">
          {formLoading && (
            <div className="flex items-center justify-center gap-3 py-6">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <span className="text-sm text-muted-foreground">
                جارٍ تحميل نموذج الدفع…
              </span>
            </div>
          )}

          <div
            id={MOYASAR_FORM_ELEMENT_ID}
            className="mysr-form"
            data-testid="moyasar-form"
          />

          {formError && (
            <div
              className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
              data-testid="pay-form-error"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <div className="flex flex-col gap-2">
                <p className="text-sm text-destructive">{formError}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  onClick={() => window.location.reload()}
                  data-testid="pay-form-retry"
                >
                  إعادة المحاولة
                </Button>
              </div>
            </div>
          )}
        </section>
      )}

      {/* Refund terms are deliberately NOT here (owner, 2026-08-04): shown
          only at the refund action itself (PaymentHistoryDialog), where they
          can't be misread as a general anytime-refund promise. */}
      <footer className="flex flex-col gap-2 border-t border-border pt-5">
        <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
          <span>{PAYMENT_TRUST_NOTE}</span>
        </p>
      </footer>
    </div>
  );
}
