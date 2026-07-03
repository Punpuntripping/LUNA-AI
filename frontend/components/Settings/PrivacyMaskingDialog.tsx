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
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { usePreferencesStore } from "@/stores/preferences-store";

interface PrivacyMaskingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * وضع السرية settings dialog. Mirrors `UsageLimitsDialog` / `RedeemCodeDialog`
 * structure. The switch is bound to `preferences-store.privacyMasking`
 * (optimistic PATCH via `setPrivacyMasking`). Turning the feature OFF is
 * gated behind an explicit confirmation step; turning it back ON is immediate.
 */
export function PrivacyMaskingDialog({
  open,
  onOpenChange,
}: PrivacyMaskingDialogProps) {
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
          dir="rtl"
          lang="ar"
          data-testid="privacy-masking-dialog"
        >
          <DialogHeader>
            <DialogTitle>وضع السرية</DialogTitle>
            <DialogDescription>
              قبل أن تغادر رسالتك خوادمنا تُستبدل المعرّفات الشخصية (أرقام الهوية،
              الجوال، الحسابات البنكية، البريد الإلكتروني) ببدائل تشبهها في الشكل،
              وتُستعاد قيمك الحقيقية تلقائيًا في الرد.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4">
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
