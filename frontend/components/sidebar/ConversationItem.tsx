"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { MoreVertical, Pencil, Trash2, Check, X, Star, StarOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import {
  useDeleteConversation,
  useRenameConversation,
  useStarConversation,
} from "@/hooks/use-conversations";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { Button } from "@/components/ui/button";
import { HighlightedText } from "@/components/sidebar/HighlightedText";
import type { ConversationSummary } from "@/types";

interface ConversationItemProps {
  conversation: ConversationSummary;
  searchQuery?: string;
  /** Keep the 3-dots actions always visible (list view) instead of hover-only. */
  alwaysShowActions?: boolean;
}

export function ConversationItem({
  conversation,
  searchQuery = "",
  alwaysShowActions = false,
}: ConversationItemProps) {
  const router = useRouter();
  const { selectedConversationId, setSelectedConversation } = useSidebarStore();
  const deleteConversation = useDeleteConversation();
  const renameConversation = useRenameConversation();
  const starConversation = useStarConversation();

  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const isActive = selectedConversationId === conversation.conversation_id;
  const title = conversation.title_ar || "محادثة جديدة";
  const isStarred = conversation.is_starred;
  // Show the dimmed snippet line only on search results where the match was in
  // a message body — never alter the compact sidebar look (no searchQuery → no
  // snippet rendered).
  const snippet =
    searchQuery && conversation.match_type === "message"
      ? conversation.snippet
      : null;
  // Optimistic placeholder rows carry an ``optimistic-<timestamp>`` id while
  // the create-conversation POST is in flight. Navigating to that id leaks
  // it into every downstream API call (messages, workspace, …) and breaks
  // the chat with a 5-retry "فشل الاتصال" loop. Disable clicks until the
  // real UUID arrives via the list invalidate.
  const isPendingCreate = conversation.conversation_id.startsWith("optimistic-");

  useEffect(() => {
    if (isRenaming && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isRenaming]);

  const handleClick = () => {
    if (isRenaming) return;
    if (isPendingCreate) return;
    setSelectedConversation(conversation.conversation_id);
    router.push(`/chat/${conversation.conversation_id}`);
  };

  const handleToggleStar = () => {
    starConversation.mutate({
      id: conversation.conversation_id,
      starred: !isStarred,
    });
  };

  const handleStartRename = () => {
    setRenameValue(conversation.title_ar || "");
    setIsRenaming(true);
  };

  const handleConfirmRename = () => {
    if (renameValue.trim()) {
      renameConversation.mutate({
        id: conversation.conversation_id,
        title_ar: renameValue.trim(),
      });
    }
    setIsRenaming(false);
  };

  const handleCancelRename = () => {
    setIsRenaming(false);
    setRenameValue("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleConfirmRename();
    } else if (e.key === "Escape") {
      handleCancelRename();
    }
  };

  const handleConfirmDelete = () => {
    deleteConversation.mutate(conversation.conversation_id, {
      onSuccess: () => {
        if (isActive) {
          setSelectedConversation(null);
          router.push("/chat");
        }
      },
    });
    setShowDeleteDialog(false);
  };

  return (
    <>
      <div
        className={cn(
          "group flex items-center gap-2 rounded-md px-3 py-2 transition-colors",
          isPendingCreate
            ? "cursor-wait opacity-60"
            : "cursor-pointer",
          isActive
            ? "bg-accent text-accent-foreground"
            : "text-sidebar-foreground/85 hover:bg-accent/40 hover:text-foreground"
        )}
        title={isPendingCreate ? "جارٍ إنشاء المحادثة…" : undefined}
        onClick={handleClick}
      >
        {isRenaming ? (
          <div className="flex flex-1 items-center gap-1" onClick={(e) => e.stopPropagation()}>
            <input
              ref={inputRef}
              type="text"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={handleKeyDown}
              className="flex-1 bg-background border border-input rounded px-1.5 py-0.5 text-xs text-foreground outline-none focus:ring-1 focus:ring-ring"
              dir="rtl"
            />
            <button
              onClick={handleConfirmRename}
              className="p-0.5 text-green-600 hover:text-green-700"
            >
              <Check className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={handleCancelRename}
              className="p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <>
            <div className="flex flex-1 min-w-0 flex-col gap-0.5">
              <div className="flex min-w-0 items-center gap-1.5">
                {isStarred && (
                  <Star className="h-3 w-3 shrink-0 fill-amber-400 text-amber-400" />
                )}
                <p
                  className="min-w-0 flex-1 truncate text-sm max-w-[15rem]"
                  title={title}
                >
                  {searchQuery ? (
                    <HighlightedText text={title} highlight={searchQuery} />
                  ) : (
                    title
                  )}
                </p>
              </div>

              {snippet && (
                <p className="min-w-0 truncate text-xs text-muted-foreground/70">
                  <HighlightedText text={snippet} highlight={searchQuery} />
                </p>
              )}
            </div>

            <div
              className={cn(
                "transition-opacity shrink-0 self-start",
                alwaysShowActions
                  ? "opacity-100"
                  : "opacity-0 group-hover:opacity-100",
              )}
              onClick={(e) => e.stopPropagation()}
            >
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-6 w-6">
                    <MoreVertical className="h-3.5 w-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-40">
                  <DropdownMenuItem onClick={handleToggleStar}>
                    {isStarred ? (
                      <>
                        <StarOff className="h-3.5 w-3.5 me-2" />
                        إزالة التمييز
                      </>
                    ) : (
                      <>
                        <Star className="h-3.5 w-3.5 me-2" />
                        تمييز بنجمة
                      </>
                    )}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleStartRename}>
                    <Pencil className="h-3.5 w-3.5 me-2" />
                    إعادة تسمية
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => setShowDeleteDialog(true)}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5 me-2" />
                    حذف
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </>
        )}
      </div>

      {/* Delete confirmation dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>حذف المحادثة</AlertDialogTitle>
            <AlertDialogDescription>
              هل أنت متأكد من حذف هذه المحادثة؟
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
    </>
  );
}
