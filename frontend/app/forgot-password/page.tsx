"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2, MailCheck } from "lucide-react";
import { z } from "zod";
import { supabase } from "@/lib/supabase";
import { Button } from "@/components/ui/button";

/**
 * «نسيت كلمة المرور» — request a password-reset link.
 *
 * ⚠ The reset is requested CLIENT-side, not through our backend, and that is
 * deliberate. Supabase runs the PKCE flow: `resetPasswordForEmail` stores a
 * code_verifier in THIS browser, and `/auth/callback` needs that same verifier
 * to exchange the emailed `?code=` for a session. A backend-initiated reset
 * leaves no verifier anywhere, so the link in the email would land on a
 * callback that cannot complete. Signup already works this way for exactly the
 * same reason — see the note above /refresh in backend/app/api/auth.py.
 *
 * Consequence worth knowing: the link must be opened in the same browser that
 * asked for it. That is standard Supabase behaviour, and the copy below says so.
 *
 * Enumeration safety is GoTrue's, not ours: `/auth/v1/recover` answers 200 for
 * an address with no account (verified against prod). So this page does NOT need
 * to hide failures to stay safe — and MUST NOT, which is the bug fixed here.
 * The first cut ignored the returned error and showed «تحقّق من بريدك» no matter
 * what; with SMTP down that told every user their link was on the way while
 * GoTrue was 500ing on every single send. A non-200 is a mail-system failure,
 * which is true whether or not the address exists, so surfacing it leaks nothing
 * and is the only way the user learns to retry.
 *
 * ⚠ `resetPasswordForEmail` RESOLVES with `{ error }` — it does not throw. The
 * `catch` below only ever sees a transport/CSP failure, so checking the returned
 * error is not optional.
 */

const emailSchema = z.string().email("البريد الإلكتروني غير صحيح");

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [isSending, setIsSending] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const parsed = emailSchema.safeParse(email.trim());
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "البريد الإلكتروني غير صحيح");
      return;
    }

    setIsSending(true);
    try {
      // `next` rides the callback so the exchange lands on the reset form
      // rather than /chat. It is allowlisted in lib/safe-next.ts.
      const { error: resetError } = await supabase.auth.resetPasswordForEmail(
        parsed.data,
        {
          redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(
            "/reset-password",
          )}`,
        },
      );

      if (resetError) {
        // 429 is GoTrue's own per-address/per-hour email cap — a real answer to
        // "why didn't it arrive", and worth saying plainly rather than folding
        // into the generic failure.
        setError(
          resetError.status === 429
            ? "تم إرسال رابط بالفعل. انتظر قليلًا قبل المحاولة مرة أخرى."
            : "تعذّر إرسال الرابط حاليًا. حاول بعد قليل، وإن تكرّر الأمر تواصل معنا.",
        );
        return;
      }

      // 200 — shown identically whether or not the address has an account,
      // because that is exactly what GoTrue returned.
      setSent(true);
    } catch {
      // Only reached on a transport-level failure (offline, CSP, DNS).
      setError("تعذّر الاتصال. تحقّق من الشبكة وحاول مجددًا.");
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div
      className="flex min-h-dvh items-center justify-center bg-background px-4 py-10"
      dir="rtl"
      lang="ar"
    >
      <div className="w-full max-w-md">
        {sent ? (
          <div
            className="flex flex-col items-center gap-4 rounded-lg border border-border bg-card p-8 text-center"
            data-testid="forgot-password-sent"
          >
            <MailCheck className="h-10 w-10 text-primary" aria-hidden />
            <h1 className="text-lg font-semibold text-foreground">
              تحقّق من بريدك الإلكتروني
            </h1>
            <p className="text-sm leading-relaxed text-muted-foreground">
              إن كان لديك حساب بهذا البريد، فقد أرسلنا إليك رابطًا لتعيين كلمة
              مرور جديدة. افتح الرابط من هذا المتصفّح نفسه.
            </p>
            {/* Observed on the very first real send (2026-08-18): Gmail filed it
                as spam. `rayhanai.com` only started sending through Resend that
                day, so the domain has no sending reputation yet — SPF and DKIM
                both align, but reputation is earned over weeks of delivered
                mail. Until it settles, a user who does not think to look in
                spam concludes the reset is broken and leaves. */}
            <p className="rounded-md bg-muted px-3 py-2 text-sm leading-relaxed text-foreground">
              لم تصلك الرسالة؟ تحقّق من مجلد <strong>الرسائل غير المرغوب فيها
              (Spam)</strong> — قد تصل إليه أحيانًا.
            </p>
            <Link
              href="/login"
              className="text-sm font-medium text-primary hover:underline"
            >
              العودة لتسجيل الدخول
            </Link>
          </div>
        ) : (
          <form
            onSubmit={handleSubmit}
            className="flex flex-col gap-4 rounded-lg border border-border bg-card p-8"
            noValidate
          >
            <div className="flex flex-col gap-2">
              <h1 className="text-lg font-semibold text-foreground">
                نسيت كلمة المرور؟
              </h1>
              <p className="text-sm leading-relaxed text-muted-foreground">
                أدخل بريدك الإلكتروني وسنرسل إليك رابطًا لتعيين كلمة مرور جديدة.
              </p>
            </div>

            <div className="flex flex-col gap-2">
              <label
                htmlFor="forgot-password-email"
                className="block text-sm font-medium text-foreground"
              >
                البريد الإلكتروني
              </label>
              <input
                id="forgot-password-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                dir="ltr"
                data-testid="forgot-password-email"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {error && (
                <p
                  className="text-xs text-destructive"
                  data-testid="forgot-password-error"
                >
                  {error}
                </p>
              )}
            </div>

            <Button
              type="submit"
              disabled={isSending}
              className="w-full"
              data-testid="forgot-password-submit"
            >
              {isSending && <Loader2 className="h-4 w-4 animate-spin" />}
              {isSending ? "جارٍ الإرسال…" : "إرسال الرابط"}
            </Button>

            <Link
              href="/login"
              className="flex items-center justify-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowRight className="h-4 w-4" aria-hidden />
              العودة لتسجيل الدخول
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
