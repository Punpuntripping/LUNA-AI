import { useAuthStore } from "@/stores/auth-store";

/**
 * Paid-only features (owner 2026-11-25): the writer, the sources behind an
 * answer, and saving a document as PDF / Word. Free and every marketing_*
 * trial are refused — the server enforces all three; the web only locks the
 * affordances so nobody clicks into an error.
 */
export type PaidFeature = "writer" | "export" | "sources";

export const PAID_FEATURE_COPY: Record<
  PaidFeature,
  { title: string; message: string }
> = {
  writer: {
    title: "كتابة المستندات متاحة في الباقات المدفوعة",
    message:
      "يعدّ ريحان خطة المستند في باقتك الحالية، أما كتابته كاملاً وتعديله فمتاحان في الباقات المدفوعة.",
  },
  export: {
    title: "حفظ PDF وWord متاح في الباقات المدفوعة",
    message:
      "يمكنك نسخ نص المستند في باقتك الحالية، أما حفظه ملفاً بصيغة PDF أو Word فمتاح في الباقات المدفوعة.",
  },
  sources: {
    title: "عرض المصادر متاح في الباقات المدفوعة",
    message:
      "المصادر التي يستند إليها الجواب — الأنظمة والأحكام والتعاميم ونصوصها — تظهر في الباقات المدفوعة.",
  },
};

/** `false` only when the server said this account has no paid features;
 *  unknown (`undefined` / `null`) counts as allowed. */
export function usePaidFeatures(): boolean {
  return useAuthStore((s) => s.user?.paid_features !== false);
}
