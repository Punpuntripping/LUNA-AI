"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { ApiClientError } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { LEGAL_VERSION, LEGAL_ROUTES } from "@/lib/legal";
import { DEFAULT_NEXT, safeNext } from "@/lib/safe-next";
// The "G" mark moved to the shared quick-signup module so the gate surfaces
// and this form render the identical logo.
import { GoogleIcon } from "@/components/auth/GoogleQuickSignup";
import { PasswordInput } from "@/components/ui/password-input";

// -----------------------------------------------
// Zod schemas with Arabic error messages
// -----------------------------------------------

const loginSchema = z.object({
  email: z
    .string()
    .min(1, "البريد الإلكتروني مطلوب")
    .email("صيغة البريد الإلكتروني غير صحيحة"),
  password: z
    .string()
    .min(1, "كلمة المرور مطلوبة")
    .min(8, "كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل"),
});

const registerSchema = loginSchema.extend({
  full_name_ar: z.string().min(1, "الاسم الكامل مطلوب"),
});

type LoginFormData = z.infer<typeof loginSchema>;
type RegisterFormData = z.infer<typeof registerSchema>;

// -----------------------------------------------
// Component
// -----------------------------------------------

export function LoginForm() {
  const router = useRouter();
  const { login, register } = useAuthStore();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // Neutral, non-destructive message (e.g. an email link that could not be
  // exchanged here). Deliberately separate from serverError — it is not a
  // failure the visitor caused and must not render in the red box.
  const [notice, setNotice] = useState<string | null>(null);
  // Raw `?next=` as it arrived; validated on every read via safeNext().
  const [nextParam, setNextParam] = useState<string | null>(null);
  const [registrationSuccess, setRegistrationSuccess] = useState(false);
  // Option B consent: registration is blocked until this is checked.
  const [agreedToTerms, setAgreedToTerms] = useState(false);
  // Marketing consent: optional, default ON — never blocks registration.
  const [marketingOptIn, setMarketingOptIn] = useState(true);

  // Read every query parameter this form understands, once, on mount.
  //
  // NOT useSearchParams(): app/login/page.tsx is a server component, and the
  // hook would force the route into client rendering and fail `next build`
  // with "useSearchParams() should be wrapped in a suspense boundary"
  // (anon_conversion_popup.md §7.6 / trap T2). window.location.search inside
  // an effect is the file's existing idiom and needs no Suspense boundary.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);

    // Return-to-page target (§7.2).
    const rawNext = params.get("next");
    if (rawNext) setNextParam(rawNext);

    // «ابدأ الآن» promises signup, so open the form already on signup (§7.7).
    if (params.get("mode") === "register") setMode("register");

    // The email-confirmation link could not be exchanged: expired, already
    // used, or opened in a different browser than the one that signed up (PKCE
    // code_verifier is per-browser). /auth/callback cannot tell which — the
    // reason arrives in the URL fragment, which never reaches a route handler
    // (§7.4 / T1b) — so the copy names no cause. Not an error, not the Google
    // message.
    if (params.get("notice") === "verify_elsewhere") {
      setNotice("تم تأكيد بريدك. سجّل الدخول للمتابعة.");
    }

    // Surface OAuth failures redirected back from /auth/callback?error=oauth.
    if (params.get("error") === "oauth") {
      setServerError("تعذّر تسجيل الدخول عبر Google. حاول مرة أخرى.");
      // Drop the error from the address bar but keep `next`, so a reload still
      // returns the visitor to the page they came from.
      const keep = safeNext(rawNext);
      window.history.replaceState(
        null,
        "",
        keep === DEFAULT_NEXT
          ? window.location.pathname
          : `${window.location.pathname}?next=${encodeURIComponent(keep)}`,
      );
    }
  }, []);

  // Field values
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullNameAr, setFullNameAr] = useState("");

  // Field errors
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Where a successful sign-in lands. Allowlisted on every read (trap T3), so
  // a hostile or stale `next` silently degrades to today's /chat.
  const nextTarget = safeNext(nextParam);
  /** The same value, but undefined when it is just the default — keeps a dead
   *  `?next=/chat` off the OAuth and confirmation-email URLs. */
  const returnTo = nextTarget === DEFAULT_NEXT ? undefined : nextTarget;

  const toggleMode = () => {
    setMode((prev) => (prev === "login" ? "register" : "login"));
    setErrors({});
    setServerError(null);
    setNotice(null);
    setRegistrationSuccess(false);
    setAgreedToTerms(false);
    setMarketingOptIn(true);
  };

  const validate = (): boolean => {
    const schema = mode === "login" ? loginSchema : registerSchema;
    const data =
      mode === "login"
        ? { email, password }
        : { email, password, full_name_ar: fullNameAr };

    const result = schema.safeParse(data);

    if (!result.success) {
      const fieldErrors: Record<string, string> = {};
      for (const issue of result.error.issues) {
        const field = issue.path[0] as string;
        if (!fieldErrors[field]) {
          fieldErrors[field] = issue.message;
        }
      }
      // Surface the consent error alongside any field errors (option B).
      if (mode === "register" && !agreedToTerms) {
        fieldErrors.terms = "يجب الموافقة على الشروط وسياسة الخصوصية";
      }
      setErrors(fieldErrors);
      return false;
    }

    // Schema passed — still block registration without consent.
    if (mode === "register" && !agreedToTerms) {
      setErrors({ terms: "يجب الموافقة على الشروط وسياسة الخصوصية" });
      return false;
    }

    setErrors({});
    return true;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setServerError(null);

    if (!validate()) return;

    setIsSubmitting(true);

    try {
      if (mode === "login") {
        await login(email, password);
        router.push(nextTarget);
      } else {
        const { needsVerification } = await register(
          email,
          password,
          fullNameAr,
          LEGAL_VERSION,
          marketingOptIn,
          returnTo,
        );
        if (needsVerification) {
          setRegistrationSuccess(true);
        } else {
          router.push(nextTarget);
        }
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        setServerError(err.message);
      } else {
        setServerError("حدث خطأ غير متوقع. حاول مرة أخرى.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleGoogleSignIn = async () => {
    setServerError(null);

    // In register mode the checkbox gates Google too (Google auto-creates the
    // account on first sign-in). The always-visible fine print under the button
    // covers the login-mode / first-time-Google path by action.
    if (mode === "register" && !agreedToTerms) {
      setErrors({ terms: "يجب الموافقة على الشروط وسياسة الخصوصية" });
      return;
    }

    setIsGoogleLoading(true);

    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        // `next` rides through Google and back into /auth/callback, which
        // re-validates it before redirecting (§7.2 path B).
        redirectTo: `${window.location.origin}/auth/callback${
          returnTo ? `?next=${encodeURIComponent(returnTo)}` : ""
        }`,
      },
    });

    // On success the browser navigates away to Google — this only runs on
    // failure (e.g. provider misconfigured), so re-enable the button.
    if (error) {
      setServerError("تعذّر تسجيل الدخول عبر Google. حاول مرة أخرى.");
      setIsGoogleLoading(false);
    }
  };

  // Registration success message
  if (registrationSuccess) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center space-y-4">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-success">
          <svg
            className="h-6 w-6 text-success-fg"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
        <h3 className="text-lg font-semibold text-foreground">
          تم إنشاء الحساب بنجاح
        </h3>
        <p className="text-sm text-muted-foreground">
          تم إرسال رابط التحقق إلى بريدك الإلكتروني. يرجى تأكيد بريدك الإلكتروني ثم تسجيل الدخول.
        </p>
        <button
          type="button"
          onClick={() => {
            setMode("login");
            setRegistrationSuccess(false);
          }}
          className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          تسجيل الدخول
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6" noValidate>
      <div className="rounded-lg border border-border bg-card p-6 space-y-4">
        {/* Informational notice (e.g. «تم تأكيد بريدك») — neutral styling on
            purpose: nothing went wrong for the visitor here. */}
        {notice && (
          <div className="rounded-md bg-primary/5 border border-primary/20 p-3 text-sm text-foreground">
            {notice}
          </div>
        )}

        {/* Server error */}
        {serverError && (
          <div className="rounded-md bg-destructive/10 border border-destructive/20 p-3 text-sm text-destructive">
            {serverError}
          </div>
        )}

        {/* Full Name — register only */}
        {mode === "register" && (
          <div className="space-y-2">
            <label
              htmlFor="full_name_ar"
              className="block text-sm font-medium text-foreground"
            >
              الاسم الكامل
            </label>
            <input
              id="full_name_ar"
              type="text"
              value={fullNameAr}
              onChange={(e) => setFullNameAr(e.target.value)}
              placeholder="أدخل اسمك الكامل"
              className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${
                errors.full_name_ar ? "border-destructive" : "border-input"
              }`}
              dir="rtl"
            />
            {errors.full_name_ar && (
              <p className="text-xs text-destructive">{errors.full_name_ar}</p>
            )}
          </div>
        )}

        {/* Email */}
        <div className="space-y-2">
          <label
            htmlFor="email"
            className="block text-sm font-medium text-foreground"
          >
            البريد الإلكتروني
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="example@email.com"
            className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${
              errors.email ? "border-destructive" : "border-input"
            }`}
            dir="ltr"
            autoComplete="email"
          />
          {errors.email && (
            <p className="text-xs text-destructive">{errors.email}</p>
          )}
        </div>

        {/* Password */}
        <PasswordInput
          id="password"
          label="كلمة المرور"
          value={password}
          onChange={setPassword}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          error={errors.password}
          labelAction={
            /* Login only: in register mode there is no password to have
               forgotten, and offering a reset there reads as an error. */
            mode === "login" ? (
              <Link
                href="/forgot-password"
                className="text-xs text-muted-foreground transition-colors hover:text-foreground hover:underline"
                data-testid="forgot-password-link"
              >
                نسيت كلمة المرور؟
              </Link>
            ) : undefined
          }
        />

        {/* Terms consent (option B) — register only */}
        {mode === "register" && (
          <div className="space-y-2">
            {/* The links must NOT be nested inside the <label>: an <a> inside a
                <label> is invalid HTML and the label hijacks the click, so the
                anchors don't navigate. Keep only the checkbox + "أوافق على" in
                the label; render the two links as plain siblings. */}
            <div className="flex items-start gap-2 text-sm text-foreground">
              <input
                id="agree_terms"
                type="checkbox"
                checked={agreedToTerms}
                onChange={(e) => {
                  setAgreedToTerms(e.target.checked);
                  if (e.target.checked && errors.terms) {
                    setErrors((prev) => {
                      const next = { ...prev };
                      delete next.terms;
                      return next;
                    });
                  }
                }}
                data-testid="register-terms-checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-input accent-primary focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <span className="leading-relaxed">
                <label htmlFor="agree_terms" className="cursor-pointer">
                  أوافق على
                </label>{" "}
                <Link
                  href={LEGAL_ROUTES.terms}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:text-primary/80 transition-colors"
                >
                  الشروط والأحكام
                </Link>{" "}
                و{" "}
                <Link
                  href={LEGAL_ROUTES.privacy}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:text-primary/80 transition-colors"
                >
                  سياسة الخصوصية
                </Link>
              </span>
            </div>
            {errors.terms && (
              <p className="text-xs text-destructive">{errors.terms}</p>
            )}

            {/* Marketing consent — optional, default checked, never blocks. */}
            <div className="flex items-start gap-2 text-sm text-foreground">
              <input
                id="marketing_opt_in"
                type="checkbox"
                checked={marketingOptIn}
                onChange={(e) => setMarketingOptIn(e.target.checked)}
                data-testid="register-marketing-checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-input accent-primary focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <label
                htmlFor="marketing_opt_in"
                className="cursor-pointer leading-relaxed"
              >
                أوافق على استلام محتوى ترويجي وتحديثات عبر البريد الإلكتروني
              </label>
            </div>
          </div>
        )}

        {/* Submit button */}
        <button
          type="submit"
          disabled={
            isSubmitting ||
            isGoogleLoading ||
            (mode === "register" && !agreedToTerms)
          }
          className="w-full flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {mode === "login" ? "تسجيل الدخول" : "إنشاء حساب"}
        </button>

        {/* Divider */}
        <div className="flex items-center gap-3">
          <div className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground">أو</span>
          <div className="h-px flex-1 bg-border" />
        </div>

        {/* Google sign-in */}
        <button
          type="button"
          onClick={handleGoogleSignIn}
          disabled={isSubmitting || isGoogleLoading}
          className="w-full flex items-center justify-center gap-2 rounded-md border border-input bg-background px-4 py-2.5 text-sm font-medium text-foreground hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isGoogleLoading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <GoogleIcon />
          )}
          المتابعة مع Google
        </button>

        {/* Google consent fine-print — always visible (covers first-time
            Google sign-in, which auto-creates an account, in both modes). */}
        <p className="text-center text-xs leading-relaxed text-muted-foreground">
          بالمتابعة عبر Google، فإنك توافق على{" "}
          <Link
            href={LEGAL_ROUTES.terms}
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-foreground transition-colors"
          >
            الشروط والأحكام
          </Link>{" "}
          و{" "}
          <Link
            href={LEGAL_ROUTES.privacy}
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-foreground transition-colors"
          >
            سياسة الخصوصية
          </Link>
          .
        </p>
      </div>

      {/* Toggle mode */}
      <div className="text-center text-sm text-muted-foreground">
        {mode === "login" ? (
          <>
            ليس لديك حساب؟{" "}
            <button
              type="button"
              onClick={toggleMode}
              className="font-medium text-primary hover:text-primary/80 transition-colors"
            >
              إنشاء حساب جديد
            </button>
          </>
        ) : (
          <>
            لديك حساب بالفعل؟{" "}
            <button
              type="button"
              onClick={toggleMode}
              className="font-medium text-primary hover:text-primary/80 transition-colors"
            >
              تسجيل الدخول
            </button>
          </>
        )}
      </div>
    </form>
  );
}
