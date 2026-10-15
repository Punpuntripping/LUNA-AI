import {
  arNumber,
  arResetDate,
  refusalCardCopy,
  toLatinDigits,
  type RefusalCardCopy,
  type ReferenceUnlockInfo,
} from "@contracts";

import type { ReferenceSourceResult } from "@/api/workspace";

// Reader copy — the same Arabic as the web's lib/library/gate-copy.ts (its
// reveal/balance/error strings; not in contracts because that module also
// carries web-only CTAs). The refusal card itself comes from contracts.

export const readerCopy = {
  loading: "جارٍ فتح المصدر…",
  retry: "إعادة المحاولة",
  dismiss: "حسناً",
  login: "تسجيل الدخول",
  copyContent: "نسخ المحتوى",
  copied: "تم النسخ",
  officialExit: "فتح المصدر الرسمي",
  copyLink: "نسخ الرابط",
  linkCopied: "تم نسخ الرابط",
  share: "مشاركة",
  crossRefs: "الإحالات",
  missingReference: "المرجع غير موجود",
} as const;

export const balanceCopy = {
  exhausted: "لم يتبقَّ رصيد لهذه الفترة",
  remaining: (remaining: number, limit: number): string =>
    `متبقٍ ${arNumber(remaining)} من ${arNumber(limit)} مصدراً هذه الفترة`,
  renewsOn: (resetsAt: string | null): string => {
    const date = arResetDate(resetsAt);
    return date ? `يتجدّد رصيدك في ${date}` : "";
  },
} as const;

const transportErrorCopy: RefusalCardCopy = {
  title: "تعذّر فتح هذا المصدر",
  body: "تحقّق من اتصالك ثم أعد المحاولة.",
};

/** 401/403 — the reader never ejects mid-read; the button is the user's call. */
const staleSessionCopy: RefusalCardCopy = {
  title: "انتهت جلستك",
  body: "سجّل الدخول مرة أخرى لعرض النص كاملاً.",
};

/** 429 — NOT a quota refusal; nothing was charged. */
const rateLimitedCopy: RefusalCardCopy = {
  title: "طلبات كثيرة في وقت قصير",
  body: "حاول بعد قليل — لم يُحتسب أي مصدر من رصيدك.",
};

/** 404, or a reference whose source cannot be built (`has_source` false). */
export const sourceUnavailableCopy: RefusalCardCopy = {
  title: "تعذّر عرض هذا المصدر",
  body: "لا يتوفر نص هذا المصدر حالياً. يمكنك فتح المصدر الرسمي من بطاقة المرجع.",
};

export type FailureAction = "retry" | "login" | "dismiss";

/**
 * A failed reveal → its card copy and the one action it offers. The refusal
 * CTAs are deliberately `null`: the app never links to web pricing, and the
 * in-app purchase screen does not exist yet (📏) — so the card closes with
 * «حسناً».
 */
export function failureCopy(result: Extract<ReferenceSourceResult, { ok: false }>): {
  copy: RefusalCardCopy;
  action: FailureAction;
} {
  if (result.kind === "refusal") {
    const { reason, resets_at, stored_count } = result.refusal;
    const copy = refusalCardCopy({ reason, resetsAt: resets_at, storedCount: stored_count }, { upgrade: null, signup: null });
    return { copy: { title: copy.title, body: copy.body }, action: "dismiss" };
  }
  switch (result.error) {
    case "rate_limited":
      return { copy: rateLimitedCopy, action: "retry" };
    case "unauthorized":
    case "no_token":
      return { copy: staleSessionCopy, action: "login" };
    case "not_found":
      return { copy: sourceUnavailableCopy, action: "dismiss" };
    default:
      return { copy: transportErrorCopy, action: "retry" };
  }
}

/**
 * The quiet line under a revealed source naming WHAT was unlocked (D15.1 —
 * often the whole نظام, not the cited paragraph). "" when there is nothing to
 * say. Mirrors the web's `unlockedNotice`.
 */
export function unlockedNotice(unlocked: ReferenceUnlockInfo): string {
  const name = (unlocked.title ?? "").trim();
  if (unlocked.reason === "open") return "هذا المصدر متاح للجميع — لم يُحتسب من رصيدك.";
  if (unlocked.reason === "already_unlocked") {
    return name ? `«${name}» مفتوح في مكتبتك — لم يُحتسب من رصيدك.` : "هذا المصدر مفتوح في مكتبتك — لم يُحتسب من رصيدك.";
  }
  const cost = unlocked.cost ?? 0;
  const costNote = cost > 1 ? ` (احتُسب ${arNumber(cost)} من رصيدك)` : "";
  // Text, not a number: «81 مكرر» / «1-1» must survive verbatim.
  const article = toLatinDigits((unlocked.article_no ?? "").trim());
  if (article) {
    return name
      ? `تم فتح المادة ${article} من «${name}» وأُضيفت إلى مكتبتك.${costNote}`
      : `تم فتح المادة ${article} وأُضيفت إلى مكتبتك.${costNote}`;
  }
  if (!name) return `تم فتح هذا المصدر وأُضيف إلى مكتبتك.${costNote}`;
  if (unlocked.content_type === "regulation") {
    return `تم فتح «${name}» كاملاً — بجميع مواده — وأُضيف إلى مكتبتك.${costNote}`;
  }
  return `تم فتح «${name}» وأُضيف إلى مكتبتك.${costNote}`;
}
