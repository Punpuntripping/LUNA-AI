"use client";

import { forwardRef } from "react";
import { toLatinDigits } from "@/lib/format/numerals";

/**
 * The one emailed-code field — «الدخول برمز عبر البريد» (EmailOtpLogin) and
 * every password set / change / reset (PasswordCodeReset) render through it.
 *
 * ONE input, not N boxes: iOS offers the code from Mail above the keyboard only
 * to a single `one-time-code` field. Numeric keypad, paste-safe (normalised by
 * the caller through `normaliseOtpCode`), Latin digits, LTR.
 */

/** GoTrue's configured OTP length is 6; the backend accepts up to 10. */
export const OTP_CODE_MIN = 6;
export const OTP_CODE_MAX = 10;
/** Mirrors the server's 1-per-60 s send limit, so «إعادة الإرسال» never offers
 *  a request that would only come back as a 429. */
export const OTP_RESEND_COOLDOWN_S = 60;

/** Arabic-Indic → Latin, then digits only, capped. Paste-safe ("123 456"). */
export function normaliseOtpCode(raw: string): string {
  return toLatinDigits(raw).replace(/\D/g, "").slice(0, OTP_CODE_MAX);
}

interface OtpCodeInputProps {
  id: string;
  value: string;
  /** Receives the RAW value — normalise with `normaliseOtpCode`. */
  onChange: (raw: string) => void;
  disabled?: boolean;
  describedBy?: string;
  autoFocus?: boolean;
  "data-testid"?: string;
}

export const OtpCodeInput = forwardRef<HTMLInputElement, OtpCodeInputProps>(
  function OtpCodeInput(
    {
      id,
      value,
      onChange,
      disabled,
      describedBy,
      autoFocus,
      "data-testid": testId,
    },
    ref,
  ) {
    return (
      <input
        ref={ref}
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={OTP_CODE_MAX}
        dir="ltr"
        disabled={disabled}
        autoFocus={autoFocus}
        placeholder="------"
        aria-describedby={describedBy}
        data-testid={testId}
        className="w-full min-h-14 rounded-md border border-input bg-background px-3 py-2 text-center font-mono text-2xl font-semibold tracking-[0.5em] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-60 transition-colors"
      />
    );
  },
);
