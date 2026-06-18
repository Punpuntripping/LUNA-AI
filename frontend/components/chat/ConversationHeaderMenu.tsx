"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { MoreVertical, Pencil, Trash2, Star, StarOff } from "lucide-react";
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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

interface ConversationHeaderMenuProps {
  conversationId: string;
  /** Current title (used to pre-fill the rename dialog). */
  title: string;
  isStarred: boolean;
}

/**
 * A 3-dots (⋯) actions menu for the OPEN conversation, shown in the main chat
 * header. Opens Star / Rename / Delete. Rename uses a dialog; delete navigates
 * back to the empty composer since the conversation being deleted is the one on
 * screen. No title is rendered here (the header shows its own label).
 */
export function ConversationHeaderMenu({
  conversationId,
  title,
  isStarred,
}: ConversationHeaderMenuProps) {
  const router = useRouter();
  const { setSelectedConversation } = useSidebarStore();
  const starConversation = useStarConversation();
  const renameConversation = useRenameConversation();
  const deleteConversation = useDeleteConversation();

  const [showRenameDialog, setShowRenameDialog] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (showRenameDialog && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [showRenameDialog]);

  const handleToggleStar = () => {
    starConversation.mutate({ id: conversationId, starred: !isStarred });
  };

  const handleStartRename = () => {
    setRenameValue(title);
    setShowRenameDialog(true);
  };

  const handleConfirmRename = () => {
    const next = renameValue.trim();
    if (next && next !== title) {
      renameConversation.mutate({ id: conversationId, title_ar: next });
    }
    setShowRenameDialog(false);
  };

  const handleConfirmDelete = () => {
    deleteConversation.mutate(conversationId, {
      onSuccess: () => {
        setSelectedConversation(null);
        router.push("/chat");
      },
    });
    setShowDeleteDialog(false);
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-muted-foreground hover:text-foreground"
            aria-label="خيارات المحادثة"
          >
            <MoreVertical className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
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

      {/* Rename dialog */}
      <Dialog open={showRenameDialog} onOpenChange={setShowRenameDialog}>
        <DialogContent dir="rtl">
          <DialogHeader>
            <DialogTitle>إعادة تسمية المحادثة</DialogTitle>
          </DialogHeader>
          <input
            ref={inputRef}
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleConfirmRename();
              else if (e.key === "Escape") setShowRenameDialog(false);
            }}
            dir="rtl"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-1 focus:ring-ring"
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setShowRenameDialog(false)}>
              إلغاء
            </Button>
            <Button onClick={handleConfirmRename} disabled={!renameValue.trim()}>
              حفظ
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
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
