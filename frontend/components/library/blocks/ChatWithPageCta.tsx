"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, MessageCircle, MessageSquarePlus } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { ApiClientError } from "@/lib/api";
import {
  useConversations,
  useCreateConversation,
} from "@/hooks/use-conversations";
import { isDemoConversation } from "@/hooks/use-demo-conversation";
import { useAuthStore } from "@/stores/auth-store";
import { useChatStore } from "@/stores/chat-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { setPendingIntent } from "@/lib/post-login-intent";
import type { LibraryItemPageType } from "@/types";
import type { LibraryPageType } from "@/types/library";

/**
 * The page types that can actually be carried into a conversation
 * (`.claude/plans/simple_search_family.md` §8, §12a C3).
 *
 * `fetch_grounding` has no grounder for `circular` / `form` / `calculator` /
 * `topic`, and there is no `/services` route at all, so the backend answers
 * those with an Arabic error. A CTA that produces an error is worse than no
 * CTA: on those page types this component degrades to the plain «افتح محادثة
 * مع ريحان» link it replaced, which still works — it just carries nothing.
 *
 * The `is` predicate is what pins `LibraryItemPageType ⊆ LibraryPageType` at
 * compile time: widen either union out of step and this stops type-checking.
 */
const CARRYABLE_PAGE_TYPES: readonly LibraryItemPageType[] = [
  "regulation",
  "article",
  "judgment",
  "blog",
];

export function isCarryablePageType(
  pageType: LibraryPageType,
): pageType is LibraryItemPageType {
  return (CARRYABLE_PAGE_TYPES as readonly string[]).includes(pageType);
}

/** «هذا النظام» / «هذه المادة» / «هذا الحكم» — the object, named. */
const DEFINITE_PAGE_NOUN: Record<LibraryItemPageType, string> = {
  regulation: "هذا النظام",
  article: "هذه المادة",
  judgment: "هذا الحكم",
  blog: "هذه المدونة",
};

interface ChatWithPageCtaProps {
  pageType: LibraryPageType;
  /** Public slug; for an `article` the composite `{reg_slug}/{article_slug}`. */
  pageId: string;
  pageTitle: string;
  /** `/login?…` target carrying the page context, built by the widget. */
  loginHref: string;
  className?: string;
}

/**
 * «تحدّث مع ريحان عن هذه الصفحة» — the CTA that actually carries the document
 * (`.claude/plans/simple_search_family.md` §8, Case B).
 *
 *   Authed      → destination picker → the page is stashed in the chat-store
 *                 carry slot and the destination `ChatInput` POSTs it to
 *                 `/conversations/{id}/library-items`, showing it as a composer
 *                 chip. On send the returned `item_id` rides the EXISTING
 *                 `attachment_ids` array — no send-payload change.
 *   Anon        → `chat_with_library_item` post-login intent → /login; the
 *                 AuthGuard consumer resumes the identical flow after sign-in.
 *   Uncarryable → the plain `/chat` link, unchanged. Never a broken button.
 */
export function ChatWithPageCta({
  pageType,
  pageId,
  pageTitle,
  loginHref,
  className,
}: ChatWithPageCtaProps) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [pickerOpen, setPickerOpen] = useState(false);

  // Graceful degradation: no grounder behind this page type ⇒ keep the old,
  // honest affordance rather than a button that 400s in Arabic.
  if (!isCarryablePageType(pageType)) {
    return (
      <Link
        href="/chat"
        className={cn(buttonVariants({ size: "default" }), "w-full", className)}
      >
        <MessageCircle aria-hidden="true" className="h-4 w-4" />
        افتح محادثة مع ريحان
      </Link>
    );
  }

  const carried: LibraryItemPageType = pageType;

  function handleClick(): void {
    if (!isAuthenticated) {
      setPendingIntent({
        type: "chat_with_library_item",
        page_type: carried,
        page_id: pageId,
        title: pageTitle || null,
      });
      // `loginHref` already carries the page context querystring the widget
      // builds; the intent above is what actually resumes the flow.
      router.push(loginHref);
      return;
    }
    setPickerOpen(true);
  }

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        className={cn(buttonVariants({ size: "default" }), "w-full", className)}
      >
        <MessageCircle aria-hidden="true" className="h-4 w-4" />
        تحدّث مع ريحان عن {DEFINITE_PAGE_NOUN[carried]}
      </button>

      {/* Mounted only while open: the picker lists the caller's conversations,
          and this component lives on PUBLIC pages where that query would 401
          for every anonymous reader. */}
      {pickerOpen && (
        <ChatWithPageDialog
          pageType={carried}
          pageId={pageId}
          pageTitle={pageTitle}
          open={pickerOpen}
          onOpenChange={setPickerOpen}
        />
      )}
    </>
  );
}

interface ChatWithPageDialogProps {
  pageType: LibraryItemPageType;
  pageId: string;
  pageTitle: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Destination picker — «محادثة جديدة» or one of the caller's recent
 * conversations. The `BlogDestinationDialog` shape (`.claude/plans/blog_import.md`
 * §D4) with ONE deliberate difference: it does not POST here.
 *
 * The blog dialog imports and then navigates, which leaves the note in the
 * workspace pane but attaches it to no message. A carried library page has to
 * reach the agent on the NEXT turn, and the only thing that does that is a
 * composer chip. So the page is stashed in `pendingLibraryRefs` and the
 * destination `ChatInput`'s drain effect performs the POST — the same slot the
 * new-chat and post-login paths use, so all three land in one code path and
 * report their errors on the chip.
 */
function ChatWithPageDialog({
  pageType,
  pageId,
  pageTitle,
  open,
  onOpenChange,
}: ChatWithPageDialogProps) {
  const router = useRouter();
  const { data: convData, isLoading: isListLoading } = useConversations(null);
  const createConversation = useCreateConversation();

  // "new" while creating a fresh conversation, else the target conversation_id.
  const [busyTarget, setBusyTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const conversations = (convData?.conversations ?? []).filter(
    (c) =>
      // Optimistic placeholders from useCreateConversation have synthetic ids.
      !c.conversation_id.startsWith("optimistic-") &&
      // The shared محادثة تجريبية is read-only for everyone and its composer is
      // replaced by a hint bar — carrying a page there would write into
      // everybody's row and then have nowhere to send it from.
      !isDemoConversation(c),
  );

  function carryInto(conversationId: string): void {
    const store = useChatStore.getState();
    store.setPendingLibraryRefs([
      ...store.pendingLibraryRefs,
      { pageType, pageId, title: pageTitle || null },
    ]);
    useSidebarStore.getState().setSelectedConversation(conversationId);
    onOpenChange(false);
    router.push(`/chat/${conversationId}`);
  }

  async function handlePick(target: "new" | string): Promise<void> {
    if (busyTarget) return;
    setError(null);
    setBusyTarget(target);
    try {
      const conversationId =
        target === "new"
          ? (await createConversation.mutateAsync({})).conversation
              .conversation_id
          : target;
      carryInto(conversationId);
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.message
          : "تعذّر فتح المحادثة. حاول مرة أخرى.",
      );
      setBusyTarget(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !busyTarget && onOpenChange(o)}>
      <DialogContent className="max-w-md" dir="rtl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <MessageSquarePlus aria-hidden="true" className="h-4 w-4" />
            تحدّث مع ريحان عن هذه الصفحة
          </DialogTitle>
          <DialogDescription>
            تُضاف «{pageTitle}» إلى مساحة عمل المحادثة ليعتمد عليها ريحان في
            إجابته.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Button
            type="button"
            onClick={() => handlePick("new")}
            disabled={!!busyTarget}
            className="w-full gap-1.5"
          >
            {busyTarget === "new" ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : (
              <MessageSquarePlus aria-hidden="true" className="h-4 w-4" />
            )}
            محادثة جديدة
          </Button>

          {(isListLoading || conversations.length > 0) && (
            <>
              <p className="text-xs font-medium text-muted-foreground">
                أو أضفها إلى محادثة موجودة:
              </p>

              {isListLoading ? (
                <div className="flex h-16 items-center justify-center rounded-md border bg-muted/30">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <ScrollArea className="max-h-56 rounded-md border">
                  <div className="p-1">
                    {conversations.map((conv) => (
                      <button
                        key={conv.conversation_id}
                        type="button"
                        onClick={() => handlePick(conv.conversation_id)}
                        disabled={!!busyTarget}
                        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-start text-sm text-foreground transition-colors hover:bg-accent/50 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {busyTarget === conv.conversation_id ? (
                          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
                        ) : null}
                        <span className="truncate">
                          {(conv.title_ar ?? "").trim() || "محادثة"}
                        </span>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </>
          )}

          {error && (
            <div className="rounded-md border border-destructive/20 bg-destructive/10 p-2.5 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
