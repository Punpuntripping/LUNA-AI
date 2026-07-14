"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { CheckCircle2, Eye, EyeOff, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { ApiClientError, authApi } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { useAuthStore } from "@/stores/auth-store";
import { DeleteAccountDialog } from "@/components/Settings/DeleteAccountDialog";

// Same rule as signup (LoginForm) — keep the messages identical.
const changePasswordSchema = z
  .object({
    current_password: z.string().min(1, "كلمة المرور الحالية مطلوبة"),
    new_password: z
      .string()
      .min(8, "كلمة المرور يجب أن تحتوي على 8 أحرف على الأقل"),
    confirm_password: z.string().min(1, "تأكيد كلمة المرور مطلوب"),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    message: "كلمتا المرور غير متطابقتين",
    path: ["confirm_password"],
  });

interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  error?: string;
  testId: string;
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  error,
  testId,
}: PasswordFieldProps) {
  const [show, setShow] = useState(false);

  return (
    <div className="space-y-1.5">
      <label
        htmlFor={id}
        className="block text-sm font-medium text-foreground"
      >
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={show ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="••••••••"
          autoComplete={autoComplete}
          dir="ltr"
          data-testid={testId}
          className={cn(
            "w-full rounded-md border bg-background px-3 py-2 pe-10 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring",
            error ? "border-destructive" : "border-input",
          )}
        />
        <button
          type="button"
          onClick={() => setShow((prev) => !prev)}
          className="absolute end-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground transition-colors hover:text-foreground"
          tabIndex={-1}
          aria-label={show ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"}
        >
          {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

interface AccountSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * إعدادات الحساب — change password · log out of all devices · delete account.
 *
 * The change-password section is hidden for accounts with no password identity
 * (Google-only), resolved from the Supabase session's `app_metadata.providers`.
 * That read is display-only: the backend independently verifies the real
 * identity via the admin API on every one of these endpoints.
 */
export function AccountSettingsDialog({
  open,
  onOpenChange,
}: AccountSettingsDialogProps) {
  const router = useRouter();
  const logoutAll = useAuthStore((s) => s.logoutAll);

  const [hasPasswordIdentity, setHasPasswordIdentity] = useState<boolean | null>(
    null,
  );

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const [confirmLogoutAll, setConfirmLogoutAll] = useState(false);
  const [logoutAllError, setLogoutAllError] = useState<string | null>(null);
  const [isLoggingOutAll, setIsLoggingOutAll] = useState(false);

  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      const { data } = await supabase.auth.getSession();
      const meta = data.session?.user.app_metadata as
        | { providers?: string[]; provider?: string }
        | undefined;
      const providers =
        meta?.providers ?? (meta?.provider ? [meta.provider] : []);
      if (!cancelled) setHasPasswordIdentity(providers.includes("email"));
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const resetPasswordForm = () => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setFieldErrors({});
    setPasswordError(null);
    setPasswordSuccess(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      resetPasswordForm();
      setLogoutAllError(null);
    }
    onOpenChange(next);
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(false);

    const result = changePasswordSchema.safeParse({
      current_password: currentPassword,
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
    setIsChangingPassword(true);
    try {
      await authApi.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordSuccess(true);
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) {
          setPasswordError("كلمة المرور الحالية غير صحيحة");
        } else if (err.status === 429) {
          setPasswordError("تم تجاوز الحد المسموح من المحاولات. حاول بعد قليل.");
        } else {
          setPasswordError(err.message || "تعذّر تغيير كلمة المرور. حاول مجددًا.");
        }
      } else {
        setPasswordError("تعذّر تغيير كلمة المرور. حاول مجددًا.");
      }
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleLogoutAll = async () => {
    setLogoutAllError(null);
    setIsLoggingOutAll(true);
    try {
      await logoutAll();
      router.push("/login");
    } catch {
      setLogoutAllError("تعذّر تسجيل الخروج من الأجهزة. حاول مجددًا.");
      setIsLoggingOutAll(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          // Three stacked sections overflow a short viewport, and DialogContent
          // is centred with translate-y-[-50%] — without a bounded height the
          // overflow is simply unreachable. Same fix as OnboardingDialog.
          className="max-h-[85vh] max-w-md overflow-y-auto"
          dir="rtl"
          lang="ar"
          data-testid="account-settings-dialog"
        >
          <DialogHeader>
            <DialogTitle>إعدادات الحساب</DialogTitle>
            <DialogDescription>
              كلمة المرور والجلسات وحذف الحساب.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            {hasPasswordIdentity && (
              <>
                <form
                  onSubmit={handleChangePassword}
                  className="flex flex-col gap-3"
                  noValidate
                >
                  <h3 className="text-sm font-semibold text-foreground">
                    تغيير كلمة المرور
                  </h3>

                  <PasswordField
                    id="change-password-current"
                    label="كلمة المرور الحالية"
                    value={currentPassword}
                    onChange={setCurrentPassword}
                    autoComplete="current-password"
                    error={fieldErrors.current_password}
                    testId="change-password-current"
                  />
                  <PasswordField
                    id="change-password-new"
                    label="كلمة المرور الجديدة"
                    value={newPassword}
                    onChange={setNewPassword}
                    autoComplete="new-password"
                    error={fieldErrors.new_password}
                    testId="change-password-new"
                  />
                  <PasswordField
                    id="change-password-confirm"
                    label="تأكيد كلمة المرور الجديدة"
                    value={confirmPassword}
                    onChange={setConfirmPassword}
                    autoComplete="new-password"
                    error={fieldErrors.confirm_password}
                    testId="change-password-confirm"
                  />

                  {passwordError && (
                    <p
                      className="text-sm text-destructive"
                      data-testid="change-password-error"
                    >
                      {passwordError}
                    </p>
                  )}

                  {passwordSuccess && (
                    <p
                      className="flex items-start gap-2 text-sm text-success-fg"
                      data-testid="change-password-success"
                    >
                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                      تم تغيير كلمة المرور. تم تسجيل الخروج من الأجهزة الأخرى.
                    </p>
                  )}

                  <Button
                    type="submit"
                    disabled={isChangingPassword}
                    className="w-full"
                    data-testid="change-password-submit"
                  >
                    {isChangingPassword && (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    )}
                    {isChangingPassword ? "جارٍ الحفظ…" : "تغيير كلمة المرور"}
                  </Button>
                </form>

                <Separator />
              </>
            )}

            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-semibold text-foreground">الجلسات</h3>
              <p className="text-sm text-muted-foreground">
                إنهاء جميع الجلسات النشطة. قد تستغرق الأجهزة الأخرى حتى ساعة حتى
                تُغلق جلساتها بالكامل.
              </p>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => setConfirmLogoutAll(true)}
                disabled={isLoggingOutAll}
                data-testid="logout-all-button"
              >
                {isLoggingOutAll && <Loader2 className="h-4 w-4 animate-spin" />}
                تسجيل الخروج من جميع الأجهزة
              </Button>
              {logoutAllError && (
                <p
                  className="text-sm text-destructive"
                  data-testid="logout-all-error"
                >
                  {logoutAllError}
                </p>
              )}
            </div>

            <Separator />

            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-semibold text-destructive">
                منطقة الخطر
              </h3>
              <p className="text-sm text-muted-foreground">
                حذف الحساب نهائيًا بعد ٣٠ يومًا، بما في ذلك جميع القضايا
                والمحادثات والمستندات.
              </p>
              <Button
                variant="destructive"
                className="w-full"
                onClick={() => setDeleteOpen(true)}
                data-testid="open-delete-account"
              >
                حذف الحساب
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmLogoutAll} onOpenChange={setConfirmLogoutAll}>
        <AlertDialogContent dir="rtl" lang="ar">
          <AlertDialogHeader>
            <AlertDialogTitle>تسجيل الخروج من جميع الأجهزة؟</AlertDialogTitle>
            <AlertDialogDescription>
              سيتم تسجيل خروجك من جميع الأجهزة بما فيها هذا الجهاز.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>إلغاء</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleLogoutAll}
              className={cn(buttonVariants({ variant: "destructive" }))}
              data-testid="logout-all-confirm"
            >
              تسجيل الخروج
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <DeleteAccountDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        hasPasswordIdentity={hasPasswordIdentity ?? false}
      />
    </>
  );
}
