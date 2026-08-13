"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, ShieldCheck } from "lucide-react";
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
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { usePreferencesStore } from "@/stores/preferences-store";
import { DetailLevelToggle } from "@/components/Settings/DetailLevelToggle";

interface ConversationSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * إعدادات المحادثة — groups the per-conversation-behavior preferences in one
 * dialog: مستوى التفصيل (deep-search verbosity) and وضع السرية (identifier
 * masking). Mirrors `UsageLimitsDialog` / `RedeemCodeDialog` structure.
 *
 * The masking switch is bound to `preferences-store.privacyMasking`
 * (optimistic PATCH via `setPrivacyMasking`). Turning masking OFF is gated
 * behind an explicit confirmation step; turning it back ON is immediate.
 */
export function ConversationSettingsDialog({
  open,
  onOpenChange,
}: ConversationSettingsDialogProps) {
  const privacyMasking = usePreferencesStore((s) => s.privacyMasking);
  const isHydrated = usePreferencesStore((s) => s.isHydrated);
  const isSaving = usePreferencesStore((s) => s.isSaving);
  const error = usePreferencesStore((s) => s.error);
  const hydrate = usePreferencesStore((s) => s.hydrate);
  const setPrivacyMasking = usePreferencesStore((s) => s.setPrivacyMasking);

  const [confirmOpen, setConfirmOpen] = useState(false);

  // One-shot hydration when the dialog is first opened. `hydrate` guards
  // against double-loads internally (sets isHydrated on success + failure).
  useEffect(() => {
    if (open && !isHydrated) {
      void hydrate();
    }
  }, [open, isHydrated, hydrate]);

  const handleToggle = (next: boolean) => {
    if (next) {
      // Turning ON needs no confirmation.
      void setPrivacyMasking(true);
    } else {
      // Turning OFF requires an explicit confirmation step.
      setConfirmOpen(true);
    }
  };

  const handleConfirmDisable = () => {
    setConfirmOpen(false);
    void setPrivacyMasking(false);
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className="max-w-md"
          presentation="mobileSheet"
          dir="rtl"
          lang="ar"
          data-testid="conversation-settings-dialog"
        >
          <DialogHeader>
            <DialogTitle>إعدادات المحادثة</DialogTitle>
            <DialogDescription>
              خيارات تتحكم في طريقة إجابة ريحان في جميع محادثاتك.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            {/* مستوى التفصيل */}
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold text-foreground">
                مستوى التفصيل
              </h3>
              <DetailLevelToggle />
            </div>

            <Separator />

            {/* وضع السرية */}
            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-semibold text-foreground">
                وضع السرية
              </h3>
              <p className="text-sm text-muted-foreground">
                قبل أن تغادر رسالتك خوادمنا تُستبدل المعرّفات الشخصية (أرقام
                الهوية، الجوال، الحسابات البنكية، البريد الإلكتروني) ببدائل
                تشبهها في الشكل، وتُستعاد قيمك الحقيقية تلقائيًا في الرد.
              </p>
              <div className="flex items-center justify-between gap-3 rounded-md border border-muted-foreground/20 bg-muted/40 p-3">
                <span className="flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 shrink-0 text-primary" />
                  <span className="text-sm font-medium text-foreground">
                    تفعيل وضع السرية
                  </span>
                </span>
                <Switch
                  checked={privacyMasking}
                  onCheckedChange={handleToggle}
                  disabled={isSaving || !isHydrated}
                  aria-label="تفعيل وضع السرية"
                  data-testid="privacy-masking-switch"
                />
              </div>

              <p
                className="text-xs text-muted-foreground"
                data-testid="privacy-masking-quality-note"
              >
                قد يؤثر وضع السرية أحيانًا على جودة المخرجات؛ إذا لاحظت أرقامًا
                متضاربة في الردود يمكنك إيقافه في أي وقت.
              </p>

              {error && (
                <p
                  className="text-sm text-destructive"
                  data-testid="privacy-masking-error"
                >
                  {error}
                </p>
              )}

              <Link
                href="/masking"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline"
                data-testid="privacy-masking-read-more"
              >
                اقرأ المزيد
                <ExternalLink className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent
          dir="rtl"
          lang="ar"
          data-testid="privacy-masking-confirm"
        >
          <AlertDialogHeader>
            <AlertDialogTitle>إيقاف وضع السرية؟</AlertDialogTitle>
            <AlertDialogDescription>
              سترسل رسائلك الجديدة — في جميع المحادثات بما فيها السابقة عند
              متابعتها — بأرقامها الحقيقية إلى نماذج الذكاء الاصطناعي. ما أُرسل
              سابقًا أثناء التفعيل يبقى مقنّعًا ولا يتأثر.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="privacy-masking-confirm-cancel">
              إلغاء
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDisable}
              className={cn(buttonVariants({ variant: "destructive" }))}
              data-testid="privacy-masking-confirm-disable"
            >
              تأكيد الإيقاف
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
