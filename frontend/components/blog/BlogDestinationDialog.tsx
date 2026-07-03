"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquarePlus } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api, ApiClientError } from "@/lib/api";
import { useConversations, useCreateConversation } from "@/hooks/use-conversations";
import { workspaceKeys } from "@/hooks/use-workspace";
import { useSidebarStore } from "@/stores/sidebar-store";

interface BlogDestinationDialogProps {
  /** Blog share token to copy into the chosen conversation. */
  token: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * «اتحدث مع المدونة» destination picker (.claude/plans/blog_import.md §D4).
 *
 * New conversation (primary) or one of the caller's recent conversations. On
 * pick: the blog snapshot is imported into the conversation as a ``kind=note``
 * workspace item (server-side, idempotent per root post), then we navigate to
 * the chat. Only mounted for authenticated users (the button gates).
 */
export function BlogDestinationDialog({
  token,
  open,
  onOpenChange,
}: BlogDestinationDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: convData, isLoading: isListLoading } = useConversations(null);
  const createConversation = useCreateConversation();

  // "new" while creating a fresh conversation, else the target conversation_id.
  const [busyTarget, setBusyTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const conversations = (convData?.conversations ?? []).filter(
    // Optimistic placeholders from useCreateConversation have synthetic ids.
    (c) => !c.conversation_id.startsWith("optimistic-"),
  );

  async function importInto(conversationId: string) {
    await api.createBlogItem(conversationId, token);
    void queryClient.invalidateQueries({
      queryKey: workspaceKeys.byConversation(conversationId),
    });
    useSidebarStore.getState().setSelectedConversation(conversationId);
    onOpenChange(false);
    router.push(`/chat/${conversationId}`);
  }

  async function handlePick(target: "new" | string) {
    if (busyTarget) return;
    setError(null);
    setBusyTarget(target);
    try {
      const conversationId =
        target === "new"
          ? (await createConversation.mutateAsync({})).conversation.conversation_id
          : target;
      await importInto(conversationId);
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
            <MessageSquarePlus className="h-4 w-4" />
            اتحدث مع المدونة
          </DialogTitle>
          <DialogDescription>
            تُضاف نسخة من المدونة إلى مساحة عمل المحادثة ليعتمد عليها ريحان في
            إجاباته.
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
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <MessageSquarePlus className="h-4 w-4" />
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
