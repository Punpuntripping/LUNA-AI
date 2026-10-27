/**
 * Machine-readable error codes returned by the FastAPI backend.
 *
 * Mirror of `backend/app/errors.py` `ErrorCode` — keep the two in sync. Every
 * structured error arrives as
 *
 *   { "error": { "code": ErrorCode, "message": "<Arabic>", "status": 4xx }, "detail": "<Arabic>" }
 *
 * The Arabic `message` is what the UI shows; `code` is only for branching.
 * Some routes still raise a plain FastAPI `HTTPException` (`{"detail": ...}`,
 * no code), and 422 validation errors carry a list-shaped `detail` — clients
 * must handle both shapes.
 */
export type ErrorCode =
  // Auth
  | "AUTH_INVALID"
  | "AUTH_EXPIRED"
  | "FORBIDDEN"
  // Account lifecycle
  | "ACCOUNT_DELETION_PENDING"
  // Cases
  | "CASE_NOT_FOUND"
  | "CASE_INVALID_TYPE"
  | "CASE_INVALID_STATUS"
  | "CASE_INVALID_PRIORITY"
  // Conversations
  | "CONV_NOT_FOUND"
  | "CONV_ACCESS_DENIED"
  // Documents
  | "DOC_NOT_FOUND"
  | "DOC_TOO_LARGE"
  | "DOC_INVALID_TYPE"
  | "DOC_EMPTY"
  | "DOC_MAGIC_MISMATCH"
  | "DOC_UPLOAD_FAILED"
  // Resumable upload sessions
  | "UPLOAD_NOT_COMPLETE"
  | "UPLOAD_SIZE_MISMATCH"
  | "UPLOAD_INVALID_STATE"
  // Memories
  | "MEMORY_NOT_FOUND"
  | "MEMORY_INVALID_TYPE"
  // Messages
  | "MSG_SEND_FAILED"
  | "MSG_LIST_FAILED"
  // Artifacts
  | "ARTIFACT_NOT_FOUND"
  | "ARTIFACT_NOT_EDITABLE"
  // Blog-post generation jobs
  | "BLOG_JOB_NOT_FOUND"
  // Preferences
  | "PREFERENCES_FAILED"
  // Templates
  | "TEMPLATE_NOT_FOUND"
  | "TEMPLATE_FAILED"
  | "TEMPLATE_READ_ONLY"
  // User
  | "USER_NOT_FOUND"
  // Plan activation codes
  | "CODE_INVALID"
  | "CODE_ALREADY_REDEEMED"
  | "PLAN_ALREADY_ACTIVE"
  | "REDEEM_LOCKED"
  // Payments (web checkout only — never surfaced by the native app)
  | "PAYMENT_PLAN_NOT_PURCHASABLE"
  | "PAYMENT_DOWNGRADE_BLOCKED"
  | "PAYMENT_NOT_FOUND"
  | "PAYMENT_REFUND_WINDOW_CLOSED"
  | "PAYMENT_NOT_REFUNDABLE"
  | "PAYMENT_PROVIDER_ERROR"
  | "PAYMENT_PRICING_UNAVAILABLE"
  | "PAYMENT_CONSENT_INVALID"
  // Subscription lifecycle
  | "SUBSCRIPTION_NOT_CANCELLABLE"
  | "SUBSCRIPTION_ALREADY_CANCELLED"
  // Library entitlement (HTTP 402)
  | "LIBRARY_QUOTA_EXCEEDED"
  | "LIBRARY_FROZEN"
  | "LIBRARY_ANONYMOUS"
  | "LIBRARY_UNRESOLVABLE"
  // Validation
  | "VALIDATION_ERROR"
  | "NO_UPDATE_DATA"
  | "INVALID_UUID"
  // Rate limiting
  | "RATE_LIMITED"
  // Service availability
  | "SERVICE_UNAVAILABLE"
  // Native app below the minimum supported version — HTTP 426. Only ever sent
  // to requests that carry `X-App-Platform: ios|android`.
  | "APP_UPDATE_REQUIRED"
  // Generic
  | "INTERNAL_ERROR";

/** The structured error envelope (`backend/app/errors.py` luna_exception_handler). */
export interface ApiErrorEnvelope {
  error?: {
    code?: ErrorCode | string;
    message?: string;
    status?: number;
  };
  /** Arabic string for structured errors; a list of issues for FastAPI 422s. */
  detail?: unknown;
}
