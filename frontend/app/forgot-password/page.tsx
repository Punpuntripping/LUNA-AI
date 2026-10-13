"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { PasswordCodeReset } from "@/components/auth/PasswordCodeReset";
import { DEFAULT_NEXT } from "@/lib/safe-next";

/**
 * «نسيت كلمة المرور» — reset with an emailed CODE, on one page.
 *
 * Owner decision 2026-10-13: on the web a password is set or changed by an
 * email code ONLY — no email link, no current-password form. Same backend
 * routes the mobile app uses (POST /auth/password/forgot → /auth/password/reset),
 * so the PKCE "open the link in the same browser" constraint of the old
 * supabase-js `resetPasswordForEmail` flow is gone with it.
 *
 * Success = a normal sign-in: `resetPasswordWithCode` runs the same session
 * tail as a password login (stores/auth-store establishSession), then this page
 * lands where LoginForm lands without a `next` — /chat.
 *
 * Old reset LINKS (emailed before 2026-10-13) are routed here by
 * app/auth/callback and app/reset-password with `?link=expired`, WITHOUT their
 * code being exchanged. That flag — and anything else a link dragged along,
 * including a `#access_token=…` fragment, which survives HTTP redirects — is
 * wiped from the address bar on mount.
 */
export default function ForgotPasswordPage() {
  const router = useRouter();
  const [linkRetired, setLinkRetired] = useState(false);

  useEffect(() => {
    const { search, hash, pathname } = window.location;
    if (new URLSearchParams(search).get("link") === "expired") {
      setLinkRetired(true);
    }
    if (search || hash) {
      window.history.replaceState(null, "", pathname);
    }
  }, []);

  return (
    <div
      className="flex min-h-dvh items-center justify-center bg-background px-4 py-10"
      dir="rtl"
      lang="ar"
    >
      <div className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-border bg-card p-8">
        <div className="flex flex-col gap-2">
          <h1 className="text-lg font-semibold text-foreground">
            نسيت كلمة المرور؟
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            أدخل بريدك الإلكتروني وسنرسل إليك رمزاً لتعيين كلمة مرور جديدة.
          </p>
        </div>

        {linkRetired && (
          <p
            className="rounded-md bg-muted px-3 py-2 text-sm leading-relaxed text-foreground"
            data-testid="forgot-password-link-retired"
          >
            روابط تعيين كلمة المرور لم تعد مستخدمة. أدخل بريدك وسنرسل إليك
            رمزاً بدلاً منها.
          </p>
        )}

        <PasswordCodeReset
          idPrefix="forgot-password"
          sendLabel="إرسال الرمز"
          submitLabel="حفظ والدخول"
          emailStepNote={
            <p className="text-sm leading-relaxed text-muted-foreground">
              إذا كنت تدخل عادةً بحساب Google، يمكنك بهذه الطريقة إنشاء كلمة
              مرور.
            </p>
          }
          onSuccess={() => {
            router.push(DEFAULT_NEXT);
          }}
        />

        <Link
          href="/login"
          className="flex items-center justify-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowRight className="h-4 w-4" aria-hidden />
          العودة لتسجيل الدخول
        </Link>
      </div>
    </div>
  );
}
