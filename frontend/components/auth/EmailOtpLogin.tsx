"use client";

import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { ApiClientError, authApi } from "@/lib/api";
import { toLatinDigits } from "@/lib/format/numerals";
import { track } from "@/lib/analytics/client";

// «الدخول برمز عبر البريد» (.claude/plans/email_otp_login.md Phase A).
//
// Why a code and not a link: in the installed iOS app a link opened from Mail
// lands in Safari — a separate storage jar — so the session would end up in the
// wrong place. A code is typed INTO the app; nothing leaves this context.
//
// Rendered by LoginForm in place of the password card, only when the device
// flag is on (lib/feature-flags). The flag is visibility only; the server
// allowlist decides whether an email is ever sent.

const emailSchema = z
  .string()
  .trim()
  .min(1, "البريد الإلكتروني مطلوب")
  .email("صيغة البريد الإلكتروني غير صحيحة");

/** GoTrue's configured OTP length is 6; accept up to 10 in case it is raised. */
const CODE_MIN = 6;
const CODE_MAX = 10;
const RESEND_COOLDOWN_S = 60;

type FailReason = "invalid" | "rate_limited" | "error";

/** Arabic-Indic → Latin, then digits only, capped. Paste-safe ("123 456"). */
function normaliseCode(raw: string): string {
  return toLatinDigits(raw).replace(/\D/g, "").slice(0, CODE_MAX);
}

function failReason(err: unknown): FailReason {
  if (err instanceof ApiClientError) {
    if (err.status === 429) return "rate_limited";
    if (err.status === 401) return "invalid";
  }
  return "error";
}

/** The server's Arabic `detail` when there is one; a generic line otherwise. */
function errorMessage(err: unknown): string {
  if (err instanceof ApiClientError && err.message) return err.message;
  return "حدث خطأ غير متوقع. حاول مرة أخرى.";
}

interface EmailOtpLoginProps {
  /** Pre-fills step 1 with whatever was typed in the password card. */
  initialEmail?: string;
  /** Runs after the session is live — LoginForm's own landing logic. */
  onSuccess: () => Promise<void>;
  /** «الدخول بكلمة المرور» — back to the password card. */
  onBack: () => void;
}

export function EmailOtpLogin({
  initialEmail = "",
  onSuccess,
  onBack,
}: EmailOtpLoginProps) {
  const loginWithOtp = useAuthStore((s) => s.loginWithOtp);

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState(initialEmail);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [serverError, setServerError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isVerifying, setIsVerifying] = useState(false);
  // Seconds until «إعادة الإرسال» unlocks. Mirrors the server's 1/60s limit so
  // the button never offers a request that would only come back as a 429.
  const [cooldown, setCooldown] = useState(0);

  const codeInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = window.setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => window.clearTimeout(id);
  }, [cooldown]);

  // Focus the code field on arrival so iOS shows the Mail suggestion at once.
  useEffect(() => {
    if (step === "code") codeInputRef.current?.focus();
  }, [step]);

  /** POST /auth/otp/request. A 200 says nothing about the email — always
   *  advance, never hint at whether the address is registered. */
  const requestCode = async (target: string): Promise<boolean> => {
    setServerError(null);
    setIsSending(true);
    try {
      await authApi.otpRequest(target);
      track("otp_requested");
      setCooldown(RESEND_COOLDOWN_S);
      return true;
    } catch (err) {
      track("otp_failed", { reason: failReason(err), stage: "request" });
      setServerError(errorMessage(err));
      return false;
    } finally {
      setIsSending(false);
    }
  };

  const handleSendSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const parsed = emailSchema.safeParse(email);
    if (!parsed.success) {
      setEmailError(parsed.error.issues[0]?.message ?? "صيغة البريد الإلكتروني غير صحيحة");
      return;
    }
    setEmailError(null);
    const normalised = parsed.data.toLowerCase();
    setEmail(normalised);
    if (await requestCode(normalised)) {
      setCode("");
      setStep("code");
    }
  };

  const verify = async (value: string) => {
    if (isVerifying || value.length < CODE_MIN) return;
    setServerError(null);
    setIsVerifying(true);
    try {
      await loginWithOtp(email, value);
      track("otp_verified");
      // Stays "verifying" through the navigation — no flash of the idle form.
      await onSuccess();
    } catch (err) {
      const reason = failReason(err);
      track("otp_failed", { reason, stage: "verify" });
      setServerError(errorMessage(err));
      // A wrong code is retyped from scratch; clear it so auto-submit re-arms.
      if (reason === "invalid") setCode("");
      setIsVerifying(false);
      codeInputRef.current?.focus();
    }
  };

  const handleCodeChange = (raw: string) => {
    const next = normaliseCode(raw);
    const prevLength = code.length;
    setCode(next);
    if (serverError) setServerError(null);
    // Auto-submit on the 6th digit only (the transition, not every render), so
    // a longer code can still be finished and sent with «تأكيد».
    if (next.length === CODE_MIN && prevLength < CODE_MIN) void verify(next);
  };

  const handleCodeSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void verify(code);
  };

  const handleResend = async () => {
    if (cooldown > 0 || isSending) return;
    setCode("");
    await requestCode(email);
    codeInputRef.current?.focus();
  };

  const changeEmail = () => {
    setStep("email");
    setCode("");
    setServerError(null);
  };

  const busy = isSending || isVerifying;

  return (
    <div className="space-y-6" data-testid="email-otp-login">
      <div className="rounded-lg border border-border bg-card p-6 space-y-4">
        {serverError && (
          <div
            role="alert"
            className="rounded-md bg-destructive/10 border border-destructive/20 p-3 text-sm text-destructive"
          >
            {serverError}
          </div>
        )}

        {step === "email" ? (
          <form onSubmit={handleSendSubmit} className="space-y-4" noValidate>
            <p className="text-sm leading-relaxed text-muted-foreground">
              أدخل بريدك الإلكتروني وسنرسل إليه رمز دخول صالحاً لمدة قصيرة.
            </p>
            <div className="space-y-2">
              <label
                htmlFor="otp_email"
                className="block text-sm font-medium text-foreground"
              >
                البريد الإلكتروني
              </label>
              <input
                id="otp_email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="example@email.com"
                // text-base (16px): anything smaller makes iOS zoom on focus.
                className={`w-full min-h-11 rounded-md border bg-background px-3 py-2 text-base text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent transition-colors ${
                  emailError ? "border-destructive" : "border-input"
                }`}
                dir="ltr"
                autoComplete="email"
                inputMode="email"
                autoFocus
              />
              {emailError && (
                <p className="text-xs text-destructive">{emailError}</p>
              )}
            </div>

            <button
              type="submit"
              disabled={busy}
              className="w-full min-h-11 flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isSending && <Loader2 className="h-4 w-4 animate-spin" />}
              أرسل الرمز
            </button>
          </form>
        ) : (
          <form onSubmit={handleCodeSubmit} className="space-y-4" noValidate>
            {/* Deliberately non-committal: the same 200 comes back for an
                address we will never email (not allowlisted / unknown). */}
            <p className="text-sm leading-relaxed text-muted-foreground">
              إذا كان البريد مسجّلاً لدينا، أرسلنا إليه رمز الدخول
              <span className="mt-1 block font-medium text-foreground" dir="ltr">
                {email}
              </span>
            </p>

            <div className="space-y-2">
              <label
                htmlFor="otp_code"
                className="block text-sm font-medium text-foreground"
              >
                رمز الدخول
              </label>
              {/* ONE input, not N boxes: iOS offers the code from Mail above
                  the keyboard only to a single one-time-code field. */}
              <input
                ref={codeInputRef}
                id="otp_code"
                type="text"
                value={code}
                onChange={(e) => handleCodeChange(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={CODE_MAX}
                dir="ltr"
                disabled={isVerifying}
                placeholder="------"
                aria-describedby="otp_code_hint"
                data-testid="otp-code-input"
                className="w-full min-h-14 rounded-md border border-input bg-background px-3 py-2 text-center font-mono text-2xl font-semibold tracking-[0.5em] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-60 transition-colors"
              />
              <p id="otp_code_hint" className="text-xs text-muted-foreground">
                أدخل الرمز المكوّن من 6 أرقام كما وصلك في البريد.
              </p>
            </div>

            <button
              type="submit"
              disabled={busy || code.length < CODE_MIN}
              className="w-full min-h-11 flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isVerifying && <Loader2 className="h-4 w-4 animate-spin" />}
              تأكيد
            </button>

            <div className="flex items-center justify-between gap-2 text-sm">
              <button
                type="button"
                onClick={handleResend}
                disabled={cooldown > 0 || busy}
                className="min-h-11 inline-flex items-center gap-2 px-1 font-medium text-primary hover:text-primary/80 disabled:text-muted-foreground disabled:cursor-not-allowed transition-colors"
              >
                {isSending && <Loader2 className="h-4 w-4 animate-spin" />}
                {cooldown > 0
                  ? `إعادة الإرسال خلال ${cooldown} ث`
                  : "إعادة الإرسال"}
              </button>
              <button
                type="button"
                onClick={changeEmail}
                disabled={isVerifying}
                className="min-h-11 px-1 text-muted-foreground hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                تغيير البريد
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="text-center text-sm">
        <button
          type="button"
          onClick={onBack}
          disabled={isVerifying}
          className="min-h-11 px-2 font-medium text-primary hover:text-primary/80 disabled:opacity-50 transition-colors"
          data-testid="password-login-link"
        >
          الدخول بكلمة المرور
        </button>
      </div>
    </div>
  );
}
