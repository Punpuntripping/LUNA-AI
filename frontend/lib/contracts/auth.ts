// ==========================================
// USER & AUTH — backend `/api/v1/auth/*` shapes
// (moved from frontend/types/index.ts; the web re-exports them from there)
// ==========================================

export interface User {
  user_id: string;
  email: string;
  full_name_ar?: string | null;
  /** «بماذا تحب أن نناديك؟» (users.preferred_name, migration 122). null = never
   *  answered → إعدادات الحساب prefills with `call_name` instead. */
  preferred_name?: string | null;
  /** What the app actually calls the user: preferred_name, else a first name
   *  derived server-side from full_name_ar, else null when we have no name.
   *  Resolved in one place (shared/identity.py) — never re-derived here. */
  call_name?: string | null;
  /** Legacy column — superseded by plan_id. */
  subscription_tier?: string | null;
  /** Subscription plan (plans table). null = account not activated yet.
   *
   *  ⚠ NOT a paid-ness test. `plan_id !== "free"` is true for dev grants, comps
   *  and long-expired terms alike — use `paid_activated_at` for "this account
   *  actually bought something". */
  plan_id?: string | null;
  /** ISO timestamp of the last time this account activated a paid plan BY
   *  PAYING for it — null for free, dev, comped and expired subscriptions.
   *  Server-resolved from source + plan + a still-running term
   *  (`subscription_service.resolve_paid_activated_at`); absent on the /login
   *  payload, which does not read the subscription at all. Gates the
   *  post-purchase «اتعرف على ريحان» tour. */
  paid_activated_at?: string | null;
  created_at?: string | null;
  /** Account deletion grace period (30 days). True → app is gated behind
   *  AccountDeletionPendingScreen until restored. */
  deletion_pending?: boolean;
  deletion_requested_at?: string | null;
  /** Server-computed purge date (requested + 30 days) — never derived client-side. */
  purge_at?: string | null;
  /** True when the account holds a password credential (migration 141).
   *  Decides «تعيين» vs «تغيير كلمة المرور» in إعدادات الحساب and which branch
   *  DeleteAccountDialog confirms with.
   *
   *  ⚠ Do NOT re-derive this from the Supabase session's `app_metadata.providers`
   *  — that is what this field replaced. Setting a password on a Google account
   *  writes the credential without adding an `email` identity, so the session
   *  still reads ["google"] forever afterwards and the settings dialog would go
   *  on hiding the password form from someone who has one. Server-resolved from
   *  auth.users.encrypted_password; absent/undefined is treated as false. */
  has_password?: boolean;
  /** Onboarding profession segment (users.profession_group, migration 115).
   *  EXACTLY null = never asked → the profession prompt opens. "unknown" is
   *  the server's fail-closed sentinel for degraded reads; "declined" = chose
   *  not to answer; else legal | entrepreneur | specialist | individual. */
  profession_group?: string | null;
  /** Finer segment (chip pick or free-typed «أخرى») — specialist/individual only. */
  profession_label?: string | null;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

// ------------------------------------------
// Request/response bodies (native app — additive)
// ------------------------------------------

/** POST /auth/login */
export interface LoginRequest {
  email: string;
  password: string;
}

/** POST /auth/refresh — answers `AuthTokens` (TokenResponse: no `user`). */
export interface RefreshRequest {
  refresh_token: string;
}

/** POST /auth/otp/request (also the body of POST /auth/signup/resend). */
export interface OtpRequestBody {
  email: string;
}

/** POST /auth/otp/request · POST /auth/signup/resend.
 *  Always `sent: true` for well-formed input (membership is never revealed).
 *  `fallback: "password"` = code login is unavailable for this request — the
 *  client switches to the password form. */
export interface OtpRequestResponse {
  sent: boolean;
  fallback?: "password";
}

/** POST /auth/otp/verify · POST /auth/signup/verify. `code` = digits only
 *  (the backend strips whitespace); answers `AuthResponse`. */
export interface OtpVerifyBody {
  email: string;
  code: string;
}

/** POST /auth/signup — native signup (the web signs up through supabase-js). */
export interface SignupRequest {
  email: string;
  password: string;
  full_name: string;
  /** `LEGAL_VERSION` the user consented to (contracts/legal.ts). */
  terms_version: string;
  /** Promotional email consent — OFF unless the user ticks the box. */
  marketing_opt_in: boolean;
}

/** POST /auth/signup — a 6-digit code was emailed; confirm via /auth/signup/verify. */
export interface SignupVerificationRequired {
  status: "verification_required";
  email: string;
}

/** POST /auth/signup answers either "check your email" or, when email
 *  confirmation is off, a full session. 409 = email already registered. */
export type SignupResponse = SignupVerificationRequired | AuthResponse;

/** Generic `{success: true}` (logout, restore-account, …). */
export interface SuccessResponse {
  success: boolean;
}

/** POST /auth/password/forgot — emails a 6-digit reset code. Always
 *  `sent: true` for well-formed input (membership is never revealed).
 *  429 = cooldown, 503 = the email could not be sent. */
export interface PasswordForgotRequest {
  email: string;
}

export interface PasswordForgotResponse {
  sent: boolean;
}

/** The four profession cards (users.profession_group, migration 115). */
export type ProfessionGroupKey = "legal" | "entrepreneur" | "specialist" | "individual";

/** Every value a client may WRITE to users.profession_group — the backend's
 *  `UpdateProfessionRequest` allow-list. Reading `User.profession_group` stays
 *  `string | null`: it can also be the server's "unknown" sentinel. */
export type ProfessionAnswer = ProfessionGroupKey | "declined";

/** PATCH /auth/profession. The label is kept only for specialist / individual
 *  (the server drops it for the other answers); ≤120 chars, trimmed, empty →
 *  null. An unknown group → 422 «قيمة المهنة غير صحيحة». */
export interface UpdateProfessionRequest {
  profession_group: ProfessionAnswer;
  profession_label: string | null;
}

/** PATCH /auth/profession — echo of the stored answer. */
export interface UpdateProfessionResponse {
  profession_group: string;
  profession_label: string | null;
}

/** POST /auth/password/reset — sets a new password with the emailed code and
 *  answers `AuthResponse` (same as /auth/login). 401 AUTH_INVALID = wrong or
 *  expired code · 429 = too many attempts · 400 = password rejected. Also how
 *  a Google-only account gains a password for the native app. */
export interface PasswordResetRequest {
  email: string;
  code: string;
  new_password: string;
}
