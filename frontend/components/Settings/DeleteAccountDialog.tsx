"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Eye, EyeOff, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ApiClientError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";

/** Type-to-confirm phrase for accounts with no password identity (Google-only). */
const CONFIRM_PHRASE = "حذف حسابي";

interface DeleteAccountDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The account holds a password (server-resolved `user.has_password`,
   *  migration 141) → confirm by re-entering it; otherwise type-to-confirm.
   *  Display-only: the backend re-checks and 422s if it disagrees, which the
   *  handler below recovers from by flipping to the password field. */
  hasPasswordIdentity: boolean;
}

export function DeleteAccountDialog({
  open,
  onOpenChange,
  hasPasswordIdentity,
}: DeleteAccountDialogProps) {
  const router = useRouter();
  const deleteAccount = useAuthStore((s) => s.deleteAccount);

  const [password, setPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  // The server owns the branch. If it rejects a password-less delete (422),
  // flip to the password variant instead of dead-ending on a stale client-side
  // provider read.
  const [passwordRequired, setPasswordRequired] = useState(false);

  const needsPassword = hasPasswordIdentity || passwordRequired;
  const canConfirm =
    !isDeleting &&
    (needsPassword ? password.length > 0 : phrase.trim() === CONFIRM_PHRASE);

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setPassword("");
      setPhrase("");
      setShowPassword(false);
      setError(null);
      setPasswordRequired(false);
    }
    onOpenChange(next);
  };

  const handleConfirm = async () => {
    if (!canConfirm) return;
    setError(null);
    setIsDeleting(true);
    try {
      await deleteAccount(needsPassword ? password : undefined);
      router.push("/login");
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) {
          setError("كلمة المرور غير صحيحة");
        } else if (err.status === 422) {
          setPasswordRequired(true);
          setError("أدخل كلمة المرور لتأكيد حذف الحساب");
        } else if (err.status === 429) {
          setError("تم تجاوز الحد المسموح من المحاولات. حاول بعد قليل.");
        } else {
          setError(err.message || "تعذّر حذف الحساب. حاول مجددًا.");
        }
      } else {
        setError("تعذّر حذف الحساب. حاول مجددًا.");
      }
      setIsDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="max-h-[85dvh] max-w-md overflow-y-auto"
        presentation="mobileSheet"
        dir="rtl"
        lang="ar"
        data-testid="delete-account-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5 shrink-0" />
            حذف الحساب
          </DialogTitle>
          <DialogDescription className="text-start leading-relaxed">
            سيتم إلغاء تنشيط حسابك فورًا وحذفه نهائيًا بعد ٣٠ يومًا، بما في ذلك
            جميع القضايا والمحادثات والمستندات. يمكنك استعادة الحساب خلال هذه
            الفترة بتسجيل الدخول مجددًا.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2">
          {needsPassword ? (
            <>
              <label
                htmlFor="delete-account-password"
                className="block text-sm font-medium text-foreground"
              >
                أدخل كلمة المرور للتأكيد
              </label>
              <div className="relative">
                <input
                  id="delete-account-password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleConfirm();
                  }}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  dir="ltr"
                  data-testid="delete-account-password"
                  className="w-full rounded-md border border-input bg-background px-3 py-2 pe-10 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  className="absolute end-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground transition-colors hover:text-foreground"
                  tabIndex={-1}
                  aria-label={
                    showPassword ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"
                  }
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </>
          ) : (
            <>
              <label
                htmlFor="delete-account-confirm-phrase"
                className="block text-sm font-medium text-foreground"
              >
                اكتب «{CONFIRM_PHRASE}» للتأكيد
              </label>
              <input
                id="delete-account-confirm-phrase"
                type="text"
                value={phrase}
                onChange={(e) => setPhrase(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleConfirm();
                }}
                placeholder={CONFIRM_PHRASE}
                autoComplete="off"
                dir="rtl"
                data-testid="delete-account-confirm-phrase"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground focus:border-transparent focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </>
          )}

          {error && (
            <p className="text-sm text-destructive" data-testid="delete-account-error">
              {error}
            </p>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isDeleting}
          >
            إلغاء
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={!canConfirm}
            data-testid="delete-account-confirm"
          >
            {isDeleting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isDeleting ? "جارٍ الحذف…" : "حذف الحساب"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
