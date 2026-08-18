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
 * The response is deliberately identical whether or not the address has an
 * account: a page that says "no such user" is an account-enumeration oracle on
 * a public, unauthenticated route.
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
      await supabase.auth.resetPasswordForEmail(parsed.data, {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(
          "/reset-password",
        )}`,
      });
      // Success regardless of the result — see the enumeration note above. A
      // genuine transport failure still lands here; the user retries, and the
      // alternative (leaking which addresses exist) is worse.
      setSent(true);
    } catch {
      setSent(true);
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
