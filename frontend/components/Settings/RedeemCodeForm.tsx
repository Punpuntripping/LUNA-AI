"use client";

import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRedeemCode } from "@/hooks/use-redeem-code";
import type { RedeemCodeResponse } from "@/types";

/**
 * The activation-code form itself — input, error, submit, success card.
 *
 * Extracted from `RedeemCodeDialog` when the two-week «تفعيل برمز» popup
 * (`components/promo`) became a second entry point: both need identical
 * normalization and identical error rendering, and the one thing that must
 * never drift between them is what a code does when you type it. Each caller
 * supplies its own Dialog shell, its own headline, and its own footer buttons.
 *
 * State lives here and dies with the component. Both callers render this
 * inside a Radix `DialogContent`, which unmounts on close, so a dismissed
 * dialog always reopens on a clean field with no stale error.
 */

/** Normalize as the user types: uppercase, drop anything that isn't a base32
 *  character, cap length. Mirrors the server-side normalization so what the
 *  user sees is exactly what gets sent. */
function normalizeCode(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, 12);
}

interface RedeemCodeFormProps {
  /** Fired exactly once, after the server has activated a plan. The caller
   *  decides what happens next — the settings dialog simply stays on the
   *  success card; the promo popup also marks itself seen. */
  onRedeemed?: (result: RedeemCodeResponse) => void;
  /** Rendered under the submit button while the field is showing. */
  footer?: React.ReactNode;
  /** Rendered under the success card once a code has activated. */
  successFooter?: React.ReactNode;
}

export function RedeemCodeForm({
  onRedeemed,
  footer,
  successFooter,
}: RedeemCodeFormProps) {
  const [code, setCode] = useState("");
  const redeem = useRedeemCode();

  const canSubmit = code.length >= 3 && !redeem.isPending;

  const handleSubmit = () => {
    if (!canSubmit) return;
    redeem.mutate(code);
  };

  const success = redeem.isSuccess ? redeem.data : null;

  // Notify on the transition, not on every render — `onRedeemed` may PATCH a
  // preference, and firing it once per re-render would spam the endpoint.
  useEffect(() => {
    if (success) onRedeemed?.(success);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [redeem.isSuccess]);

  if (success) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-3 rounded-md border border-success-fg/25 bg-success p-4">
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success-fg" />
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium text-success-fg">
              تم تفعيل باقتك بنجاح
            </p>
            <p className="text-xs text-success-fg/80">
              باقتك الحالية: {success.name_ar ?? success.plan_id}
            </p>
          </div>
        </div>
        {successFooter}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <input
        type="text"
        inputMode="text"
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        value={code}
        onChange={(e) => setCode(normalizeCode(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleSubmit();
        }}
        placeholder="مثال: K7P2M"
        autoFocus
        dir="ltr"
        data-testid="redeem-code-input"
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-center font-mono text-lg uppercase tracking-[0.3em] ring-offset-background outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 placeholder:text-sm placeholder:tracking-normal placeholder:text-muted-foreground"
      />

      {redeem.isError && (
        <p className="text-sm text-destructive" data-testid="redeem-code-error">
          {redeem.error?.message || "تعذّر تفعيل الرمز. حاول مجددًا."}
        </p>
      )}

      <Button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full"
        data-testid="redeem-code-submit"
      >
        {redeem.isPending ? "جارٍ التفعيل…" : "تفعيل"}
      </Button>

      {footer}
    </div>
  );
}
