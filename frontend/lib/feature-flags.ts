/**
 * Device-local feature flags (.claude/plans/email_otp_login.md Phase A).
 *
 * ⚠ VISIBILITY ONLY, never a security boundary. The real gate for email OTP is
 * the server allowlist `EMAIL_OTP_ALLOWED_EMAILS`: a flagged device with a
 * non-allowlisted email gets the same generic 200 and no email.
 *
 * The flag lives in THIS origin's localStorage — the installed iOS app has its
 * own jar, so a tester must open `/login?otp=1` inside the app itself.
 */

const EMAIL_OTP_KEY = "rayhan.ff.email_otp";
const EMAIL_OTP_PARAM = "otp";

/** Is «الدخول برمز عبر البريد» shown on this device? SSR-safe (false). */
export function isEmailOtpEnabled(): boolean {
  try {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(EMAIL_OTP_KEY) === "1";
  } catch {
    // Storage blocked (private mode, disabled cookies) — flag reads as off.
    return false;
  }
}

/**
 * Apply `?otp=1` (set) / `?otp=0` (clear) from the current URL, then strip the
 * parameter so a reload or a shared link does not re-apply it. Every other
 * query parameter (`next`, `u`, `mode`, …) is kept as-is. No-op on the server
 * and when the parameter is absent.
 */
export function applyEmailOtpFlagFromUrl(): void {
  try {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const value = url.searchParams.get(EMAIL_OTP_PARAM);
    if (value === null) return;

    if (value === "1") window.localStorage.setItem(EMAIL_OTP_KEY, "1");
    else if (value === "0") window.localStorage.removeItem(EMAIL_OTP_KEY);

    url.searchParams.delete(EMAIL_OTP_PARAM);
    const query = url.searchParams.toString();
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${query ? `?${query}` : ""}${url.hash}`,
    );
  } catch {
    // Storage or history unavailable — the flag simply stays as it was.
  }
}
