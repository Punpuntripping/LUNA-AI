// EVERY Arabic string the library gate renders lives HERE, in ONE file (D10), so
// the copy can be reviewed and rewritten in a single pass. Nothing under
// `components/library/` may hardcode gate copy — import it from this module.
//
// FRAMING RULE (plan §1.2): «Gating must read as a curated feature, never a
// paywall slap.» So: no scolding, no blame, no destructive/red tone, and never
// a dead end — the official source URL is in the never-gated class and stays on
// the page through every refusal, so a refused reader always has somewhere to go.
//
// Numbers are rendered with Arabic-Indic digits (ar-EG), matching the usage
// dialog. Dates are Gregorian rendered in Arabic (`ar-SA` + `calendar: gregory`)
// — the library period boundary is a UTC calendar/subscription instant, and a
// Hijri rendering of it would read as a different date than the one the plan
// pages quote.

import type { LibraryRefusalReason } from "@/lib/library/full-content";

// ------------------------------------------------------------------
// Formatters
// ------------------------------------------------------------------

/** Arabic-Indic digits, no grouping surprises. */
export function arNumber(value: number): string {
  return Math.round(value).toLocaleString("ar-EG");
}

/**
 * An ISO instant → «١ أغسطس ٢٠٢٦», or "" when absent/unparsable. Callers must
 * treat "" as "no date to show" and fall back to the date-free copy variant.
 */
export function arResetDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  try {
    return new Intl.DateTimeFormat("ar-SA", {
      day: "numeric",
      month: "long",
      year: "numeric",
      calendar: "gregory",
    }).format(new Date(ms));
  } catch {
    return "";
  }
}

/** «قسم إضافي واحد» / «قسمان إضافيان» / «٥ أقسام إضافية» — gated-section count. */
export function arSections(count: number): string {
  if (count === 1) return "قسم إضافي واحد";
  if (count === 2) return "قسمان إضافيان";
  if (count >= 3 && count <= 10) return `${arNumber(count)} أقسام إضافية`;
  return `${arNumber(count)} قسماً إضافياً`;
}

/** «صفحة واحدة» / «صفحتان» / «٣ صفحات» — the hub depth cap, read naturally. */
export function arPages(count: number): string {
  if (count === 1) return "صفحة واحدة";
  if (count === 2) return "صفحتان";
  if (count >= 3 && count <= 10) return `${arNumber(count)} صفحات`;
  return `${arNumber(count)} صفحة`;
}

// ------------------------------------------------------------------
// The reveal action (§5.1 — the click IS the consent; there is no dialog)
// ------------------------------------------------------------------

export const revealCopy = {
  /** Signed-in reader: one click spends one unlock and swaps in the full text. */
  authedCta: "اعرض النص كاملاً",
  /** Anonymous reader: goes to /login, never touches the API. */
  anonCta: "سجّل مجاناً لعرض النص كاملاً",
  loadingCta: "جارٍ الفتح…",
  /**
   * The sub-line under the anonymous CTA. When the page knows how many sections
   * are entirely behind the gate, say so — a concrete number converts far better
   * than a generic promise.
   *
   * ⚠ It does NOT quote the monthly allowance any more (user, 2026-08-01). The
   * sub-line sits under a CTA about THIS document; naming a ten-a-month cap there
   * answers a question nobody asked and sets a ceiling before the reader has seen
   * a single مصدر. The allowance still shows where it belongs — the balance chip
   * and إعدادات → حدود الاستخدام, for readers who already have an account.
   */
  anonHint: (hiddenSections?: number): string =>
    hiddenSections && hiddenSections > 0
      ? `${arSections(hiddenSections)} بانتظارك — افتح حسابك المجاني واعرض المصدر`
      : "افتح حسابك المجاني واعرض المصدر",
  authedHint: "يُحتسب المصدر مرة واحدة فقط — العودة إليه لاحقاً مجانية.",
  retryCta: "إعادة المحاولة",
} as const;

/**
 * The شرح variant of the same action.
 *
 * On a مادة of an OPEN نظام the نص is already whole on the page — only the AI شرح
 * sits behind the gate (it is Rayhan's own value-add, gated independently of the
 * article's tier). «اعرض النص كاملاً» there would promise the reader exactly what
 * they are already reading, which is the one thing a gate CTA must never do.
 *
 * Same shape as `revealCopy` so `RevealPanel` can swap the whole object.
 */
export const sharhRevealCopy = {
  authedCta: "اعرض الشرح كاملاً",
  anonCta: "سجّل مجاناً لعرض الشرح كاملاً",
  loadingCta: "جارٍ الفتح…",
  // «الشرح», not «المصدر» — this gate opens the شرح, and the sub-line has to name
  // what the click actually buys, same rule as the CTA above it.
  anonHint: (_hiddenSections?: number): string =>
    "شرح مبسّط موثّق من ريحان — افتح حسابك المجاني واعرض الشرح",
  authedHint: revealCopy.authedHint,
  retryCta: revealCopy.retryCta,
} as const;

/** What a given reveal action actually buys — picks the CTA wording. */
export type RevealTarget = "content" | "sharh";

export function revealCopyFor(target: RevealTarget) {
  return target === "sharh" ? sharhRevealCopy : revealCopy;
}

// ------------------------------------------------------------------
// The passive balance chip — «no prompt, but never a silent meter» (§5.1)
// ------------------------------------------------------------------

export const balanceCopy = {
  /** `limit === null` → the plan has no cap on library unlocks. */
  unlimited: "فتح غير محدود",
  exhausted: "لم يتبقَّ رصيد لهذه الفترة",
  remaining: (remaining: number, limit: number): string =>
    `متبقٍ ${arNumber(remaining)} من ${arNumber(limit)} مصدراً هذه الفترة`,
  /** Appended when we know the period boundary (we always do — see D8). */
  renewsOn: (resetsAt: string | null): string => {
    const date = arResetDate(resetsAt);
    return date ? `يتجدّد رصيدك في ${date}` : "";
  },
} as const;

// ------------------------------------------------------------------
// Refusal cards — one per `reason` on the D14 402 payload
// ------------------------------------------------------------------

export interface RefusalCardCopy {
  title: string;
  body: string;
  /** Omitted when there is nothing useful to click. */
  ctaLabel?: string;
  ctaHref?: string;
}

export interface RefusalCopyInput {
  reason: LibraryRefusalReason;
  resetsAt: string | null;
  /** Shelf size — only meaningful for `frozen_library` (§5B.4). */
  storedCount: number | null;
}

const PRICING_CTA = { ctaLabel: "عرض الباقات", ctaHref: "/pricing" } as const;

/**
 * The Arabic card for a refused reveal. Never blames the reader: an exhausted
 * period is a plan fact, an unresolvable item is our problem, and a frozen
 * shelf is framed as an asset the reader already owns (§5B.4 — the strongest
 * upgrade prompt in the product).
 */
export function refusalCardCopy({
  reason,
  resetsAt,
  storedCount,
}: RefusalCopyInput): RefusalCardCopy {
  const date = arResetDate(resetsAt);

  switch (reason) {
    case "quota_exhausted":
      return {
        title: "رصيد فتح المصادر لهذه الفترة انتهى",
        body: date
          ? `يتجدّد رصيدك في ${date}. يمكنك الترقية للوصول إلى عدد أكبر من المصادر الكاملة.`
          : "يمكنك الترقية للوصول إلى عدد أكبر من المصادر الكاملة.",
        ...PRICING_CTA,
      };

    case "frozen_library":
      return {
        title: "مصادر مكتبتك محفوظة",
        body:
          storedCount && storedCount > 0
            ? `لديك ${arNumber(storedCount)} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد.`
            : "هذا المصدر محفوظ في مكتبتك — رقِّ باقتك لفتحه من جديد.",
        ...PRICING_CTA,
      };

    case "anonymous":
      return {
        title: "سجّل مجاناً لعرض النص كاملاً",
        body: "حساب ريحان المجاني يفتح لك عشرة مصادر كاملة كل شهر.",
        ctaLabel: "إنشاء حساب مجاني",
        ctaHref: "/login",
      };

    case "locked":
      // Matches the UsageLimitsDialog activation notice, word for word.
      return {
        title: "حسابك غير مفعّل بعد",
        body: "تواصل معنا لتفعيل اشتراكك والبدء في استخدام ريحان.",
        ...PRICING_CTA,
      };

    case "unresolvable":
    default:
      // Never blame the reader, and never imply the item is missing.
      return {
        title: "تعذّر فتح هذا المصدر",
        body: "حاول مرة أخرى بعد قليل، أو افتح المصدر الرسمي أدناه.",
      };
  }
}

/** A transport failure (network / 5xx) — distinct from an entitlement refusal. */
export const transportErrorCopy: RefusalCardCopy = {
  title: "تعذّر فتح هذا المصدر",
  body: "تحقّق من اتصالك ثم أعد المحاولة.",
};

/** A dead session (401/403) on a PUBLIC page — never a forced redirect. */
export const staleSessionCopy: RefusalCardCopy = {
  title: "انتهت جلستك",
  body: "سجّل الدخول مرة أخرى لعرض النص كاملاً.",
  ctaLabel: "تسجيل الدخول",
  ctaHref: "/login",
};

// ------------------------------------------------------------------
// Hub browse-depth wall (§5, D12)
// ------------------------------------------------------------------

export const hubWallCopy = {
  /** Anonymous — the cap is 1 page, so the wall is the signup surface. */
  // ⚠ Say only what a free account actually buys. This used to promise «المكتبة
  // كاملة … دون حدود على التصفّح», which a free plan does not deliver: it caps at
  // three hub pages per section and ten source unlocks a month. A wall is a
  // promise made at the moment of signup — it has to survive contact with the
  // quota the user meets ten minutes later.
  anon: {
    title: "تصفّح المكتبة — سجّل مجاناً",
    body: "أنشئ حسابك المجاني للوصول إلى الأنظمة والقوانين والأحكام والخدمات.",
    ctaLabel: "سجّل مجاناً",
    ctaHref: "/login",
  },
  /**
   * Signed-in but past the free cap — an upgrade surface, not a signup one.
   * `maxPage` >= 999 is the wire's "unbounded" sentinel (D16.3 trap 8); a caller
   * with an unbounded cap can never actually reach this wall, so the count is
   * dropped rather than rendered as «٩٬٩٩٩ صفحة».
   */
  upgrade: (maxPage: number) => ({
    title: "تصفّح بلا حدود مع باقة مدفوعة",
    body:
      maxPage > 0 && maxPage < 999
        ? `باقتك الحالية تتيح ${arPages(maxPage)} من كل قسم. رقِّ باقتك لتصفّح المكتبة كاملة دون حدود.`
        : "رقِّ باقتك لتصفّح المكتبة كاملة دون حدود على عدد الصفحات.",
    ctaLabel: "عرض الباقات",
    ctaHref: "/pricing",
  }),
  loading: "جارٍ تحميل الصفحة…",
  error: "تعذّر تحميل هذه الصفحة. حاول مرة أخرى.",
} as const;

// ------------------------------------------------------------------
// Settings → حدود الاستخدام (the third bar)
// ------------------------------------------------------------------

export const usageBarCopy = {
  sectionTitle: "فتح المصادر (المكتبة)",
  barLabel: "هذه الفترة",
  unit: "مصدر",
  note: "يُحتسب فتح كل مصدر من المكتبة مرة واحدة فقط — العودة إليه لاحقاً مجانية.",
} as const;

// ------------------------------------------------------------------
// The in-chat reference reveal (Phase C, §6.2) — same meter, second surface
// ------------------------------------------------------------------
// A citation's `[n]` and «عرض المصدر» now fetch the body on demand from
// `GET /workspace/{item_id}/references/{n}/source`. Everything a refused reveal
// renders is already above (`refusalCardCopy`, `staleSessionCopy`,
// `transportErrorCopy`, `balanceCopy`) — this block only adds what the chat
// surface needs and the library page did not.

export const referenceRevealCopy = {
  /** The card action. Unchanged wording — only its behaviour moved. */
  cta: "عرض المصدر",
  /** Dialog title while the body is in flight. */
  loading: "جارٍ فتح المصدر…",
  /** Screen-reader/idle label for the dialog before anything is known. */
  dialogFallbackTitle: "المصدر",
} as const;

/**
 * The 429 card. This is NOT a quota refusal and must never read like one: the
 * reader has plenty of allowance left, they simply asked too fast (one 20/min
 * budget is shared with `/library/full/*`). Saying so — and saying the rate
 * limiter charged them nothing — is the difference between "slow down" and
 * "you have been cut off".
 */
export const rateLimitedCopy: RefusalCardCopy = {
  title: "طلبات كثيرة في وقت قصير",
  body: "حاول بعد قليل — لم يُحتسب أي مصدر من رصيدك.",
};

/**
 * A 404 on the reveal path: the reference row is gone, or the corpus has no
 * body to build. Never blames the reader, and points at the never-gated
 * official link that is still sitting on the card behind the dialog.
 */
export const sourceUnavailableCopy: RefusalCardCopy = {
  title: "تعذّر عرض هذا المصدر",
  // Names the card's button verbatim («فتح المصدر الرسمي») — copy that points at
  // an affordance must use the affordance's own words.
  body: "لا يتوفر نص هذا المصدر حالياً. يمكنك فتح المصدر الرسمي من بطاقة المرجع.",
};

export interface UnlockedNoticeInput {
  /** `unlocked.title` — WHAT was unlocked, not what was clicked (D15.1). */
  title: string;
  /** Non-null only when the cited chunk owned exactly one مادة. */
  articleNo: number | null;
  /** `unlocked.content_type` — drives the «بجميع مواده» clause. */
  contentType: string;
  /** Weighted cost (§1.2.1). Surfaced only when it is more than one. */
  cost: number;
  reason: "granted" | "already_unlocked" | "open";
}

/**
 * The quiet confirmation under a revealed source — D15.1's whole point.
 *
 * ~81% of `reg:` citations resolve to the WHOLE نظام, because only 2,140 of
 * 11,455 chunks own exactly one مادة. The unlock really does cover the entire
 * statute and every مادة under it (D5), so the reader must be told that they
 * unlocked a نظام — a reader who believes they spent an unlock on one paragraph
 * has been misled by the interface, not by the meter.
 *
 * Returns "" when there is nothing worth saying (an `open` item with no title):
 * silence is better than a confirmation of nothing.
 */
export function unlockedNotice({
  title,
  articleNo,
  contentType,
  cost,
  reason,
}: UnlockedNoticeInput): string {
  const name = title.trim();

  if (reason === "open") {
    return "هذا المصدر متاح للجميع — لم يُحتسب من رصيدك.";
  }

  if (reason === "already_unlocked") {
    return name
      ? `«${name}» مفتوح في مكتبتك — لم يُحتسب من رصيدك.`
      : "هذا المصدر مفتوح في مكتبتك — لم يُحتسب من رصيدك.";
  }

  // reason === "granted" — the one case that actually spent something.
  const costNote = cost > 1 ? ` (احتُسب ${arNumber(cost)} من رصيدك)` : "";

  if (!name) {
    return `تم فتح هذا المصدر وأُضيف إلى مكتبتك.${costNote}`;
  }
  if (articleNo !== null) {
    return `تم فتح المادة ${arNumber(articleNo)} من «${name}» وأُضيفت إلى مكتبتك.${costNote}`;
  }
  if (contentType === "regulation") {
    // The clause that stops the D15.1 misread: a نظام, not the cited paragraph.
    return `تم فتح «${name}» كاملاً — بجميع مواده — وأُضيف إلى مكتبتك.${costNote}`;
  }
  return `تم فتح «${name}» وأُضيف إلى مكتبتك.${costNote}`;
}
