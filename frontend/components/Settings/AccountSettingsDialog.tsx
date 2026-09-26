"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { CheckCircle2, CreditCard, Loader2 } from "lucide-react";
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
import { Switch } from "@/components/ui/switch";
import { useMarketingEmail, useUpdateMarketingEmail } from "@/hooks/use-preferences";
import { cn } from "@/lib/utils";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import { PasswordInput } from "@/components/ui/password-input";
import { ApiClientError, authApi, paymentsApi } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { DeleteAccountDialog } from "@/components/Settings/DeleteAccountDialog";
import { QuotaUpgradeDialog } from "@/components/chat/QuotaUpgradeDialog";
import { pricingPlansAbove } from "@/lib/pricing";
import { useEarlyAdopterStore } from "@/stores/early-adopter-store";
import { usePaymentMethod, useRemovePaymentMethod } from "@/hooks/use-payments";
import type { CancelSubscriptionReason, SubscriptionState } from "@/types";
import { PromoTermsLink } from "@/components/pricing/PromoTermsLink";

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

// Setting a FIRST password (Google-OAuth accounts): same rules minus the
// current-password field, because there is none to re-enter.
const setPasswordSchema = z
  .object({
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
  autoComplete: "current-password" | "new-password";
  error?: string;
  testId: string;
}

/** Thin alias over the shared `PasswordInput` so the existing call sites in
 *  this dialog keep their prop names. The markup — and the RTL/LTR toggle
 *  collision it used to carry — now lives in one place. */
function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  error,
  testId,
}: PasswordFieldProps) {
  return (
    <PasswordInput
      id={id}
      label={label}
      value={value}
      onChange={onChange}
      autoComplete={autoComplete}
      error={error}
      data-testid={testId}
    />
  );
}

// ── الاشتراك ─────────────────────────────────────────────────────────────────
// `.claude/plans/subscription_cancellation.md` §4.

/** Term-end dates only — day precision, Arabic locale, same as the receipts
 *  list (which adds a time because a payment happens at a moment; a term ends
 *  on a day). */
const TERM_DATE_FORMAT = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
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
  { key: "no_longer_needed", label: "لم أعد بحاجة إلى التطبيق" },
  { key: "something_wrong", label: "عدم الرضا عن الخدمة" },
  { key: "other", label: "سبب آخر" },
];

// ── وسيلة الدفع ──────────────────────────────────────────────────────────────
// `.claude/plans/subscription_auto_renewal.md` §9. Everything here is display
// data returned by the provider at tokenization; the token itself never leaves
// the backend, so there is nothing on this surface worth stealing.

/** Card expiry — month + year, same Arabic locale as the term dates above. */
const CARD_EXPIRY_FORMAT = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  year: "numeric",
  month: "long",
});

/** Provider brand strings → Arabic. mada first: it is the dominant network here. */
const CARD_BRANDS: Record<string, string> = {
  mada: "مدى",
  visa: "فيزا",
  mastercard: "ماستركارد",
  amex: "أمريكان إكسبريس",
};

/** An unknown brand renders verbatim rather than vanishing — an English word
 *  beats a card the user cannot identify before deleting it. */
function formatCardBrand(brand: string | null | undefined): string {
  if (!brand) return "بطاقة";
  return CARD_BRANDS[brand.trim().toLowerCase()] ?? brand;
}

/** Wallet `source_type` → the product name, which Apple and Samsung do not
 *  translate and which is marketed in Latin here. */
const METHOD_SOURCES: Record<string, string> = {
  applepay: "Apple Pay",
  samsungpay: "Samsung Pay",
};

/**
 * How the credential should introduce itself — or null when the card brand is
 * the whole answer.
 *
 * A wallet is NOT a card brand. An Apple Pay credential carries the FUNDING
 * card's brand and last4 (Moyasar returns the FPAN, never the device PAN), so
 * showing «فيزا ••••2796» alone described the plastic behind the wallet and
 * quietly claimed to be the wallet's own identity. Both facts are true and both
 * are shown; this function supplies the first one.
 *
 * `creditcard` → null: a typed PAN has no wrapper, and the line stays exactly
 * as it has always rendered. Absent/null → null as well — provenance was added
 * to `payment_methods` after these rows existed and can never be recovered
 * retroactively, so silence is the only honest answer.
 *
 * ⚠ Deliberately does NOT fall back to the raw string the way `formatCardBrand`
 * does. An unmapped brand is still a word the user reads off their card; an
 * unmapped `source_type` is a provider identifier («googlepay») that would land
 * as bare Latin machine text inside an RTL panel. Whatever it is, it is a
 * wallet — say that in Arabic and stop.
 */
function formatMethodSource(
  sourceType: string | null | undefined,
): string | null {
  if (!sourceType) return null;
  const key = sourceType.trim().toLowerCase();
  if (key === "creditcard") return null;
  return METHOD_SOURCES[key] ?? "محفظة رقمية";
}

/**
 * «فيزا ••••2796» — the funding card, wherever it happens to be shown.
 *
 * Extracted because a wallet credential moves it onto its own muted line while
 * a plain card keeps it on the first one. Exactly one of the two renders, so
 * `payment-method-last4` stays a unique test id.
 */
function CardMask({
  brand,
  last4,
}: {
  brand: string | null;
  last4: string | null;
}) {
  return (
    <>
      {formatCardBrand(brand)}
      {last4 && (
        // Latin digits, LTR: these four characters exist to be matched against
        // the plastic (and the banking app), where they are printed in Latin.
        // Amounts and dates stay Arabic-Indic — an identifier is not a number.
        <span
          dir="ltr"
          className="tabular-nums"
          data-testid="payment-method-last4"
        >
          •••• {last4}
        </span>
      )}
    </>
  );
}

/** The plans that renew by charging a stored card — mirrors
 *  `payment_method_service.RENEWABLE_PLAN_IDS`, which stays authoritative; this
 *  decides display only. `basic` is a one-time 7-day term that ends without a
 *  further charge, so it must never be warned about a renewal it never had. */
const RENEWING_PLAN_IDS = new Set(["pro", "max"]);

/**
 * «أغسطس 2027» from `exp_month` + `exp_year`, or null for anything unusable.
 *
 * ⚠ LOCAL midnight, never `Date.UTC`: formatting a UTC instant in a
 * negative-offset zone lands on the previous day, and for a first-of-month date
 * that silently shifts the whole label back a month.
 */
function formatCardExpiry(
  month: number | null | undefined,
  year: number | null | undefined,
): string | null {
  if (!month || !year || month < 1 || month > 12) return null;
  // Providers send either a full year (2027) or two digits (27).
  const fullYear = year < 100 ? 2000 + year : year;
  if (fullYear < 2000 || fullYear > 2100) return null;
  return CARD_EXPIRY_FORMAT.format(new Date(fullYear, month - 1, 1));
}

interface AccountSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * إعدادات الحساب — change password · log out of all devices · delete account.
 *
 * Accounts with no password (Google-OAuth signups — most of the user base) get
 * «تعيين كلمة مرور» instead of «تغيير كلمة المرور». Which one is shown comes
 * from the server-resolved `user.has_password` (migration 141), NOT from the
 * Supabase session's `app_metadata.providers` as it once did: setting a password
 * on a Google account does not add an `email` identity, so that read stays
 * ["google"] forever and would keep hiding the form from someone who has a
 * password. Display-only either way — the backend re-checks on every one of
 * these endpoints and 409s a set-password against an account that has one.
 */
export function AccountSettingsDialog({
  open,
  onOpenChange,
}: AccountSettingsDialogProps) {
  const router = useRouter();
  const marketingEmail = useMarketingEmail(open);
  const updateMarketingEmail = useUpdateMarketingEmail();
  const logoutAll = useAuthStore((s) => s.logoutAll);
  const savePreferredName = useAuthStore((s) => s.savePreferredName);
  const loadUser = useAuthStore((s) => s.loadUser);
  // `call_name` is the resolved answer (override → derived first name), which
  // is exactly what the field should show whether or not one was ever typed.
  const callName = useAuthStore((s) => s.user?.call_name);

  const [nameInput, setNameInput] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameSaved, setNameSaved] = useState(false);
  const [isSavingName, setIsSavingName] = useState(false);

  // Server-resolved (/auth/me). Undefined on a degraded read → treated as
  // "no password", which only ever OFFERS to set one; the server refuses if
  // there already is one.
  const hasPassword = useAuthStore((s) => s.user?.has_password ?? false);

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
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [cancelReason, setCancelReason] =
    useState<CancelSubscriptionReason | null>(null);
  const [cancelComment, setCancelComment] = useState("");
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const [reactivateError, setReactivateError] = useState<string | null>(null);
  const [isReactivating, setIsReactivating] = useState(false);

  // المشتركون الأوائل — one probe per session, shared with the upgrade dialog.
  // Only the ladder's PRICE ORDER is read from it; whether THIS user holds a
  // seat comes from the authed subscription payload, never from the campaign
  // flag (a seat holder keeps their price after the campaign closes).
  const campaign = useEarlyAdopterStore((s) => s.campaign);
  const ensureCampaignLoaded = useEarlyAdopterStore((s) => s.ensureLoaded);
  useEffect(() => {
    if (open) ensureCampaignLoaded();
  }, [open, ensureCampaignLoaded]);

  // وسيلة الدفع — read only while the dialog is open, and fail-quiet exactly
  // like the subscription read above: no card (or no such endpoint, which is
  // what a backend with the renewal flag off looks like) → no section.
  const { data: paymentMethod } = usePaymentMethod(open);
  const removeCard = useRemovePaymentMethod();
  const [removeCardOpen, setRemoveCardOpen] = useState(false);
  const [removeCardError, setRemoveCardError] = useState<string | null>(null);

  // Seed the field from the resolved name every time the dialog opens — and
  // again after a save, since the server may answer with something other than
  // what was typed (trimmed, or the derived default when the field was
  // cleared). `callName` only ever changes on save or on a /auth/me refresh,
  // so this never yanks the input out from under someone mid-type.
  useEffect(() => {
    if (!open) return;
    setNameInput(callName ?? "");
  }, [open, callName]);

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
      setRemoveCardOpen(false);
      setRemoveCardError(null);
      removeCard.reset();
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

  const handleSetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(false);

    const result = setPasswordSchema.safeParse({
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
      await authApi.setPassword(newPassword);
      setNewPassword("");
      setConfirmPassword("");
      setPasswordSuccess(true);
      // Re-read /auth/me so `has_password` flips and this section becomes the
      // CHANGE form. Without it the user could submit «تعيين» twice and get a
      // confusing 409 from the server for what looks like the same action.
      await loadUser();
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 409) {
          // Raced another tab, or /me had degraded to has_password=false.
          setPasswordError(
            "هذا الحساب يملك كلمة مرور بالفعل. أعد فتح الإعدادات لتغييرها.",
          );
          void loadUser();
        } else if (err.status === 429) {
          setPasswordError("تم تجاوز الحد المسموح من المحاولات. حاول بعد قليل.");
        } else {
          setPasswordError(err.message || "تعذّر تعيين كلمة المرور. حاول مجددًا.");
        }
      } else {
        setPasswordError("تعذّر تعيين كلمة المرور. حاول مجددًا.");
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

  const handleRemovePaymentMethod = async () => {
    setRemoveCardError(null);
    try {
      await removeCard.mutateAsync();
      setRemoveCardOpen(false);
    } catch (err) {
      setRemoveCardError(
        err instanceof ApiClientError && err.message
          ? err.message
          : "تعذّر إزالة البطاقة. حاول مجددًا.",
      );
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

  // The upgrade path, offered BEFORE a wall is hit — the quota banner only ever
  // catches someone already blocked. Derived by price from the catalog: there is
  // no blocking window here to ask the server about, and price order mirrors the
  // server's downgrade guard, so nothing offered can be refused at checkout.
  // Empty for `max` (nothing above it) — and then no button at all. Ranked on
  // EFFECTIVE prices while المشتركون الأوائل is open, so the ladder matches the
  // numbers the upgrade dialog then puts on its cards.
  const upgradePlans = pricingPlansAbove(subscription?.plan_id, campaign.open);

  // Seat holder? Straight from the authed subscription read — an older backend
  // omits the block entirely, and absent must mean "no", so the warning below is
  // shown only on an explicit `true`.
  const isEarlyAdopter = subscription?.early_adopter?.is_member === true;

  // The stored-card surface. Shown on `has_method` ALONE — never gated on a
  // running subscription: a credential the user cannot see is a credential they
  // cannot remove, and it outlives the term that created it.
  const hasCard = paymentMethod?.has_method === true;
  const cardExpiry = formatCardExpiry(
    paymentMethod?.exp_month,
    paymentMethod?.exp_year,
  );
  const cardConsentAt = formatTermDate(paymentMethod?.consent_given_at);
  // «Apple Pay» / «Samsung Pay» when the credential came out of a wallet — null
  // for a typed card and for every row stored before provenance existed.
  const walletLabel = formatMethodSource(paymentMethod?.source_type);

  // The silent non-renewal. A pro/max term that is still running and has not
  // been cancelled renews by charging a stored card; with no card there is
  // nothing to charge, and the renewal sweep drops the user without a word.
  // Apple Pay buyers land here by construction (the checkout form tokenizes
  // card purchases only), and so does anyone who removed their card. Until
  // now this surface rendered NOTHING for them — the one fact that costs them
  // their plan was the one fact never shown.
  //
  // `cancellable` already means source='payment' AND a term still running —
  // the same two walls the renewal sweep applies — so only the plan and the
  // opt-out need adding on top. An ALREADY-CANCELLED subscription is excluded
  // deliberately: the section above it already says «لن يُجدَّد اشتراكك», and
  // repeating that as a warning would read as a second, different problem.
  //
  // ⚠ `paymentMethod` must be DEFINED before this may fire. The hook fails
  // quiet — a 404 on a backend without the endpoint, or any hiccup, resolves
  // to "no method" — so `undefined` is the only value meaning "not known yet",
  // and firing on it would flash the warning at every subscriber on open.
  //
  // ⚠ And `unavailable` means the read FAILED, which is not the same answer as
  // "no card". Telling a paying subscriber their subscription will not renew
  // because one request hiccuped is a worse lie than saying nothing, so a
  // failed read shows what it showed before this warning existed: nothing.
  const showMissingCard =
    Boolean(paymentMethod) &&
    paymentMethod?.unavailable !== true &&
    !hasCard &&
    RENEWING_PLAN_IDS.has(subscription?.plan_id ?? "") &&
    subscription?.cancellable === true &&
    !isCancelled;

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          // Three stacked sections overflow a short viewport, and DialogContent
          // is centred with translate-y-[-50%] — without a bounded height the
          // overflow is simply unreachable. Same fix as OnboardingDialog.
          className="max-h-[85dvh] max-w-md overflow-y-auto"
          presentation="mobileSheet"
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

            {hasPassword ? (
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
            ) : (
              <>
                <form
                  onSubmit={handleSetPassword}
                  className="flex flex-col gap-3"
                  noValidate
                >
                  <h3 className="text-sm font-semibold text-foreground">
                    تعيين كلمة مرور
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    سجّلت الدخول عبر Google، فلا توجد كلمة مرور لحسابك. عيّن
                    كلمة مرور لتتمكّن من الدخول بالبريد الإلكتروني أيضًا.
                  </p>

                  <PasswordField
                    id="set-password-new"
                    label="كلمة المرور الجديدة"
                    value={newPassword}
                    onChange={setNewPassword}
                    autoComplete="new-password"
                    error={fieldErrors.new_password}
                    testId="set-password-new"
                  />
                  <PasswordField
                    id="set-password-confirm"
                    label="تأكيد كلمة المرور الجديدة"
                    value={confirmPassword}
                    onChange={setConfirmPassword}
                    autoComplete="new-password"
                    error={fieldErrors.confirm_password}
                    testId="set-password-confirm"
                  />

                  {passwordError && (
                    <p
                      className="text-sm text-destructive"
                      data-testid="set-password-error"
                    >
                      {passwordError}
                    </p>
                  )}
                  {passwordSuccess && (
                    <p
                      className="text-sm text-emerald-600 dark:text-emerald-500"
                      data-testid="set-password-success"
                    >
                      تم تعيين كلمة المرور
                    </p>
                  )}

                  <Button
                    type="submit"
                    variant="outline"
                    disabled={isChangingPassword}
                    className="w-full"
                    data-testid="set-password-submit"
                  >
                    {isChangingPassword && (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    )}
                    {isChangingPassword ? "جارٍ الحفظ…" : "تعيين كلمة المرور"}
                  </Button>
                </form>

                <Separator />
              </>
            )}

            {/* Marketing email — the in-product half of PDPL Art. 25's "clear
                mechanism to stop" (migration 167). Silent on a failed read,
                like the subscription section: no state → no section. */}
            {marketingEmail.data && (
              <>
                <div
                  className="flex flex-col gap-3"
                  data-testid="marketing-email-section"
                >
                  <h3 className="text-sm font-semibold text-foreground">
                    البريد الإلكتروني
                  </h3>
                  <div className="flex items-start justify-between gap-4">
                    <label
                      htmlFor="marketing-email-switch"
                      className="text-sm leading-relaxed text-muted-foreground"
                    >
                      استلام محتوى ترويجي وتحديثات عبر البريد الإلكتروني. رسائل
                      حسابك (كتأكيد التسجيل وانتهاء الاشتراك) تصلك في كل الأحوال.
                    </label>
                    <Switch
                      id="marketing-email-switch"
                      checked={marketingEmail.data.marketing_opt_in}
                      onCheckedChange={(v) => updateMarketingEmail.mutate(v)}
                      disabled={updateMarketingEmail.isPending}
                      data-testid="marketing-email-switch"
                    />
                  </div>
                  {updateMarketingEmail.isError && (
                    <p className="text-sm text-destructive">
                      تعذّر حفظ التفضيل. حاول مرة أخرى.
                    </p>
                  )}
                </div>

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
                          الدفع التلقائي»: pro/max are meant to auto-renew, but
                          the engine has not shipped, so there is no automatic
                          charge to stop today. The forward-looking wording is
                          the one sentence true both now and after the Wave 2
                          renewal engine lands. */}
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
                      <div className="flex flex-wrap items-center gap-3">
                        {upgradePlans.length > 0 && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setUpgradeOpen(true)}
                            data-testid="subscription-upgrade-open"
                          >
                            ترقية الباقة
                          </Button>
                        )}
                        {/* Subdued: cancelling is allowed, not encouraged, and
                            it sits one section above منطقة الخطر — it must not
                            read as destructive either. */}
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
                      </div>
                    </>
                  )}
                </div>

                <Separator />
              </>
            )}

            {hasCard && paymentMethod && (
              <>
                <div
                  className="flex flex-col gap-3"
                  data-testid="payment-method-section"
                >
                  <h3 className="text-sm font-semibold text-foreground">
                    وسيلة الدفع
                  </h3>

                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                    <CreditCard className="h-4 w-4 shrink-0 text-muted-foreground" />
                    {walletLabel ? (
                      // dir=ltr: «Apple Pay» is a two-word Latin product name,
                      // and RTL reorders its words without its own direction.
                      <span dir="ltr" data-testid="payment-method-wallet">
                        {walletLabel}
                      </span>
                    ) : (
                      <CardMask
                        brand={paymentMethod.brand}
                        last4={paymentMethod.last4}
                      />
                    )}
                  </p>

                  {/* The funding card, kept and never replaced: Moyasar
                      confirmed `last_four` is the FPAN — the number printed on
                      the plastic and shown on the bank statement — while the
                      device PAN the wallet actually charges is a different
                      number the user has never seen. Dropping it would leave
                      someone with two wallet-eligible cards unable to tell
                      which one renews. */}
                  {walletLabel &&
                    (paymentMethod.brand || paymentMethod.last4) && (
                      <p
                        className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
                        data-testid="payment-method-funding-card"
                      >
                        {/* Its own element, not a bare text node: two adjacent
                            text runs in a flex row collapse into ONE anonymous
                            flex item, and `gap` would then never separate the
                            label from the brand. */}
                        <span>البطاقة المرتبطة:</span>
                        <CardMask
                          brand={paymentMethod.brand}
                          last4={paymentMethod.last4}
                        />
                      </p>
                    )}

                  {cardExpiry && (
                    <p className="text-sm text-muted-foreground">
                      تنتهي صلاحيتها في {cardExpiry}
                    </p>
                  )}

                  {cardConsentAt && (
                    <p className="text-sm text-muted-foreground">
                      سُجّلت موافقتك على التجديد التلقائي في {cardConsentAt}.
                    </p>
                  )}

                  {/* Subdued for the same reason as «إلغاء الاشتراك» above:
                      allowed, not encouraged, and it must not read as
                      destructive one section from منطقة الخطر. */}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-fit px-0 text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      setRemoveCardError(null);
                      setRemoveCardOpen(true);
                    }}
                    data-testid="payment-method-remove-open"
                  >
                    إزالة البطاقة
                  </Button>
                </div>

                <Separator />
              </>
            )}

            {showMissingCard && (
              <>
                <div
                  className="flex flex-col gap-3"
                  data-testid="payment-method-missing"
                >
                  <h3 className="text-sm font-semibold text-foreground">
                    وسيلة الدفع
                  </h3>

                  {/* «لن يُجدَّد» and never «سيتم إيقاف الدفع التلقائي» — the
                      same rule the removal dialog below follows, for the same
                      reason: the forward-looking wording is the one sentence
                      true regardless of what the renewal engine is doing. */}
                  <p
                    className="text-sm font-medium text-foreground"
                    data-testid="payment-method-missing-consequence"
                  >
                    لا توجد بطاقة محفوظة، ولن يُجدَّد اشتراكك تلقائياً.
                  </p>

                  {/* NO «أضف بطاقة» button: there is no endpoint behind one.
                      A card can only be stored by completing a payment, which
                      is exactly what the removal dialog already tells the
                      user — same sentence, so the two surfaces cannot drift
                      into promising different things. */}
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {termEndsAt
                      ? `تبقى باقتك فعّالة حتى ${termEndsAt} ثم تنتقل إلى الباقة المجانية. `
                      : ""}
                    يمكنك حفظ بطاقة عند أي عملية دفع لاحقة.
                  </p>
                </div>

                <Separator />
              </>
            )}

            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-semibold text-destructive">
                منطقة الخطر
              </h3>
              <p className="text-sm text-muted-foreground">
                حذف الحساب نهائيًا بعد 30 يومًا، بما في ذلك جميع القضايا
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

                {/* المشتركون الأوائل — early_adopters.md §6.2. Stated BEFORE
                    the confirm because the rule is irreversible: a completed
                    cancellation releases the seat and the promotional price is
                    gone for good, seats open or not.

                    ⚠ TONE IS THE SPEC HERE. This is a retention lever pointed
                    at someone in the act of leaving, so it reads as a fact and
                    carries the undo deadline in the same breath. Deliberately
                    NOT destructive-red, NOT a countdown, NOT a second confirm
                    step — the primary tint marks it as information about their
                    standing, not an alarm. Do not "strengthen" it. */}
                {isEarlyAdopter && (
                  <span
                    className="rounded-lg border border-primary/30 bg-primary/5 px-3 py-2.5 text-sm leading-relaxed text-foreground"
                    data-testid="cancel-early-adopter-note"
                  >
                    {/* Written out rather than interpolated from
                        EARLY_ADOPTER_LABEL: that constant is the campaign's
                        NAME («المشتركون الأوائل»), and after «من» Arabic needs
                        the genitive «المشتركين». A constant cannot carry both
                        cases, and the badge is the one that must match the
                        marketing exactly. */}
                    أنت من{" "}
                    <strong className="font-semibold">المشتركين الأوائل</strong>
                    . إذا اكتمل الإلغاء فسيعود سعر باقتك إلى السعر المعتاد، ولن
                    يمكن استعادة سعر المشتركين الأوائل لاحقاً. يمكنك التراجع عن
                    الإلغاء
                    {termEndsAt
                      ? ` قبل انتهاء اشتراكك في ${termEndsAt}.`
                      : " قبل انتهاء اشتراكك."}
                    {/* The forfeiture rule this sentence states lives in full on
                        /promo-terms. Linked HERE and not only at the point of
                        sale because this is the moment the rule actually costs
                        the user something, and «نهائياً» is a claim they are
                        entitled to go read. */}
                    <PromoTermsLink className="mt-2 block" />
                  </span>
                )}

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

      <AlertDialog
        open={removeCardOpen}
        onOpenChange={(next) => {
          if (!next && !removeCard.isPending) {
            setRemoveCardOpen(false);
            setRemoveCardError(null);
          }
        }}
      >
        <AlertDialogContent dir="rtl" lang="ar">
          <AlertDialogHeader>
            <AlertDialogTitle>إزالة البطاقة المحفوظة؟</AlertDialogTitle>
            <AlertDialogDescription asChild>
              {/* asChild + div: same reason as the cancel dialog — the body is
                  more than one paragraph. */}
              <div className="flex flex-col gap-2 text-start">
                {/* The consequence, stated plainly and FIRST. «لن يُجدَّد» and
                    never «سيتم إيقاف الدفع التلقائي»: the forward-looking
                    wording is true both before and after the renewal engine
                    ships, and the second phrasing asserts a live recurring
                    charge that may not exist yet. */}
                <span
                  className="text-sm font-medium text-foreground"
                  data-testid="payment-method-remove-consequence"
                >
                  لن يُجدَّد اشتراكك تلقائياً بعد إزالة البطاقة.
                </span>
                <span className="text-sm text-muted-foreground">
                  {termStillRunning && termEndsAt
                    ? `تبقى باقتك فعّالة حتى ${termEndsAt} ثم تنتقل إلى الباقة المجانية. `
                    : ""}
                  يمكنك حفظ بطاقة جديدة عند أي عملية دفع لاحقة.
                </span>
                {removeCardError && (
                  <span
                    className="text-sm text-destructive"
                    data-testid="payment-method-remove-error"
                  >
                    {removeCardError}
                  </span>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removeCard.isPending}>
              تراجع
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                // Held open while the request is in flight so the error has
                // somewhere to render — AlertDialogAction closes on click.
                e.preventDefault();
                void handleRemovePaymentMethod();
              }}
              disabled={removeCard.isPending}
              className={cn(buttonVariants({ variant: "destructive" }))}
              data-testid="payment-method-remove-confirm"
            >
              {removeCard.isPending && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              إزالة البطاقة
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <DeleteAccountDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        hasPasswordIdentity={hasPassword}
      />

      {/* Same dialog the quota block opens, minus the block: no `info`, because
          nothing has been exceeded here and a fabricated quota event would put
          invented usage numbers on screen. It reads the plan and derives the
          ladder itself. Rendered as a sibling of the settings Dialog — the
          established pattern in this file (see DeleteAccountDialog) — so it
          stacks above rather than inside it. */}
      {upgradePlans.length > 0 && (
        <QuotaUpgradeDialog
          open={upgradeOpen}
          onOpenChange={setUpgradeOpen}
          currentPlanId={subscription?.plan_id}
        />
      )}
    </>
  );
}
