import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";

// tailwind-merge only knows Tailwind's stock font-size keys. Our custom tiers
// (tailwind.config.ts `fontSize`) look like `text-<word>`, which it reads as a
// COLOUR — so `cn("text-read", "text-foreground")` silently dropped the size.
// Registering them here makes `text-read` conflict with `text-base`, not with
// `text-foreground`.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: [
            "read",
            "display",
            "h1",
            "h2",
            "h3",
            "body-lg",
            "body",
            "label",
            "caption",
            "meta",
          ],
        },
      ],
    },
  },
});

// shadcn/ui class merge utility
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// -----------------------------------------------
// Arabic relative time helpers
// -----------------------------------------------

function arabicPlural(n: number, singular: string, dual: string, plural: string, singularAbove10: string): string {
  if (n === 1) return singular;
  if (n === 2) return dual;
  if (n >= 3 && n <= 10) return `${n} ${plural}`;
  return `${n} ${singularAbove10}`;
}

export function getRelativeTimeAr(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);
  const diffWeeks = Math.floor(diffDays / 7);

  if (diffMins < 1) return "الآن";
  if (diffMins < 60) return `منذ ${arabicPlural(diffMins, "دقيقة", "دقيقتين", "دقائق", "دقيقة")}`;
  if (diffHours < 24) return `منذ ${arabicPlural(diffHours, "ساعة", "ساعتين", "ساعات", "ساعة")}`;
  if (diffDays < 2) return "أمس";
  if (diffDays < 7) return `منذ ${arabicPlural(diffDays, "يوم", "يومين", "أيام", "يوم")}`;
  if (diffDays < 30) return `منذ ${arabicPlural(diffWeeks, "أسبوع", "أسبوعين", "أسابيع", "أسبوع")}`;
  return date.toLocaleDateString(AR_DATE_LOCALE);
}

export function getDateGroupAr(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dateDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.floor((today.getTime() - dateDay.getTime()) / 86400000);

  if (diffDays === 0) return "اليوم";
  if (diffDays === 1) return "أمس";
  if (diffDays < 7) return "هذا الأسبوع";
  if (diffDays < 30) return "هذا الشهر";
  return "أقدم";
}

// -----------------------------------------------
// Case type display helpers
// -----------------------------------------------

const CASE_TYPE_LABELS: Record<string, string> = {
  "عقاري": "عقاري",
  "تجاري": "تجاري",
  "عمالي": "عمالي",
  "جنائي": "جنائي",
  "أحوال_شخصية": "أحوال شخصية",
  "إداري": "إداري",
  "تنفيذ": "تنفيذ",
  "عام": "عام",
};

export function getCaseTypeLabel(type: string): string {
  return CASE_TYPE_LABELS[type] || type;
}

const CASE_STATUS_LABELS: Record<string, string> = {
  active: "نشطة",
  closed: "مغلقة",
  archived: "مؤرشفة",
};

export function getCaseStatusLabel(status: string): string {
  return CASE_STATUS_LABELS[status] || status;
}

const PRIORITY_LABELS: Record<string, string> = {
  high: "عالية",
  medium: "متوسطة",
  low: "منخفضة",
};

export function getPriorityLabel(priority: string): string {
  return PRIORITY_LABELS[priority] || priority;
}
