"use client";

import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";

interface RecurringConsentGateProps {
  /**
   * The server's `recurring_disclosure_ar`, rendered **verbatim**.
   *
   * ⚠ Never reword, re-order, translate, re-punctuate or interpolate into it.
   * The server hashes its own copy of this exact string as the consent artefact
   * (`payment_methods.consent_text_hash`); anything the client rewrites can no
   * longer be proven against that hash. If the amount or the date reads oddly,
   * the fix belongs in the backend copy, not here.
   */
  disclosure: string;
  /** Consent recorded server-side. The card form is live from this moment. */
  accepted: boolean;
  /** The POST is in flight. */
  pending: boolean;
  /** Arabic failure text — the checkbox re-arms so the user can try again. */
  error: string | null;
  onAccept: () => void;
}

/**
 * Pre-purchase recurring consent on `/pay/{plan}`
 * (`.claude/plans/subscription_auto_renewal.md` §6 + §9).
 *
 * One artefact serving two masters: the KSA e-commerce disclosure obligation
 * and the card-scheme stored-credential consent. Both want the same thing —
 * the cardholder saw the terms of the recurring charge, at the point of sale,
 * before the card was entered — which is why this renders ABOVE the Moyasar
 * form and why that form does not exist until the tick is recorded.
 *
 * Purely presentational: the page owns the mutation, because the page also owns
 * the one-shot mount effect that must not fire until it succeeds.
 */
export function RecurringConsentGate({
  disclosure,
  accepted,
  pending,
  error,
  onAccept,
}: RecurringConsentGateProps) {
  return (
    <section
      className="flex flex-col gap-3 rounded-2xl border border-border bg-card p-5"
      data-testid="recurring-consent"
    >
      <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <RefreshCw className="h-4 w-4 shrink-0 text-primary" />
        التجديد التلقائي
      </h2>

      {/* `whitespace-pre-line` so a multi-line server string keeps the shape it
          was hashed in. */}
      <p
        className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground"
        data-testid="recurring-consent-disclosure"
      >
        {disclosure}
      </p>

      <div className="flex items-start gap-2 text-sm text-foreground">
        <input
          id="recurring-consent-checkbox"
          type="checkbox"
          // Ticked optimistically while the POST is in flight so the control
          // never fights the pointer, and locked once recorded: un-ticking
          // would have to tear down a live Moyasar form, and the artefact is
          // already written. Backing out is «العودة إلى الباقات» — or, after
          // paying, «إزالة البطاقة» in إعدادات الحساب.
          checked={accepted || pending}
          disabled={accepted || pending}
          onChange={(e) => {
            if (e.target.checked) onAccept();
          }}
          data-testid="recurring-consent-checkbox"
          className="mt-0.5 h-4 w-4 shrink-0 rounded border-input accent-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed"
        />
        <label
          htmlFor="recurring-consent-checkbox"
          className="cursor-pointer leading-relaxed"
        >
          أوافق على التجديد التلقائي وفق ما هو موضّح أعلاه
        </label>
      </div>

      {pending && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          جارٍ تسجيل موافقتك…
        </p>
      )}

      {error && (
        <p
          className="flex items-start gap-2 text-sm text-destructive"
          data-testid="recurring-consent-error"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </p>
      )}

      {accepted ? (
        <p
          className="flex items-start gap-2 text-xs text-success-fg"
          data-testid="recurring-consent-accepted"
        >
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          تم تسجيل موافقتك. يمكنك الآن إدخال بيانات البطاقة.
        </p>
      ) : (
        !pending && (
          <p className="text-xs text-muted-foreground">
            يظهر نموذج الدفع بعد الموافقة.
          </p>
        )
      )}
    </section>
  );
}
