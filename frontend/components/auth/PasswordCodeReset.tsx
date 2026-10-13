"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PasswordInput } from "@/components/ui/password-input";
import {
  OTP_CODE_MIN,
  OTP_RESEND_COOLDOWN_S,
  OtpCodeInput,
  normaliseOtpCode,
} from "@/components/auth/OtpCodeInput";
import { ApiClientError, authApi } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

/**
 * Set / change / reset the password with an emailed code — the ONLY way the web
 * writes a password (owner decision 2026-10-13: email code only, no email link,
 * no current-password form; same method as the mobile app).
 *
 *   1. POST /auth/password/forgot {email}            → a 6-digit code is emailed
 *   2. POST /auth/password/reset {email, code, new_password}
 *                                                    → the same AuthResponse as
 *      /auth/login; `resetPasswordWithCode` turns it into the web session
 *      exactly like a password login (stores/auth-store establishSession).
 *
 * Rendered by /forgot-password (email typed, logged out) and إعدادات الحساب
 * (`fixedEmail` = the signed-in account's own address, not editable). Works for
 * Google-only accounts too — it writes their FIRST password.
 *
 * On success this stays in its "saving" state: the caller either navigates away
 * or remounts it (via `key`), so the idle form never flashes in between.
 */

const emailSchema = z
  .string()
  .trim()
  .min(1, "البريد الإلكتروني مطلوب")
  .email("صيغة البريد الإلكتروني غير صحيحة");

// Same password rule and messages as signup / the old settings forms.
const resetSchema = z
  .object({
    code: z.string().min(OTP_CODE_MIN, "أدخل الرمز المكوّن من 6 أرقام"),
    new_password: z
      .string()
      .min(8, "كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل"),
    confirm_password: z.string().min(1, "تأكيد كلمة المرور مطلوب"),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    message: "كلمتا المرور غير متطابقتين",
    path: ["confirm_password"],
  });

type ResetField = "code" | "new_password" | "confirm_password";

const GENERIC_ERROR = "حدث خطأ غير متوقع. حاول مرة أخرى.";
const NETWORK_ERROR = "تعذّر الاتصال. تحقّق من الشبكة وحاول مجددًا.";

/** The server's Arabic `detail` for an HTTP error; a transport failure (fetch
 *  TypeError — offline, CSP, DNS) gets the network line instead. */
function errorMessage(err: unknown): string {
  if (err instanceof ApiClientError) return err.message || GENERIC_ERROR;
  if (err instanceof TypeError) return NETWORK_ERROR;
  return GENERIC_ERROR;
}

interface PasswordCodeResetProps {
  /** Prefix for element ids and `data-testid`s. */
  idPrefix: string;
  /** The signed-in account's own address — shown read-only, never editable.
   *  Omit on /forgot-password, where the visitor types it. */
  fixedEmail?: string;
  /** Step-1 button label. */
  sendLabel: string;
  /** Step-2 submit label. */
  submitLabel: string;
  /** Extra copy under the step-1 intro (e.g. the Google-account hint). */
  emailStepNote?: ReactNode;
  /** Runs once the new session is live. */
  onSuccess: () => Promise<void> | void;
}

export function PasswordCodeReset({
  idPrefix,
  fixedEmail,
  sendLabel,
  submitLabel,
  emailStepNote,
  onSuccess,
}: PasswordCodeResetProps) {
  const resetPasswordWithCode = useAuthStore((s) => s.resetPasswordWithCode);

  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState(fixedEmail ?? "");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<ResetField, string>>
  >({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
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

  /** POST /auth/password/forgot. 200 says nothing about membership. 429 and
   *  503 carry Arabic `detail`s worth showing as-is. */
  const requestCode = async (target: string): Promise<boolean> => {
    setServerError(null);
    setNotice(null);
    setIsSending(true);
    try {
      await authApi.passwordForgot(target);
      setCooldown(OTP_RESEND_COOLDOWN_S);
      return true;
    } catch (err) {
      // A 429 IS the cooldown — lock the resend button for the same window
      // instead of inviting another rejected click.
      if (err instanceof ApiClientError && err.status === 429) {
        setCooldown(OTP_RESEND_COOLDOWN_S);
      }
      setServerError(errorMessage(err));
      return false;
    } finally {
      setIsSending(false);
    }
  };

  const handleSendSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    let target: string;
    if (fixedEmail) {
      target = fixedEmail;
    } else {
      const parsed = emailSchema.safeParse(email);
      if (!parsed.success) {
        setEmailError(
          parsed.error.issues[0]?.message ?? "صيغة البريد الإلكتروني غير صحيحة",
        );
        return;
      }
      setEmailError(null);
      target = parsed.data.toLowerCase();
      setEmail(target);
    }
    if (await requestCode(target)) {
      setCode("");
      setFieldErrors({});
      setStep("code");
    }
  };

  const handleResend = async () => {
    if (cooldown > 0 || isSending) return;
    setCode("");
    if (await requestCode(email)) {
      setNotice("أرسلنا رمزاً جديداً. استخدم الرمز الأحدث فقط.");
    }
    codeInputRef.current?.focus();
  };

  const handleCodeChange = (raw: string) => {
    setCode(normaliseOtpCode(raw));
    if (fieldErrors.code) setFieldErrors((f) => ({ ...f, code: undefined }));
    if (serverError) setServerError(null);
  };

  const handleResetSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSaving) return;
    setServerError(null);
    setNotice(null);

    const result = resetSchema.safeParse({
      code,
      new_password: newPassword,
      confirm_password: confirmPassword,
    });
    if (!result.success) {
      const errors: Partial<Record<ResetField, string>> = {};
      for (const issue of result.error.issues) {
        const field = issue.path[0] as ResetField;
        if (!errors[field]) errors[field] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }

    setFieldErrors({});
    setIsSaving(true);
    try {
      await resetPasswordWithCode(email, code, newPassword);
      // Stays "saving" — the caller navigates away or remounts this form.
      await onSuccess();
    } catch (err) {
      const status = err instanceof ApiClientError ? err.status : 0;
      if (status === 400) {
        // The password was rejected AFTER the code verified — the server has
        // spent that code, so the same one can never work again.
        setServerError(
          `${errorMessage(err)} — الرمز المستخدم لم يعد صالحاً، أرسل رمزاً جديداً.`,
        );
        setCode("");
      } else {
        setServerError(errorMessage(err));
        // A wrong / expired code is retyped from scratch.
        if (status === 401) setCode("");
      }
      setIsSaving(false);
      if (status === 400 || status === 401) codeInputRef.current?.focus();
    }
  };

  const changeEmail = () => {
    setStep("email");
    setCode("");
    setServerError(null);
    setNotice(null);
    setFieldErrors({});
  };

  const busy = isSending || isSaving;

  const errorBox = serverError && (
    <div
      role="alert"
      className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
      data-testid={`${idPrefix}-error`}
    >
      {serverError}
    </div>
  );

  if (step === "email") {
    return (
      <form
        onSubmit={handleSendSubmit}
        className="flex flex-col gap-4"
        noValidate
        data-testid={`${idPrefix}-email-step`}
      >
        {errorBox}

        {fixedEmail ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm leading-relaxed text-muted-foreground">
              سنرسل رمز تحقق إلى بريدك الإلكتروني:
            </p>
            <p
              className="rounded-md border border-input bg-muted px-3 py-2 text-sm text-foreground"
              dir="ltr"
              data-testid={`${idPrefix}-fixed-email`}
            >
              {fixedEmail}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <label
              htmlFor={`${idPrefix}-email`}
              className="block text-sm font-medium text-foreground"
            >
              البريد الإلكتروني
            </label>
            <input
              id={`${idPrefix}-email`}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="example@email.com"
              autoComplete="email"
              inputMode="email"
              dir="ltr"
              autoFocus
              data-testid={`${idPrefix}-email`}
              // text-base (16px): anything smaller makes iOS zoom on focus.
              className={`min-h-11 w-full rounded-md border bg-background px-3 py-2 text-base text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring ${
                emailError ? "border-destructive" : "border-input"
              }`}
            />
            {emailError && (
              <p className="text-xs text-destructive">{emailError}</p>
            )}
          </div>
        )}

        {emailStepNote}

        <Button
          type="submit"
          disabled={busy}
          className="w-full"
          data-testid={`${idPrefix}-send`}
        >
          {isSending && <Loader2 className="h-4 w-4 animate-spin" />}
          {isSending ? "جارٍ الإرسال…" : sendLabel}
        </Button>
      </form>
    );
  }

  return (
    <form
      onSubmit={handleResetSubmit}
      className="flex flex-col gap-4"
      noValidate
      data-testid={`${idPrefix}-code-step`}
    >
      {errorBox}

      {/* Non-committal on the public page: the same 200 comes back for an
          address with no account. */}
      <p className="text-sm leading-relaxed text-muted-foreground">
        {fixedEmail
          ? "أرسلنا رمز التحقق إلى بريدك"
          : "إذا كان البريد مسجّلاً لدينا، أرسلنا إليه رمز التحقق"}
        <span className="mt-1 block font-medium text-foreground" dir="ltr">
          {email}
        </span>
      </p>

      {/* The sender has little reputation yet; Gmail has filed these as spam
          (first observed 2026-08-18). Without this line a user who never
          checks spam concludes the flow is broken. */}
      <p className="rounded-md bg-muted px-3 py-2 text-sm leading-relaxed text-foreground">
        لم تصلك الرسالة؟ تحقّق من مجلد{" "}
        <strong>الرسائل غير المرغوب فيها (Spam)</strong> — قد تصل إليه أحيانًا.
      </p>

      {notice && (
        <p
          className="text-sm text-success-fg"
          role="status"
          data-testid={`${idPrefix}-notice`}
        >
          {notice}
        </p>
      )}

      <div className="flex flex-col gap-2">
        <label
          htmlFor={`${idPrefix}-code`}
          className="block text-sm font-medium text-foreground"
        >
          رمز التحقق
        </label>
        <OtpCodeInput
          ref={codeInputRef}
          id={`${idPrefix}-code`}
          value={code}
          onChange={handleCodeChange}
          disabled={isSaving}
          describedBy={`${idPrefix}-code-hint`}
          data-testid={`${idPrefix}-code`}
        />
        {fieldErrors.code ? (
          <p className="text-xs text-destructive">{fieldErrors.code}</p>
        ) : (
          <p
            id={`${idPrefix}-code-hint`}
            className="text-xs text-muted-foreground"
          >
            أدخل الرمز المكوّن من 6 أرقام كما وصلك في البريد.
          </p>
        )}
      </div>

      <PasswordInput
        id={`${idPrefix}-new`}
        label="كلمة المرور الجديدة"
        value={newPassword}
        onChange={setNewPassword}
        autoComplete="new-password"
        disabled={isSaving}
        error={fieldErrors.new_password}
        hint="8 أحرف على الأقل"
        data-testid={`${idPrefix}-new`}
      />
      <PasswordInput
        id={`${idPrefix}-confirm`}
        label="تأكيد كلمة المرور الجديدة"
        value={confirmPassword}
        onChange={setConfirmPassword}
        autoComplete="new-password"
        disabled={isSaving}
        error={fieldErrors.confirm_password}
        data-testid={`${idPrefix}-confirm`}
      />

      <Button
        type="submit"
        disabled={busy}
        className="w-full"
        data-testid={`${idPrefix}-submit`}
      >
        {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
        {isSaving ? "جارٍ الحفظ…" : submitLabel}
      </Button>

      <div className="flex items-center justify-between gap-2 text-sm">
        <button
          type="button"
          onClick={handleResend}
          disabled={cooldown > 0 || busy}
          className="inline-flex min-h-11 items-center gap-2 px-1 font-medium text-primary transition-colors hover:text-primary/80 disabled:cursor-not-allowed disabled:text-muted-foreground"
          data-testid={`${idPrefix}-resend`}
        >
          {isSending && <Loader2 className="h-4 w-4 animate-spin" />}
          {cooldown > 0
            ? `إعادة إرسال الرمز خلال ${cooldown} ث`
            : "إعادة إرسال الرمز"}
        </button>
        {!fixedEmail && (
          <button
            type="button"
            onClick={changeEmail}
            disabled={isSaving}
            className="min-h-11 px-1 text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          >
            تغيير البريد
          </button>
        )}
      </div>
    </form>
  );
}
