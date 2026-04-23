"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { MessageSquare, MoreHorizontal, Pencil, Trash2, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { getRelativeTimeAr } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useDeleteConversation, useRenameConversation } from "@/hooks/use-conversations";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
}

export function ConversationItem({ conversation, searchQuery = "" }: ConversationItemProps) {
  const router = useRouter();
  const { selectedConversationId, setSelectedConversation } = useSidebarStore();
  const deleteConversation = useDeleteConversation();
  const renameConversation = useRenameConversation();

  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const isActive = selectedConversationId === conversation.conversation_id;
  const title = conversation.title_ar || "محادثة جديدة";

  useEffect(() => {
    if (isRenaming && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isRenaming]);

  const handleClick = () => {
    if (isRenaming) return;
    setSelectedConversation(conversation.conversation_id);
    router.push(`/chat/${conversation.conversation_id}`);
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
          "group rounded-md border px-3 py-2.5 text-sm cursor-pointer transition-colors",
          isActive
            ? "border-primary/30 bg-accent text-accent-foreground"
            : "border-border/50 bg-background/50 text-sidebar-foreground hover:bg-accent/50"
        )}
        onClick={handleClick}
      >
        {isRenaming ? (
          <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
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
            {/* Top row: icon + title + actions */}
            <div className="flex items-center gap-2">
              <MessageSquare className="h-4 w-4 shrink-0 opacity-60" />
              <p className="flex-1 min-w-0 text-sm font-medium line-clamp-2">
                {searchQuery ? (
                  <HighlightedText text={title} highlight={searchQuery} />
                ) : (
                  title
                )}
              </p>

              {/* Actions dropdown — visible on hover */}
              <div
                className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                onClick={(e) => e.stopPropagation()}
              >
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-6 w-6">
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-40">
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
            </div>

            {/* Bottom row: timestamp */}
            <p className="text-xs text-muted-foreground mt-1">
              {getRelativeTimeAr(conversation.updated_at)}
            </p>
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
