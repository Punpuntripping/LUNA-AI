import type { ApiErrorEnvelope, ErrorCode } from "@contracts";

// The backend already sends Arabic text in `error.message` / `detail`; the app
// shows it as-is (same as the web). These fallbacks cover everything that does
// not carry an Arabic message: FastAPI 422 lists, plain HTTPExceptions, network
// failures, and anything that is not our JSON at all (a Cloudflare page).

export const MSG_NETWORK = "تعذّر الاتصال بالخادم — تحقق من اتصالك";
export const MSG_EDGE = "تعذّر الوصول إلى الخادم";
export const MSG_UNKNOWN = "حدث خطأ غير متوقع، حاول مجدداً";
export const MSG_UPDATE_REQUIRED = "يلزم تحديث التطبيق إلى أحدث إصدار للمتابعة";
export const MSG_SERVICE_UNAVAILABLE = "الخدمة غير متاحة مؤقتاً، حاول مجدداً";

const STATUS_FALLBACK: Record<number, string> = {
  400: "تعذّر تنفيذ الطلب",
  401: "انتهت الجلسة، سجّل الدخول من جديد",
  403: "لا تملك صلاحية لتنفيذ هذا الإجراء",
  404: "العنصر المطلوب غير موجود",
  409: "تعارض مع بيانات موجودة",
  422: "البيانات المدخلة غير صحيحة",
  426: MSG_UPDATE_REQUIRED,
  429: "تم تجاوز الحد المسموح من الطلبات",
};

/** `http` = the backend answered with an error status · `network` = no answer
 *  (offline, DNS, timeout) · `edge` = something that is not our API answered
 *  (Cloudflare challenge / HTML page). */
export type ApiErrorKind = "http" | "network" | "edge";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly code: ErrorCode | "UNKNOWN";
  readonly requestId: string | null;

  constructor(opts: {
    kind: ApiErrorKind;
    status: number;
    code?: ErrorCode | "UNKNOWN";
    message: string;
    requestId?: string | null;
  }) {
    super(opts.message);
    this.name = "ApiError";
    this.kind = opts.kind;
    this.status = opts.status;
    this.code = opts.code ?? "UNKNOWN";
    this.requestId = opts.requestId ?? null;
  }
}

const ARABIC_LETTER = /[؀-ۿ]/;

function arabicOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() && ARABIC_LETTER.test(value) ? value.trim() : null;
}

export function fallbackForStatus(status: number): string {
  if (status >= 500) return MSG_SERVICE_UNAVAILABLE;
  return STATUS_FALLBACK[status] ?? MSG_UNKNOWN;
}

/** Build an ApiError from a parsed error body (either envelope shape). */
export function errorFromBody(status: number, body: unknown, requestId: string | null): ApiError {
  const env = (body && typeof body === "object" ? body : {}) as ApiErrorEnvelope;
  const code = (env.error?.code as ErrorCode | undefined) ?? undefined;
  // A list-shaped `detail` (FastAPI 422) is never shown — only Arabic strings.
  const message =
    arabicOrNull(env.error?.message) ?? arabicOrNull(env.detail) ?? fallbackForStatus(status);
  return new ApiError({ kind: "http", status, code, message, requestId });
}

/** The Arabic text to show for ANY thrown value. Never English, never raw. */
export function errorMessage(err: unknown, fallback: string = MSG_UNKNOWN): string {
  if (err instanceof ApiError) return err.message;
  return fallback;
}
