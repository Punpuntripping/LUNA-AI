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
import { ApiClientError, authApi, paymentsApi } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { useAuthStore } from "@/stores/auth-store";
import { DeleteAccountDialog } from "@/components/Settings/DeleteAccountDialog";
import type { CancelSubscriptionReason, SubscriptionState } from "@/types";

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

// ── الاشتراك ─────────────────────────────────────────────────────────────────
// `.claude/plans/subscription_cancellation.md` §4.

/** Term-end dates only — day precision, Arabic locale, same as the receipts
 *  list (which adds a time because a payment happens at a moment; a term ends
 *  on a day). */
const TERM_DATE_FORMAT = new Intl.DateTimeFormat("ar-EG", {
  year: "numeric",
  month: "long",
  day: "numeric",
});

function formatTermDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return null;
  return TERM_DATE_FORMAT.format(new Date(at));
}

/** The exit survey. Keys mirror the CHECK constraint in migration 120. */
const CANCEL_REASONS: { key: CancelSubscriptionReason; label: string }[] = [
  { key: "expensive", label: "السعر مرتفع" },
  { key: "no_longer_needed", label: "انتفت حاجتي من التطبيق" },
  { key: "something_wrong", label: "واجهت شيئًا لم يعجبني" },
  { key: "other", label: "سبب آخر" },
];

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
  const savePreferredName = useAuthStore((s) => s.savePreferredName);
  // `call_name` is the resolved answer (override → derived first name), which
  // is exactly what the field should show whether or not one was ever typed.
  const callName = useAuthStore((s) => s.user?.call_name);

  const [nameInput, setNameInput] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameSaved, setNameSaved] = useState(false);
  const [isSavingName, setIsSavingName] = useState(false);

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

  const [subscription, setSubscription] = useState<SubscriptionState | null>(
    null,
  );
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] =
    useState<CancelSubscriptionReason | null>(null);
  const [cancelComment, setCancelComment] = useState("");
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const [reactivateError, setReactivateError] = useState<string | null>(null);
  const [isReactivating, setIsReactivating] = useState(false);

  // Seed the field from the resolved name every time the dialog opens — and
  // again after a save, since the server may answer with something other than
  // what was typed (trimmed, or the derived default when the field was
  // cleared). `callName` only ever changes on save or on a /auth/me refresh,
  // so this never yanks the input out from under someone mid-type.
  useEffect(() => {
    if (!open) return;
    setNameInput(callName ?? "");
  }, [open, callName]);

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

  // The subscription section is silent about failure on purpose: this dialog's
  // job is passwords, sessions and account deletion, and none of those may be
  // blocked because the subscription read hiccuped. No state → no section.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const state = await paymentsApi.getSubscription();
        if (!cancelled) setSubscription(state);
      } catch {
        if (!cancelled) setSubscription(null);
      }
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

  const resetCancelForm = () => {
    setCancelReason(null);
    setCancelComment("");
    setCancelError(null);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      resetPasswordForm();
      setNameError(null);
      setNameSaved(false);
      setLogoutAllError(null);
      setCancelOpen(false);
      resetCancelForm();
      setReactivateError(null);
    }
    onOpenChange(next);
  };

  const handleSavePreferredName = async (e: React.FormEvent) => {
    e.preventDefault();
    setNameError(null);
    setNameSaved(false);
    setIsSavingName(true);
    try {
      // An emptied field is not an error — it clears the override, and the
      // server replies with the name derived from the registration name.
      await savePreferredName(nameInput.trim() || null);
      setNameSaved(true);
    } catch (err) {
      setNameError(
        err instanceof ApiClientError && err.message
          ? err.message
          : "تعذّر حفظ الاسم. حاول مجددًا.",
      );
    } finally {
      setIsSavingName(false);
    }
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

  const handleCancelSubscription = async () => {
    if (!cancelReason) return;
    setCancelError(null);
    setIsCancelling(true);
    try {
      const next = await paymentsApi.cancelSubscription({
        reason: cancelReason,
        comment: cancelComment.trim() || undefined,
      });
      // The response IS the new state — render the cancelled copy inline
      // instead of re-fetching and leaving the section blank in between.
      setSubscription(next);
      setCancelOpen(false);
      resetCancelForm();
    } catch (err) {
      setCancelError(
        err instanceof ApiClientError && err.message
          ? err.message
          : "تعذّر إلغاء الاشتراك. حاول مجددًا.",
      );
    } finally {
      setIsCancelling(false);
    }
  };

  const handleReactivateSubscription = async () => {
    setReactivateError(null);
    setIsReactivating(true);
    try {
      setSubscription(await paymentsApi.reactivateSubscription());
    } catch (err) {
      setReactivateError(
        err instanceof ApiClientError && err.message
          ? err.message
          : "تعذّر التراجع عن الإلغاء. حاول مجددًا.",
      );
    } finally {
      setIsReactivating(false);
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

  // Visibility (plan §4): a paid term still running, or one already cancelled
  // but not yet ended. `cancellable` already implies a running term, so the
  // second clause only matters if the server ever narrows that flag.
  const termEndsAt = formatTermDate(subscription?.expires_at);
  const isCancelled = Boolean(subscription?.renewal_cancelled_at);
  const termStillRunning = subscription?.expires_at
    ? Date.parse(subscription.expires_at) > Date.now()
    : false;
  const showSubscription = Boolean(
    subscription && (subscription.cancellable || (isCancelled && termStillRunning)),
  );

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
              طريقة مناداتك وكلمة المرور والجلسات وحذف الحساب.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            <form
              onSubmit={handleSavePreferredName}
              className="flex flex-col gap-3"
              noValidate
            >
              <h3 className="text-sm font-semibold text-foreground">
                بماذا تحب أن نناديك؟
              </h3>
              <p className="text-sm text-muted-foreground">
                الاسم الذي يستخدمه ريحان عند مخاطبتك. الافتراضي هو اسمك عند
                التسجيل.
              </p>

              <input
                id="preferred-name"
                type="text"
                value={nameInput}
                onChange={(e) => {
                  setNameInput(e.target.value);
                  setNameSaved(false);
                }}
                // Mirrors users.preferred_name VARCHAR(60); the server caps it
                // again on write — this is convenience, not the enforcement.
                maxLength={60}
                autoComplete="nickname"
                placeholder="مثال: أبو محمد"
                aria-label="بماذا تحب أن نناديك؟"
                data-testid="preferred-name-input"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
              />

              {nameError && (
                <p
                  className="text-sm text-destructive"
                  data-testid="preferred-name-error"
                >
                  {nameError}
                </p>
              )}

              {nameSaved && (
                <p
                  className="flex items-start gap-2 text-sm text-success-fg"
                  data-testid="preferred-name-success"
                >
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                  تم الحفظ.
                </p>
              )}

              <Button
                type="submit"
                variant="outline"
                // Nothing to save when the field already matches the stored
                // answer — including the case where both are empty.
                disabled={isSavingName || nameInput.trim() === (callName ?? "")}
                className="w-full"
                data-testid="preferred-name-submit"
              >
                {isSavingName && <Loader2 className="h-4 w-4 animate-spin" />}
                {isSavingName ? "جارٍ الحفظ…" : "حفظ"}
              </Button>
            </form>

            <Separator />

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

            {showSubscription && subscription && (
              <>
                <div
                  className="flex flex-col gap-3"
                  data-testid="subscription-section"
                >
                  <h3 className="text-sm font-semibold text-foreground">
                    الاشتراك
                  </h3>

                  {isCancelled ? (
                    <>
                      {/* Deliberately says «لن يُجدَّد» and NOT «سيتم إيقاف
                          الدفع التلقائي»: Wave 1 sells one-time purchases, so
                          there is no automatic charge to stop, and /pricing
                          already promises «بدون تجديد تلقائي». This wording
                          stays true after the Wave 2 renewal engine ships. */}
                      <p
                        className="text-sm leading-relaxed text-muted-foreground"
                        data-testid="subscription-cancelled-note"
                      >
                        لن يُجدَّد اشتراكك — تبقى باقتك فعّالة
                        {termEndsAt ? ` حتى ${termEndsAt}` : ""} ثم تنتقل إلى
                        الباقة المجانية.
                      </p>
                      <Button
                        variant="outline"
                        className="w-full"
                        onClick={handleReactivateSubscription}
                        disabled={isReactivating}
                        data-testid="subscription-reactivate"
                      >
                        {isReactivating && (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        )}
                        تراجع عن الإلغاء
                      </Button>
                      {reactivateError && (
                        <p
                          className="text-sm text-destructive"
                          data-testid="subscription-reactivate-error"
                        >
                          {reactivateError}
                        </p>
                      )}
                    </>
                  ) : (
                    <>
                      <p className="text-sm font-medium text-foreground">
                        باقة {subscription.plan_name_ar ?? subscription.plan_id}
                      </p>
                      {termEndsAt && (
                        <p className="text-sm text-muted-foreground">
                          تنتهي في {termEndsAt}
                        </p>
                      )}
                      {/* Subdued: cancelling is allowed, not encouraged, and it
                          sits one section above منطقة الخطر — it must not read
                          as destructive either. */}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="w-fit px-0 text-muted-foreground hover:text-foreground"
                        onClick={() => {
                          resetCancelForm();
                          setCancelOpen(true);
                        }}
                        data-testid="subscription-cancel-open"
                      >
                        إلغاء الاشتراك
                      </Button>
                    </>
                  )}
                </div>

                <Separator />
              </>
            )}

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

      <AlertDialog
        open={cancelOpen}
        onOpenChange={(next) => {
          if (!next && !isCancelling) {
            setCancelOpen(false);
            resetCancelForm();
          }
        }}
      >
        <AlertDialogContent dir="rtl" lang="ar">
          <AlertDialogHeader>
            <AlertDialogTitle>إلغاء الاشتراك؟</AlertDialogTitle>
            <AlertDialogDescription asChild>
              {/* asChild + div: the body holds inputs, and a <p> may not
                  contain them (same reason PaymentHistoryDialog does it). */}
              <div className="flex flex-col gap-3 text-start">
                <span className="text-sm text-muted-foreground">
                  تبقى باقتك فعّالة
                  {termEndsAt ? ` حتى ${termEndsAt}` : ""} ثم تنتقل إلى الباقة
                  المجانية. يمكنك التراجع في أي وقت قبل ذلك.
                </span>

                <span className="text-sm font-medium text-foreground">
                  ما سبب الإلغاء؟
                </span>

                <div
                  role="radiogroup"
                  aria-label="سبب الإلغاء"
                  className="flex flex-col gap-2"
                >
                  {CANCEL_REASONS.map((option) => (
                    <label
                      key={option.key}
                      className="flex cursor-pointer items-center gap-2 text-sm text-foreground"
                    >
                      <input
                        type="radio"
                        name="cancel-reason"
                        value={option.key}
                        checked={cancelReason === option.key}
                        onChange={() => setCancelReason(option.key)}
                        disabled={isCancelling}
                        className="h-4 w-4 accent-primary"
                        data-testid={`cancel-reason-${option.key}`}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>

                <textarea
                  value={cancelComment}
                  onChange={(e) => setCancelComment(e.target.value)}
                  disabled={isCancelling}
                  rows={3}
                  maxLength={2000}
                  placeholder="اختياري: أخبرنا المزيد"
                  aria-label="تفاصيل إضافية (اختياري)"
                  data-testid="cancel-comment"
                  className="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                />

                {cancelError && (
                  <span
                    className="text-sm text-destructive"
                    data-testid="cancel-subscription-error"
                  >
                    {cancelError}
                  </span>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isCancelling}>
              تراجع
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Keep the dialog open while the request is in flight so the
                // error has somewhere to render — AlertDialogAction closes on
                // click by default.
                e.preventDefault();
                void handleCancelSubscription();
              }}
              // A survey with no answer is the one thing this flow exists to
              // collect, so the confirm stays closed until a reason is picked.
              disabled={!cancelReason || isCancelling}
              data-testid="cancel-subscription-confirm"
            >
              {isCancelling && <Loader2 className="h-4 w-4 animate-spin" />}
              تأكيد الإلغاء
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
