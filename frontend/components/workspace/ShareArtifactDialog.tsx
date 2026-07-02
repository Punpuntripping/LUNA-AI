"use client";

import { useEffect, useState } from "react";
import {
  Check,
  Copy,
  ExternalLink,
  Loader2,
  MessageCircleQuestion,
  Newspaper,
  Share2,
} from "lucide-react";
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

interface ShareArtifactDialogProps {
  itemId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Phase = "draft" | "published";
type Template = "question" | "title";

/**
 * Publish-an-artifact dialog (مشاركة).
 *
 * On open: ``GET share-draft`` pre-fills an editable السؤال textarea. The
 * publisher can scrub PII before publishing. «نشر ونسخ الرابط» calls
 * ``POST share`` (snapshots current content + resolved refs server-side),
 * copies the returned ``public_url`` to the clipboard, and switches to a
 * success state showing the link with «نسخ» + «فتح» affordances.
 */
export function ShareArtifactDialog({
  itemId,
  open,
  onOpenChange,
}: ShareArtifactDialogProps) {
  const [phase, setPhase] = useState<Phase>("draft");
  const [template, setTemplate] = useState<Template>("question");
  const [questionText, setQuestionText] = useState("");
  const [titleText, setTitleText] = useState("");
  const [publicUrl, setPublicUrl] = useState("");

  const [isLoadingDraft, setIsLoadingDraft] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Reset to a clean draft each time the dialog opens, then fetch the default
  // question. Closing leaves state alone; the next open re-fetches.
  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    setPhase("draft");
    setTemplate("question");
    setPublicUrl("");
    setError(null);
    setCopied(false);
    setQuestionText("");
    setTitleText("");
    setIsLoadingDraft(true);

    api
      .getShareDraft(itemId)
      .then((res) => {
        if (cancelled) return;
        setQuestionText(res.default_question ?? "");
        setTitleText(res.default_title ?? "");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiClientError
            ? err.message
            : "تعذّر تحميل نص السؤال. حاول مرة أخرى.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoadingDraft(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, itemId]);

  async function copyToClipboard(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can fail on insecure contexts / denied permission — the
      // user can still select the visible link and copy by hand.
    }
  }

  async function handlePublish() {
    const question = questionText.trim();
    const title = titleText.trim();

    if (template === "title") {
      if (!title) {
        setError("اكتب عنواناً للمقال.");
        return;
      }
    } else if (!question) {
      setError("لا يمكن نشر سؤال فارغ.");
      return;
    }

    setError(null);
    setIsPublishing(true);
    try {
      const res =
        template === "title"
          ? await api.shareArtifact(itemId, {
              questionText: "",
              displayMode: "title",
              title,
            })
          : await api.shareArtifact(itemId, {
              questionText: question,
              displayMode: "question",
              title: null,
            });
      setPublicUrl(res.public_url);
      setPhase("published");
      await copyToClipboard(res.public_url);
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.message
          : "تعذّر نشر المستند. حاول مرة أخرى.",
      );
    } finally {
      setIsPublishing(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" dir="rtl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Share2 className="h-4 w-4" />
            مشاركة عبر رابط
          </DialogTitle>
          <DialogDescription>
            تُنشر نسخة ثابتة من المستند عبر رابط خاص — كصفحة سؤال وإجابة أو كمقال
            في مدوّنة. لا يمكن فتح الصفحة إلا لمن لديه الرابط الذي تشاركه — دون
            الحاجة إلى تسجيل دخول.
          </DialogDescription>
        </DialogHeader>

        {phase === "draft" ? (
          <div className="space-y-3">
            <div
              className="flex w-fit shrink-0 items-center gap-0.5 rounded-md border border-border bg-muted/30 p-0.5"
              role="tablist"
              aria-label="نوع المشاركة"
            >
              <Button
                type="button"
                variant={template === "question" ? "default" : "ghost"}
                size="sm"
                className="h-7 gap-1.5 px-3 text-xs"
                role="tab"
                aria-selected={template === "question"}
                onClick={() => setTemplate("question")}
                disabled={isPublishing}
              >
                <MessageCircleQuestion className="h-3.5 w-3.5" />
                سؤال
              </Button>
              <Button
                type="button"
                variant={template === "title" ? "default" : "ghost"}
                size="sm"
                className="h-7 gap-1.5 px-3 text-xs"
                role="tab"
                aria-selected={template === "title"}
                onClick={() => setTemplate("title")}
                disabled={isPublishing}
              >
                <Newspaper className="h-3.5 w-3.5" />
                مدونة
              </Button>
            </div>

            {template === "question" ? (
              <>
                <label
                  htmlFor="share-question"
                  className="block text-sm font-medium text-foreground"
                >
                  السؤال
                </label>

                {isLoadingDraft ? (
                  <div className="flex h-28 items-center justify-center rounded-md border border-input bg-muted/30">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <textarea
                    id="share-question"
                    value={questionText}
                    onChange={(e) => setQuestionText(e.target.value)}
                    rows={4}
                    dir="rtl"
                    placeholder="اكتب السؤال الذي سيظهر على الصفحة العامة..."
                    className="w-full resize-y rounded-md border border-input bg-background p-3 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                )}
              </>
            ) : (
              <>
                <label
                  htmlFor="share-title"
                  className="block text-sm font-medium text-foreground"
                >
                  عنوان المقال
                </label>

                {isLoadingDraft ? (
                  <div className="flex h-11 items-center justify-center rounded-md border border-input bg-muted/30">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <input
                    id="share-title"
                    type="text"
                    value={titleText}
                    onChange={(e) => setTitleText(e.target.value)}
                    dir="rtl"
                    placeholder="عنوان المقال"
                    className="w-full rounded-md border border-input bg-background p-3 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                )}

                <p className="text-xs leading-relaxed text-muted-foreground">
                  سيظهر كعنوان مقال في منتصف الصفحة بتنسيق مدوّنة.
                </p>
              </>
            )}

            <p className="text-xs leading-relaxed text-muted-foreground">
              لإخفاء معلومات حساسة، عدّل النص هنا أو حرّر المستند قبل النشر.
            </p>

            {error && (
              <div className="rounded-md border border-destructive/20 bg-destructive/10 p-2.5 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="flex justify-start gap-2 pt-1">
              <Button
                type="button"
                onClick={handlePublish}
                disabled={isPublishing || isLoadingDraft}
                className="gap-1.5"
              >
                {isPublishing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Share2 className="h-4 w-4" />
                )}
                نشر ونسخ الرابط
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={isPublishing}
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
              تم النشر — الرابط في الحافظة
            </div>

            <div className="flex items-center gap-1.5 rounded-md border border-input bg-muted/40 p-2">
              <input
                type="text"
                readOnly
                value={publicUrl}
                dir="ltr"
                onFocus={(e) => e.currentTarget.select()}
                className="flex-1 select-all bg-transparent px-1 text-xs text-foreground focus:outline-none"
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-7 shrink-0 gap-1.5 px-2 text-[11px]"
                onClick={() => copyToClipboard(publicUrl)}
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3" />
                    تم النسخ
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" />
                    نسخ
                  </>
                )}
              </Button>
            </div>

            <div className="flex justify-start gap-2 pt-1">
              <a
                href={publicUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  buttonVariants({ variant: "outline", size: "default" }),
                  "gap-1.5",
                )}
              >
                <ExternalLink className="h-4 w-4" />
                فتح
              </a>
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
