"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  ExternalLink,
  Globe,
  Loader2,
  Lock,
  Trash2,
} from "lucide-react";
import { api, ApiClientError } from "@/lib/api";
import { useMyBlogs, myBlogsKeys } from "@/hooks/use-my-blogs";
import { Button, buttonVariants } from "@/components/ui/button";
import { BlogArticleView } from "@/components/blog/BlogArticleView";
import { PublicAnswerView } from "@/components/blog/PublicAnswerView";
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
import { cn } from "@/lib/utils";
import type { BlogPostPublic, MyBlogsResponse } from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchPost(token: string): Promise<BlogPostPublic> {
  const res = await fetch(
    `${API_BASE}/api/v1/public/blog/${encodeURIComponent(token)}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error("not_found");
  return (await res.json()) as BlogPostPublic;
}

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function MyBlogPostPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token;
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    data: post,
    isLoading: isPostLoading,
    isError: isPostError,
  } = useQuery({
    queryKey: ["public-blog", token],
    queryFn: () => fetchPost(token!),
    enabled: !!token,
  });

  // Owner-side metadata: which of MY posts this token maps to + the curate gate.
  const { data: myBlogs } = useMyBlogs();
  const item = myBlogs?.posts.find((p) => p.token === token);
  const canPublishPublic = myBlogs?.can_publish_public ?? false;

  const [copied, setCopied] = useState(false);
  const [isToggling, setIsToggling] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const publicUrl =
    typeof window !== "undefined" && token
      ? `${window.location.origin}/blog/${token}`
      : "";

  async function handleCopyLink() {
    if (!publicUrl) return;
    try {
      await navigator.clipboard.writeText(publicUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can fail on insecure contexts / denied permission — the user
      // can still open the public page and copy the URL by hand.
    }
  }

  // Optimistically flip is_public in the مدوناتي cache, then refetch to confirm.
  function patchMyBlogsPublic(postId: string, isPublic: boolean) {
    queryClient.setQueryData<MyBlogsResponse>(myBlogsKeys.list(), (prev) =>
      prev
        ? {
            ...prev,
            posts: prev.posts.map((p) =>
              p.post_id === postId ? { ...p, is_public: isPublic } : p,
            ),
          }
        : prev,
    );
  }

  async function handleTogglePublic() {
    if (!item) return;
    const next = !item.is_public;
    setActionError(null);
    setIsToggling(true);
    // Optimistic flip
    patchMyBlogsPublic(item.post_id, next);
    try {
      if (next) {
        await api.publishBlogPublic(item.post_id);
      } else {
        await api.unpublishBlogPublic(item.post_id);
      }
      void queryClient.invalidateQueries({ queryKey: myBlogsKeys.list() });
    } catch (err) {
      // Roll back the optimistic flip on failure.
      patchMyBlogsPublic(item.post_id, item.is_public);
      setActionError(
        err instanceof ApiClientError
          ? err.message
          : "تعذّر تحديث حالة النشر. حاول مرة أخرى.",
      );
    } finally {
      setIsToggling(false);
    }
  }

  async function handleConfirmDelete() {
    if (!item) return;
    setActionError(null);
    setIsDeleting(true);
    try {
      await api.unpublishPost(item.post_id);
      queryClient.setQueryData<MyBlogsResponse>(myBlogsKeys.list(), (prev) =>
        prev
          ? {
              ...prev,
              posts: prev.posts.filter((p) => p.post_id !== item.post_id),
            }
          : prev,
      );
      void queryClient.invalidateQueries({ queryKey: myBlogsKeys.list() });
      router.push("/blogs");
    } catch (err) {
      setActionError(
        err instanceof ApiClientError
          ? err.message
          : "تعذّر حذف المدونة. حاول مرة أخرى.",
      );
      setIsDeleting(false);
      setShowDeleteDialog(false);
    }
  }

  if (isPostLoading) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isPostError || !post) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm font-medium text-foreground">
          لم يتم العثور على هذه المدونة
        </p>
        <p className="text-sm text-muted-foreground">
          قد تكون محذوفة أو أن الرابط غير صحيح.
        </p>
        <Button variant="outline" onClick={() => router.push("/blogs")}>
          العودة إلى مدوناتي
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col min-h-0 overflow-hidden" dir="rtl">
      {/* Management toolbar */}
      <div className="shrink-0 border-b bg-background/80 px-4 py-2.5 backdrop-blur">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={handleCopyLink}
            className="h-8 gap-1.5 px-2.5 text-xs"
          >
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5" />
                تم النسخ
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" />
                نسخ الرابط
              </>
            )}
          </Button>

          <a
            href={publicUrl || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "h-8 gap-1.5 px-2.5 text-xs",
            )}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            فتح الصفحة العامة
          </a>

          {/* Publish toggle — only when the user can curate the public gallery */}
          {canPublishPublic && item && (
            <Button
              type="button"
              variant={item.is_public ? "outline" : "default"}
              size="sm"
              onClick={handleTogglePublic}
              disabled={isToggling}
              className="h-8 gap-1.5 px-2.5 text-xs"
            >
              {isToggling ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : item.is_public ? (
                <Lock className="h-3.5 w-3.5" />
              ) : (
                <Globe className="h-3.5 w-3.5" />
              )}
              {item.is_public ? "إلغاء النشر من المدونة" : "نشر في المدونة العامة"}
            </Button>
          )}

          <div className="ms-auto flex items-center gap-2">
            {item && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setShowDeleteDialog(true)}
                disabled={isDeleting}
                className="h-8 gap-1.5 px-2.5 text-xs text-destructive hover:text-destructive"
              >
                {isDeleting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                حذف
              </Button>
            )}
          </div>
        </div>

        {actionError && (
          <div className="mx-auto mt-2 w-full max-w-5xl rounded-md border border-destructive/20 bg-destructive/10 p-2 text-xs text-destructive">
            {actionError}
          </div>
        )}
      </div>

      {/* Post body — reuses the public reading surfaces (incl. BlogPageShell) */}
      <div className="flex-1 overflow-y-auto">
        {post.display_mode === "title" ? (
          <BlogArticleView post={post} blogToken={token!} />
        ) : (
          <PublicAnswerView post={post} blogToken={token!} />
        )}
      </div>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent dir="rtl">
          <AlertDialogHeader>
            <AlertDialogTitle>حذف المدونة</AlertDialogTitle>
            <AlertDialogDescription>
              هل أنت متأكد من حذف هذه المدونة؟ سيتوقف الرابط العام عن العمل ولا
              يمكن التراجع.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>إلغاء</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              حذف
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
