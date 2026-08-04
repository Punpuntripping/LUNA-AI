"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useVerifyPayment } from "@/hooks/use-payments";
import { useAuthStore } from "@/stores/auth-store";
import type { PaymentVerifyResponse } from "@/types";

/**
 * Delays between `/verify` attempts, in ms. The first is immediate; the rest
 * back off to ~17.5s total across five calls.
 *
 * This is a SHORT poll on purpose. It exists to cover the seconds between the
 * card network answering and Moyasar's record settling — not to be the system's
 * reliability story. That is the webhook, which grants independently and stays
 * live long after this tab is closed. A minutes-long spinner would be pretending
 * the browser is the source of truth.
 */
const VERIFY_DELAYS_MS = [0, 1_500, 3_000, 5_000, 8_000] as const;

type Phase =
  /** No `?id=` on the URL at all. */
  | "missing"
  /** Session restore still in flight — the POST is gated on it. */
  | "restoring"
  | "verifying"
  | "paid"
  /** Still not terminal after the poll — the webhook will finish it. */
  | "processing"
  | "failed"
  /** The verify call itself failed (transport / 4xx). */
  | "error";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/**
 * Payment callback — `/pay/callback?id=<moyasar_uuid>`.
 *
 * ⚠ THIS PAGE IS A COLD BOOT. 3DS is a full-page redirect, so by the time the
 * browser lands here the React tree that started the payment is gone, and with
 * it the in-memory access token (rule 4). The `/verify` POST is therefore GATED
 * on session restore completing — `isLoading === false && isAuthenticated`.
 * Firing it earlier sends an unauthenticated request, and a 401 would be
 * misread as a failed payment on a payment that actually succeeded. That is the
 * worst possible bug on this screen: it tells someone who was just charged that
 * their money went nowhere.
 *
 * (`AuthGuard` already withholds children until the probe resolves, so this
 * gate is the second of two. Both stay: the guard's behaviour is about routing,
 * and a future change to it must not be able to silently un-gate a payment.)
 *
 * ⚠ `?id=` IS ATTACKER-CONTROLLED. Nothing here treats it as proof of anything
 * — the server binds it to our row via `metadata.payment_id` AND the caller's
 * `user_id` before it will grant (plan trap 6).
 */
// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function PayCallbackPage() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const authLoading = useAuthStore((s) => s.isLoading);
  const { mutateAsync: verifyPayment } = useVerifyPayment();

  const [moyasarId, setMoyasarId] = useState<string | null>(null);
  const [idRead, setIdRead] = useState(false);
  const [phase, setPhase] = useState<Phase>("restoring");
  const [result, setResult] = useState<PaymentVerifyResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const startedRef = useRef(false);

  // `window.location.search` inside an effect rather than `useSearchParams()`
  // — the file-level idiom in this codebase (see LoginForm), and it keeps the
  // route out of the "wrap in a Suspense boundary" build failure.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    setMoyasarId(id && id.trim() ? id.trim() : null);
    setIdRead(true);
  }, []);

  useEffect(() => {
    if (!idRead) return;
    if (!moyasarId) {
      setPhase("missing");
      return;
    }
    // THE GATE. Do not remove.
    if (authLoading) {
      setPhase("restoring");
      return;
    }
    if (!isAuthenticated) {
      // AuthGuard is already redirecting to /login. The webhook still grants,
      // so this is a lost confirmation, not a lost purchase.
      setPhase("restoring");
      return;
    }
    if (startedRef.current) return;
    startedRef.current = true;

    let cancelled = false;

    void (async () => {
      setPhase("verifying");
      for (const delay of VERIFY_DELAYS_MS) {
        if (delay > 0) await sleep(delay);
        if (cancelled) return;

        try {
          const res = await verifyPayment(moyasarId);
          if (cancelled) return;
          setResult(res);

          // `paid` alone is NOT success: the backend reports `granted:false`
          // when the money is in but the grant hasn't been applied yet (money
          // in, term pending). Claiming «تم تفعيل باقتك» then would be a lie
          // the user can disprove by opening the app. Keep polling — a later
          // verify retries the grant; if the poll runs out, "processing" is
          // the honest state and the webhook finishes the job.
          if (res.status === "paid" && res.granted !== false) {
            setPhase("paid");
            return;
          }
          if (res.status === "failed") {
            setPhase("failed");
            return;
          }
          // `initiated` / `pending` / paid-but-ungranted — keep polling.
        } catch (err) {
          if (cancelled) return;
          setErrorMessage(
            err instanceof Error && err.message
              ? err.message
              : "تعذّر التحقق من عملية الدفع.",
          );
          setPhase("error");
          return;
        }
      }

      if (!cancelled) setPhase("processing");
    })();

    return () => {
      cancelled = true;
    };
  }, [idRead, moyasarId, authLoading, isAuthenticated, verifyPayment, attempt]);

  const retryVerify = useCallback(() => {
    startedRef.current = false;
    setErrorMessage(null);
    setResult(null);
    setAttempt((n) => n + 1);
  }, []);

  // Where a failed payment sends the user back to. The plan comes from the
  // verify response when the server knows it; /pricing is the honest fallback
  // rather than guessing a plan the user did not choose.
  const retryHref = result?.plan_id ? `/pay/${result.plan_id}` : "/pricing";

  return (
    <div
      className="flex flex-col items-center gap-6 py-10 text-center"
      data-testid="pay-callback"
      data-phase={phase}
    >
      {(phase === "restoring" || phase === "verifying") && (
        <>
          <Loader2 className="h-10 w-10 animate-spin text-primary" />
          <div className="flex flex-col gap-2">
            <h1 className="text-xl font-bold text-foreground">
              {phase === "restoring"
                ? "جارٍ استعادة جلستك…"
                : "جارٍ تأكيد عملية الدفع…"}
            </h1>
            <p className="max-w-sm text-sm text-muted-foreground">
              لا تُغلق هذه الصفحة. قد تستغرق العملية بضع ثوانٍ.
            </p>
          </div>
        </>
      )}

      {phase === "paid" && (
        <>
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-success">
            <CheckCircle2 className="h-7 w-7 text-success-fg" />
          </div>
          <div className="flex flex-col gap-2">
            <h1
              className="text-xl font-bold text-foreground"
              data-testid="pay-success-title"
            >
              تم تفعيل باقتك
            </h1>
            <p className="max-w-sm text-sm text-muted-foreground">
              {result?.plan_name_ar
                ? `باقتك الحالية: ${result.plan_name_ar}. يمكنك البدء فوراً.`
                : "تم استلام دفعتك وتفعيل اشتراكك. يمكنك البدء فوراً."}
            </p>
          </div>
          <Link href="/chat">
            <Button data-testid="pay-success-cta">ابدأ الاستخدام</Button>
          </Link>
          {/* No refund copy on the success screen (owner, 2026-08-04) — it
              reads as an invitation. It lives at the refund action only. */}
        </>
      )}

      {phase === "processing" && (
        <>
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted">
            <Clock className="h-7 w-7 text-muted-foreground" />
          </div>
          <div className="flex flex-col gap-2">
            <h1
              className="text-xl font-bold text-foreground"
              data-testid="pay-processing-title"
            >
              دفعتك قيد المعالجة
            </h1>
            {/* Reassurance, and it is literally true: the webhook grants
                independently of this tab, so closing the page costs nothing. */}
            <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
              استلمنا عمليتك ولم تُؤكَّد بعد لدى مزوّد الدفع. سيتم تفعيل باقتك
              تلقائياً خلال دقائق دون أي إجراء منك — يمكنك إغلاق هذه الصفحة
              بأمان. تجد حالة العملية في «سجل المدفوعات» داخل الإعدادات.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={retryVerify}>
              تحديث الحالة
            </Button>
            <Link href="/chat">
              <Button>العودة إلى ريحان</Button>
            </Link>
          </div>
        </>
      )}

      {phase === "failed" && (
        <>
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-destructive/10">
            <AlertTriangle className="h-7 w-7 text-destructive" />
          </div>
          <div className="flex flex-col gap-2">
            <h1
              className="text-xl font-bold text-foreground"
              data-testid="pay-failed-title"
            >
              لم تكتمل عملية الدفع
            </h1>
            <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
              {result?.message ||
                "لم يقبل مزوّد الدفع هذه العملية ولم يُخصم منك أي مبلغ. يمكنك المحاولة ببطاقة أخرى."}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link href={retryHref}>
              <Button data-testid="pay-failed-retry">إعادة المحاولة</Button>
            </Link>
            <Link
              href="/pricing"
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              استعراض الباقات
            </Link>
          </div>
        </>
      )}

      {phase === "error" && (
        <>
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-destructive/10">
            <AlertTriangle className="h-7 w-7 text-destructive" />
          </div>
          <div className="flex flex-col gap-2">
            <h1 className="text-xl font-bold text-foreground">
              تعذّر تأكيد العملية
            </h1>
            {/* Careful wording: a verify failure says nothing about whether the
                charge went through, so this must not claim either outcome. */}
            <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
              {errorMessage} إن تم خصم المبلغ فسيُفعَّل اشتراكك تلقائياً خلال
              دقائق. راجع «سجل المدفوعات» داخل الإعدادات قبل إعادة المحاولة.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={retryVerify}>
              إعادة المحاولة
            </Button>
            <Link href="/chat">
              <Button>العودة إلى ريحان</Button>
            </Link>
          </div>
        </>
      )}

      {phase === "missing" && (
        <>
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted">
            <AlertTriangle className="h-7 w-7 text-muted-foreground" />
          </div>
          <div className="flex flex-col gap-2">
            <h1
              className="text-xl font-bold text-foreground"
              data-testid="pay-missing-id"
            >
              لا توجد عملية دفع لعرضها
            </h1>
            <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
              لم يصلنا معرّف العملية في الرابط. إن كنت قد أتممت الدفع للتو
              فسيُفعَّل اشتراكك تلقائياً — تجد حالة العملية في «سجل المدفوعات»
              داخل الإعدادات.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/pricing">
              <Button variant="outline">استعراض الباقات</Button>
            </Link>
            <Link href="/chat">
              <Button>العودة إلى ريحان</Button>
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
