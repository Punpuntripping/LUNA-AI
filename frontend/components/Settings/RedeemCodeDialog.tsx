"use client";

import { useState } from "react";
import { CheckCircle2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useRedeemCode } from "@/hooks/use-redeem-code";

interface RedeemCodeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Normalize as the user types: uppercase, drop anything that isn't a base32
 *  character, cap length. Mirrors the server-side normalization so what the
 *  user sees is exactly what gets sent. */
function normalizeCode(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, 12);
}

export function RedeemCodeDialog({ open, onOpenChange }: RedeemCodeDialogProps) {
  const [code, setCode] = useState("");
  const redeem = useRedeemCode();

  const canSubmit = code.length >= 3 && !redeem.isPending;

  const handleSubmit = () => {
    if (!canSubmit) return;
    redeem.mutate(code);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      // Reset on close so the next open starts clean.
      setCode("");
      redeem.reset();
    }
    onOpenChange(next);
  };

  const success = redeem.isSuccess ? redeem.data : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="max-w-sm"
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

        {success ? (
          <div className="flex items-start gap-3 rounded-md border border-success-fg/25 bg-success p-4">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success-fg" />
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium text-success-fg">
                تم تفعيل باقتك بنجاح
              </p>
              <p className="text-xs text-success-fg/80">
                باقتك الحالية: {success.name_ar ?? success.plan_id}
              </p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <input
              type="text"
              inputMode="text"
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              value={code}
              onChange={(e) => setCode(normalizeCode(e.target.value))}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSubmit();
              }}
              placeholder="مثال: K7P2M"
              autoFocus
              dir="ltr"
              data-testid="redeem-code-input"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-center font-mono text-lg uppercase tracking-[0.3em] ring-offset-background outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 placeholder:text-sm placeholder:tracking-normal placeholder:text-muted-foreground"
            />

            {redeem.isError && (
              <p
                className="text-sm text-destructive"
                data-testid="redeem-code-error"
              >
                {redeem.error?.message || "تعذّر تفعيل الرمز. حاول مجددًا."}
              </p>
            )}

            <Button
              onClick={handleSubmit}
              disabled={!canSubmit}
              className="w-full"
              data-testid="redeem-code-submit"
            >
              {redeem.isPending ? "جارٍ التفعيل…" : "تفعيل"}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
