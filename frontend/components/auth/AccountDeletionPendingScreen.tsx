"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";

// Gregorian Arabic date («12 أغسطس 2026»), matching the BlogArticleView byline
// convention. No shared absolute-date helper exists in frontend/lib.
const PURGE_DATE_FORMAT = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatPurgeDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return PURGE_DATE_FORMAT.format(date);
}

/**
 * Blocking screen for an account inside the 30-day deletion grace period.
 * Rendered by AuthGuard instead of the app: every data route 403s server-side
 * while `deletion_pending` is set, so restore or log out are the only moves.
 */
export function AccountDeletionPendingScreen() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const restoreAccount = useAuthStore((s) => s.restoreAccount);
  const logout = useAuthStore((s) => s.logout);

  const [isRestoring, setIsRestoring] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Server-computed (requested + 30 days) — never derived here.
  const purgeDate = formatPurgeDate(user?.purge_at);

  const handleRestore = async () => {
    setError(null);
    setIsRestoring(true);
    try {
      await restoreAccount();
      // On success `deletion_pending` turns false → AuthGuard renders the app.
    } catch {
      setError("تعذّرت استعادة الحساب. حاول مجددًا.");
      setIsRestoring(false);
    }
  };

  const handleLogout = async () => {
    setIsLoggingOut(true);
    await logout();
    router.push("/login");
  };

  return (
    <div
      className="flex min-h-screen items-center justify-center bg-background p-4"
      dir="rtl"
      lang="ar"
      data-testid="deletion-pending-screen"
    >
      <div className="w-full max-w-md space-y-5 rounded-lg border border-border bg-card p-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
          <AlertTriangle className="h-6 w-6 text-destructive" />
        </div>

        <h1 className="text-lg font-semibold text-foreground">
          حسابك قيد الحذف
        </h1>

        <p className="text-sm leading-relaxed text-muted-foreground">
          تم إلغاء تنشيط حسابك، وسيُحذف نهائيًا
          {purgeDate ? ` بتاريخ ${purgeDate}` : " بعد انتهاء مهلة الثلاثين يومًا"}
          {" "}بما في ذلك جميع القضايا والمحادثات والمستندات. يمكنك استعادة حسابك
          قبل هذا التاريخ.
        </p>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex flex-col gap-2">
          <Button
            onClick={handleRestore}
            disabled={isRestoring || isLoggingOut}
            data-testid="restore-account-button"
          >
            {isRestoring && <Loader2 className="h-4 w-4 animate-spin" />}
            {isRestoring ? "جارٍ الاستعادة…" : "استعادة الحساب"}
          </Button>
          <Button
            variant="ghost"
            onClick={handleLogout}
            disabled={isRestoring || isLoggingOut}
          >
            {isLoggingOut && <Loader2 className="h-4 w-4 animate-spin" />}
            تسجيل الخروج
          </Button>
        </div>
      </div>
    </div>
  );
}
