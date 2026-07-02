"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookmarkPlus, Check, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { api, ApiClientError } from "@/lib/api";
import { cn } from "@/lib/utils";

interface SaveAsBlogDialogProps {
  itemId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Phase = "form" | "saved";

/**
 * Quick "save this answer as a blog" dialog (حفظ كمدونة).
 *
 * A focused, title-only sibling of ``ShareArtifactDialog``. On open it fetches
 * the share draft to pre-fill the title, then «حفظ» snapshots the item into a
 * مدونة (``display_mode: "title"``) ``blog_posts`` row owned by the user with an
 * empty question. The post lands in مدوناتي; on success the dialog shows a small
 * confirmation with a link to «عرض مدوناتي».
 */
export function SaveAsBlogDialog({
  itemId,
  open,
  onOpenChange,
}: SaveAsBlogDialogProps) {
  const [phase, setPhase] = useState<Phase>("form");
  const [title, setTitle] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset to a clean form each time the dialog opens, then fetch the default
  // title. Closing leaves state alone; the next open re-fetches.
  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    setPhase("form");
    setTitle("");
    setError(null);
    setIsLoading(true);

    api
      .getShareDraft(itemId)
      .then((res) => {
        if (cancelled) return;
        // Prefer the derived title; fall back to the default question, else "".
        setTitle(res.default_title?.trim() || res.default_question?.trim() || "");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiClientError
            ? err.message
            : "تعذّر تحميل العنوان. حاول مرة أخرى.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, itemId]);

  async function handleSave() {
    const trimmed = title.trim();
    if (!trimmed) {
      setError("اكتب عنواناً.");
      return;
    }

    setError(null);
    setIsSaving(true);
    try {
      await api.shareArtifact(itemId, {
        questionText: "",
        displayMode: "title",
        title: trimmed,
      });
      setPhase("saved");
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.message
          : "تعذّر حفظ المدونة. حاول مرة أخرى.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" dir="rtl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <BookmarkPlus className="h-4 w-4" />
            حفظ كمدونة
          </DialogTitle>
          <DialogDescription>
            احفظ هذه الإجابة كمقال في مدوناتك للرجوع إليه لاحقاً.
          </DialogDescription>
        </DialogHeader>

        {phase === "form" ? (
          <div className="space-y-3">
            <label
              htmlFor="save-blog-title"
              className="block text-sm font-medium text-foreground"
            >
              عنوان المدونة
            </label>

            {isLoading ? (
              <div className="flex h-11 items-center justify-center rounded-md border border-input bg-muted/30">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <input
                id="save-blog-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                dir="rtl"
                placeholder="عنوان المقال"
                className="w-full rounded-md border border-input bg-background p-3 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            )}

            <p className="text-xs leading-relaxed text-muted-foreground">
              سيُحفظ في مدوناتك كمقال.
            </p>

            {error && (
              <div className="rounded-md border border-destructive/20 bg-destructive/10 p-2.5 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="flex justify-start gap-2 pt-1">
              <Button
                type="button"
                onClick={handleSave}
                disabled={isSaving || isLoading}
                className="gap-1.5"
              >
                {isSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <BookmarkPlus className="h-4 w-4" />
                )}
                حفظ
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={isSaving}
              >
                إلغاء
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-success text-success-fg">
                <Check className="h-4 w-4" />
              </span>
              تم الحفظ في مدوناتك
            </div>

            <div className="flex justify-start gap-2 pt-1">
              <Link
                href="/blogs"
                className={cn(
                  buttonVariants({ variant: "outline", size: "default" }),
                  "gap-1.5",
                )}
              >
                عرض مدوناتي
              </Link>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
              >
                إغلاق
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
