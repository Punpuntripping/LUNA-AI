"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { z } from "zod";
import { supabase } from "@/lib/supabase";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";

/**
 * «تعيين كلمة مرور جديدة» — the second half of the reset flow.
 *
 * Arrived at from the emailed link: `/auth/callback?next=/reset-password`
 * exchanged the `?code=` for a real session before redirecting here, so by the
 * time this renders the visitor is authenticated and `updateUser` has a session
 * to act on. That is also why this route is NOT in AuthGuard's PUBLIC_PREFIXES:
 * someone who lands here without a session has no code to redeem, and the guard
 * correctly sends them to /login.
 *
 * `updateUser({ password })` is the same GoTrue call the settings dialog's
 * set-password uses, so it works for a Google-only account too — it writes the
 * credential without adding an `email` identity ("ghost password"). Nothing
 * here reads identities; `has_password` comes from the credential itself
 * (migration 141), and loadUser() below refreshes it.
 */

const schema = z
  .object({
    // Same rule as signup and إعدادات الحساب — keep the messages identical.
    new_password: z
      .string()
      .min(8, "كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل"),
    confirm_password: z.string().min(1, "تأكيد كلمة المرور مطلوب"),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    message: "كلمتا المرور غير متطابقتين",
    path: ["confirm_password"],
  });

export default function ResetPasswordPage() {
  const router = useRouter();
  const loadUser = useAuthStore((s) => s.loadUser);

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const result = schema.safeParse({
      new_password: newPassword,
      confirm_password: confirmPassword,
    });

    if (!result.success) {
      const errors: Record<string, string> = {};
      for (const issue of result.error.issues) {
        const field = issue.path[0] as string;
        if (!errors[field]) errors[field] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }

    setFieldErrors({});
    setIsSaving(true);
    try {
      const { error: updateError } = await supabase.auth.updateUser({
        password: newPassword,
      });

      if (updateError) {
        // The recovery session expired, was already spent, or GoTrue rejected
        // the password itself. Both are recoverable by asking for a new link.
        setError(
          "تعذّر تعيين كلمة المرور. قد يكون الرابط منتهي الصلاحية — اطلب رابطًا جديدًا.",
        );
        return;
      }

      // Refresh the profile so `has_password` is true before إعدادات الحساب is
      // next opened — otherwise it would still offer «تعيين كلمة مرور».
      await loadUser();
      router.push("/chat");
    } catch {
      setError("تعذّر تعيين كلمة المرور. حاول مجددًا.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div
      className="flex min-h-dvh items-center justify-center bg-background px-4 py-10"
      dir="rtl"
      lang="ar"
    >
      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-border bg-card p-8"
        noValidate
      >
        <div className="flex flex-col gap-2">
          <h1 className="text-lg font-semibold text-foreground">
            تعيين كلمة مرور جديدة
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            اختر كلمة مرور جديدة لحسابك. ستتمكّن بعدها من تسجيل الدخول بالبريد
            الإلكتروني وكلمة المرور.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <label
            htmlFor="reset-password-new"
            className="block text-sm font-medium text-foreground"
          >
            كلمة المرور الجديدة
          </label>
          <div className="relative">
            <input
              id="reset-password-new"
              type={showPassword ? "text" : "password"}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              dir="ltr"
              data-testid="reset-password-new"
              className="w-full rounded-md border border-input bg-background px-3 py-2 pe-10 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <button
              type="button"
              onClick={() => setShowPassword((prev) => !prev)}
              className="absolute end-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground transition-colors hover:text-foreground"
              tabIndex={-1}
              aria-label={showPassword ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"}
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
          {fieldErrors.new_password && (
            <p className="text-xs text-destructive">{fieldErrors.new_password}</p>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <label
            htmlFor="reset-password-confirm"
            className="block text-sm font-medium text-foreground"
          >
            تأكيد كلمة المرور الجديدة
          </label>
          <input
            id="reset-password-confirm"
            type={showPassword ? "text" : "password"}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            dir="ltr"
            data-testid="reset-password-confirm"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {fieldErrors.confirm_password && (
            <p className="text-xs text-destructive">
              {fieldErrors.confirm_password}
            </p>
          )}
        </div>

        {error && (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-destructive" data-testid="reset-password-error">
              {error}
            </p>
            <Link
              href="/forgot-password"
              className="text-sm font-medium text-primary hover:underline"
            >
              اطلب رابطًا جديدًا
            </Link>
          </div>
        )}

        <Button
          type="submit"
          disabled={isSaving}
          className="w-full"
          data-testid="reset-password-submit"
        >
          {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
          {isSaving ? "جارٍ الحفظ…" : "حفظ كلمة المرور"}
        </Button>
      </form>
    </div>
  );
}
