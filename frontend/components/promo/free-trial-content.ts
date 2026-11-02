/**
 * Single source for the free-week copy (migration 205). The week is offered as
 * the Basic card at 0, so the name, tagline and struck list price come from the
 * catalog. The limits below are the granted plan's (`marketing_lawyer`), which
 * differ from Basic's, so Basic's feature list is deliberately not reused.
 */

/** The catalog card the week is presented as. */
export const FREE_TRIAL_PLAN_ID = "basic" as const;

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
  failed: "تعذّر تفعيل العرض. حاول مرة أخرى.",
  features: [
    "74 نقطة استخدام طوال الأسبوع",
    "15 نقطة لكل جلسة (5 ساعات)",
    "25 صفحة استخراج نص",
    "تقارير الباحث ومسودات الكاتب كاملة مع مشاركتها وتصديرها",
    "الوصول الكامل للمكتبة القانونية",
  ],
} as const;
