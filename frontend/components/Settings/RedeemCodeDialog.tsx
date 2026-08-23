"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { RedeemCodeForm } from "@/components/Settings/RedeemCodeForm";

interface RedeemCodeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * «تفعيل برمز» — the settings-popover entry point, opened on demand from
 * `SidebarFooter`.
 *
 * The field, the normalization and the error copy live in `RedeemCodeForm`,
 * shared with the two-week promo popup (`components/promo/PromoCodePopup`).
 * `DialogContent` unmounts on close, so the form resets itself and no explicit
 * teardown is needed here.
 */
export function RedeemCodeDialog({ open, onOpenChange }: RedeemCodeDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-sm"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="redeem-code-dialog"
      >
        <DialogHeader>
          <DialogTitle>تفعيل برمز</DialogTitle>
          <DialogDescription>
            أدخل رمز التفعيل الذي حصلت عليه لتفعيل باقتك.
          </DialogDescription>
        </DialogHeader>

        <RedeemCodeForm />
      </DialogContent>
    </Dialog>
  );
}
