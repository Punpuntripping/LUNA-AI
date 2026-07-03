"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Link2, Loader2, Plus } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api, ApiClientError } from "@/lib/api";
import { myBlogsKeys } from "@/hooks/use-my-blogs";
import type { MyBlogItem } from "@/types";

interface ImportBlogDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Phase = "form" | "saved";

/**
 * «+» paste-link import into مدوناتي (.claude/plans/blog_import.md).
 *
 * The user pastes any ``…/blog/<token>`` share URL (or a bare token); the
 * backend snapshot-copies the published post into their مدوناتي under a fresh
 * token. Idempotent: pasting a link whose root post they already hold shows
 * «موجودة لديك مسبقًا» and points at the existing entry instead of duplicating.
 */
export function ImportBlogDialog({ open, onOpenChange }: ImportBlogDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>("form");
  const [url, setUrl] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedPost, setSavedPost] = useState<MyBlogItem | null>(null);
  const [alreadySaved, setAlreadySaved] = useState(false);

  // Clean form each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setPhase("form");
    setUrl("");
    setError(null);
    setSavedPost(null);
    setAlreadySaved(false);
  }, [open]);

  async function handleImport() {
    const trimmed = url.trim();
    if (!trimmed) {
      setError("الصق رابط المدونة.");
      return;
    }

    setError(null);
    setIsImporting(true);
    try {
      const res = await api.importBlog(trimmed);
      setSavedPost(res.post);
      setAlreadySaved(res.already_saved);
      setPhase("saved");
      void queryClient.invalidateQueries({ queryKey: myBlogsKeys.all });
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.message
          : "تعذّر استيراد المدونة. تأكد من الرابط وحاول مرة أخرى.",
      );
    } finally {
      setIsImporting(false);
    }
  }

  function handleOpenSaved() {
    onOpenChange(false);
    if (savedPost) router.push(`/blogs/${savedPost.token}`);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" dir="rtl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Plus className="h-4 w-4" />
            إضافة مدونة برابط
          </DialogTitle>
          <DialogDescription>
            الصق رابط مدونة منشورة — من المدونة العامة أو رابطاً وصلك — لتُحفظ
            نسخة منها في مدوناتك.
          </DialogDescription>
        </DialogHeader>

        {phase === "form" ? (
          <div className="space-y-3">
            <label
              htmlFor="import-blog-url"
              className="block text-sm font-medium text-foreground"
            >
              رابط المدونة
            </label>

            <input
              id="import-blog-url"
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleImport();
                }
              }}
              dir="ltr"
              placeholder="https://rayhanai.com/blog/…"
              className="w-full rounded-md border border-input bg-background p-3 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />

            <p className="text-xs leading-relaxed text-muted-foreground">
              تُحفظ نسخة ثابتة في مدوناتك ويمكنك مشاركتها لاحقاً برابط جديد خاص بك.
            </p>

            {error && (
              <div className="rounded-md border border-destructive/20 bg-destructive/10 p-2.5 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="flex justify-start gap-2 pt-1">
              <Button
                type="button"
                onClick={handleImport}
                disabled={isImporting}
                className="gap-1.5"
              >
                {isImporting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Link2 className="h-4 w-4" />
                )}
                إضافة
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={isImporting}
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
              {alreadySaved ? "هذه المدونة موجودة لديك مسبقًا" : "تمت الإضافة إلى مدوناتك"}
            </div>

            <div className="flex justify-start gap-2 pt-1">
              <Button type="button" variant="outline" onClick={handleOpenSaved} className="gap-1.5">
                عرض المدونة
              </Button>
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                إغلاق
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
