/**
 * Single source for the free-week copy (migration 205). The week is offered as
 * the Basic card at 0, so the name, tagline and struck list price come from the
 * catalog. The limits below are the granted plan's (`marketing_lawyer`), which
 * differ from Basic's, so Basic's feature list is deliberately not reused.
 */

/** The catalog card the week is presented as. */
export const FREE_TRIAL_PLAN_ID = "basic" as const;

/**
 * `plans.ocr_pages_monthly` for `free` and for the granted plan. Used only to
 * decide when to show the offer, so a drift here mistimes a popup and nothing
 * more: the quota gate reads the database on every send.
 */
export const FREE_OCR_PAGES = 3;
export const FREE_TRIAL_OCR_PAGES = 50;

export const FREE_TRIAL_COPY = {
  title: "جرّب ريحان مجاناً لمدة أسبوع",
  badge: "أسبوع مجاني",
  price: "0",
  period: "لمدة أسبوع",
  note: "بدون بطاقة، ولا يُخصم منك شيء",
  cta: "احصل على العرض",
  pending: "جارٍ التفعيل…",
  /** The dialog's banner line: true because the blocked message sends itself. */
  unblock: "فعّل العرض وسنكمل طلبك فوراً.",
  /** Nothing was refused yet, so nothing resumes: the file stays attached. */
  unblockPdf: "فعّل العرض ويبقى ملفك مرفقاً لترسله.",
  pdfLead: (pages: number): string =>
    `عدد صفحات ملفك ${pages}، والباقة المجانية تشمل ${FREE_OCR_PAGES} صفحات.`,
  failed: "تعذّر تفعيل العرض. حاول مرة أخرى.",
  features: [
    "74 نقطة استخدام طوال الأسبوع",
    "15 نقطة لكل جلسة (5 ساعات)",
    `${FREE_TRIAL_OCR_PAGES} صفحة استخراج نص`,
    "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
    "الوصول الكامل للمكتبة القانونية",
  ],
} as const;
