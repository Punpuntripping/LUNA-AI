"use client";

import { useId, useState, type ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The one password field. Every «كلمة المرور» input in the app renders through
 * this — login, register, set, change, reset, and the delete-account
 * confirmation.
 *
 * ⚠ THE DIRECTION TRAP THIS EXISTS TO CLOSE. There were five hand-rolled copies
 * of this markup, and all of them put the reveal toggle on top of the password.
 * A password is Latin, so the input carries `dir="ltr"` — which means Tailwind's
 * `pe-10` (`padding-inline-end`) resolves against the INPUT's own direction and
 * reserves space on the RIGHT. The toggle, though, sat in a wrapper that
 * inherited the page's RTL, so its `end-2` (`inset-inline-end`) resolved to the
 * LEFT. Padding on one side, button on the other: the eye rendered directly over
 * the first characters typed.
 *
 * The fix is the `dir="ltr"` on the WRAPPER below. Both properties then resolve
 * against the same direction and land on the same edge. Keep them on one element
 * tree — do not "simplify" this by moving the toggle out of the LTR wrapper, and
 * do not swap the logical utilities for physical ones only on one of the two.
 * The label and the error message stay outside that wrapper, so they remain RTL
 * Arabic.
 *
 * The masked value is mono with wide tracking: the mask glyph is drawn from the
 * element's font, and the Arabic UI face has no bullet, so the browser fell back
 * to a heavy blob at default spacing. Mono gives an even, restrained dot.
 */
export interface PasswordInputProps {
  /** Visible «كلمة المرور» label. */
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Required — a password field with the wrong autocomplete actively fights
   *  the browser's password manager. `current-password` to sign in with an
   *  existing one, `new-password` to create or change one. */
  autoComplete: "current-password" | "new-password";
  id?: string;
  name?: string;
  error?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  placeholder?: string;
  onKeyDown?: (event: React.KeyboardEvent<HTMLInputElement>) => void;
  /** Rendered at the far end of the label row — «نسيت كلمة المرور؟» on login. */
  labelAction?: ReactNode;
  /** Help text under the field, e.g. the minimum length. Suppressed while an
   *  error is showing so the two never stack. */
  hint?: ReactNode;
  "data-testid"?: string;
}

export function PasswordInput({
  label,
  value,
  onChange,
  autoComplete,
  id,
  name,
  error,
  disabled,
  autoFocus,
  placeholder,
  onKeyDown,
  labelAction,
  hint,
  "data-testid": testId,
}: PasswordInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const [revealed, setRevealed] = useState(false);

  // Only mask-styled while actually masking something: an empty field would
  // otherwise push its placeholder out at 0.25em and read as a bug.
  const masked = !revealed && value.length > 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <label
          htmlFor={inputId}
          className="block text-sm font-medium text-foreground"
        >
          {label}
        </label>
        {labelAction}
      </div>

      {/* dir="ltr" HERE, on the wrapper — see the note above. */}
      <div dir="ltr" className="relative">
        <input
          id={inputId}
          name={name}
          type={revealed ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          disabled={disabled}
          placeholder={placeholder}
          dir="ltr"
          data-testid={testId}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : hint ? hintId : undefined}
          className={cn(
            "h-10 w-full rounded-md border bg-background ps-3 pe-11 text-sm text-foreground",
            "font-mono transition-[border-color,box-shadow,color] duration-150",
            "placeholder:font-sans placeholder:tracking-normal placeholder:text-muted-foreground",
            "focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring",
            "disabled:cursor-not-allowed disabled:opacity-60",
            masked && "tracking-[0.25em]",
            error ? "border-destructive" : "border-input",
          )}
        />
        <button
          type="button"
          onClick={() => setRevealed((prev) => !prev)}
          disabled={disabled}
          // Reachable by keyboard on purpose. The copies this replaces all set
          // tabIndex={-1}, which left the only way to check a typo behind a
          // mouse — the people most likely to mistype are the least likely to
          // have one.
          aria-pressed={revealed}
          aria-controls={inputId}
          aria-label={revealed ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"}
          className={cn(
            "absolute end-1.5 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center",
            "rounded-md text-muted-foreground transition-colors",
            "hover:bg-muted hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "disabled:pointer-events-none disabled:opacity-60",
          )}
        >
          {revealed ? (
            <EyeOff className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Eye className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </div>

      {error ? (
        <p id={errorId} className="text-xs text-destructive">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
