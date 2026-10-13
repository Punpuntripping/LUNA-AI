import type {
  AuthResponse,
  LoginRequest,
  OtpRequestResponse,
  OtpVerifyBody,
  PasswordForgotResponse,
  PasswordResetRequest,
  SignupRequest,
  SignupResponse,
  SuccessResponse,
  User,
} from "@contracts";

import { apiFetch } from "./client";
import { request } from "./transport";

// Typed wrappers for /api/v1/auth/*. Pre-login routes go out without a token.

export function login(body: LoginRequest): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/auth/login", { method: "POST", body, auth: false });
}

export function requestOtp(email: string): Promise<OtpRequestResponse> {
  return apiFetch<OtpRequestResponse>("/auth/otp/request", { method: "POST", body: { email }, auth: false });
}

export function verifyOtp(body: OtpVerifyBody): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/auth/otp/verify", { method: "POST", body, auth: false });
}

export function signup(body: SignupRequest): Promise<SignupResponse> {
  return apiFetch<SignupResponse>("/auth/signup", { method: "POST", body, auth: false });
}

export function verifySignup(body: OtpVerifyBody): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/auth/signup/verify", { method: "POST", body, auth: false });
}

export function resendSignup(email: string): Promise<OtpRequestResponse> {
  return apiFetch<OtpRequestResponse>("/auth/signup/resend", { method: "POST", body: { email }, auth: false });
}

export function forgotPassword(email: string): Promise<PasswordForgotResponse> {
  return apiFetch<PasswordForgotResponse>("/auth/password/forgot", { method: "POST", body: { email }, auth: false });
}

export function resetPassword(body: PasswordResetRequest): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/auth/password/reset", { method: "POST", body, auth: false });
}

export function fetchMe(): Promise<User> {
  return apiFetch<User>("/auth/me");
}

export function restoreAccount(): Promise<SuccessResponse> {
  return apiFetch<SuccessResponse>("/auth/restore-account", { method: "POST" });
}

/** Best-effort server logout with the CURRENT token — never refreshes first
 *  (logging out must not rotate a token it is about to discard). */
export function logout(token: string): Promise<SuccessResponse> {
  return request<SuccessResponse>("/auth/logout", { method: "POST", token, timeoutMs: 5_000 });
}

/** `SignupResponse` carries a session only when email confirmation is off. */
export function isAuthResponse(res: SignupResponse): res is AuthResponse {
  return "access_token" in res;
}
