"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  MoreVertical,
  Pencil,
  Trash2,
  Archive,
  XCircle,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarStore } from "@/stores/sidebar-store";
import { useConversations } from "@/hooks/use-conversations";
import { useDeleteCase, useUpdateCase, useUpdateCaseStatus } from "@/hooks/use-cases";
import { ConversationItem } from "@/components/sidebar/ConversationItem";
import { Button } from "@/components/ui/button";
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
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
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
import type { CaseSummary, CaseType, CasePriority } from "@/types";

interface CaseCardProps {
  caseSummary: CaseSummary;
}

const CASE_TYPES: { value: CaseType; label: string }[] = [
  { value: "عقاري", label: "عقاري" },
  { value: "تجاري", label: "تجاري" },
  { value: "عمالي", label: "عمالي" },
  { value: "جنائي", label: "جنائي" },
  { value: "أحوال_شخصية", label: "أحوال شخصية" },
  { value: "إداري", label: "إداري" },
  { value: "تنفيذ", label: "تنفيذ" },
  { value: "عام", label: "عام" },
];

const PRIORITIES: { value: CasePriority; label: string }[] = [
  { value: "high", label: "عالية" },
  { value: "medium", label: "متوسطة" },
  { value: "low", label: "منخفضة" },
];

function CaseConversations({ caseId }: { caseId: string }) {
  const { data, isLoading } = useConversations(caseId);

  if (isLoading) {
    return (
      <div className="ps-6 space-y-1 py-1">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="flex items-center gap-2 px-2 py-1.5 animate-pulse">
            <div className="h-3 w-3 rounded bg-muted" />
            <div className="h-3 w-2/3 rounded bg-muted" />
          </div>
        ))}
      </div>
    );
  }

  if (!data?.conversations || data.conversations.length === 0) {
    return (
      <div className="ps-8 py-1.5">
        <p className="text-xs text-muted-foreground/70">لا توجد محادثات</p>
      </div>
    );
  }

  return (
    <div className="ps-4 ms-4 border-s border-border/60 py-1 space-y-0.5">
      {data.conversations.map((conv) => (
        <ConversationItem key={conv.conversation_id} conversation={conv} />
      ))}
    </div>
  );
}

export function CaseCard({ caseSummary }: CaseCardProps) {
  const { expandedCases, toggleCaseExpanded } = useSidebarStore();
  const isExpanded = expandedCases.has(caseSummary.case_id);

  const deleteCase = useDeleteCase();
  const updateCase = useUpdateCase();
  const updateCaseStatus = useUpdateCaseStatus();

  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);

  // Edit form state
  const [editName, setEditName] = useState("");
  const [editType, setEditType] = useState<CaseType>("عام");
  const [editPriority, setEditPriority] = useState<CasePriority>("medium");
  const [editDescription, setEditDescription] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const openEditDialog = () => {
    setEditName(caseSummary.case_name);
    setEditType(caseSummary.case_type);
    setEditPriority(caseSummary.priority);
    setEditDescription(caseSummary.description || "");
    setEditError(null);
    setShowEditDialog(true);
  };

  const handleUpdate = () => {
    if (!editName.trim()) {
      setEditError("اسم القضية مطلوب");
      return;
    }

    updateCase.mutate(
      {
        caseId: caseSummary.case_id,
        data: {
          case_name: editName.trim(),
          case_type: editType,
          priority: editPriority,
          description: editDescription.trim() || undefined,
        },
      },
      {
        onSuccess: () => setShowEditDialog(false),
        onError: () => setEditError("حدث خطأ أثناء تحديث القضية"),
      }
    );
  };

  const handleStatusChange = (status: string) => {
    updateCaseStatus.mutate({ caseId: caseSummary.case_id, status });
  };

  const handleConfirmDelete = () => {
    deleteCase.mutate(caseSummary.case_id);
    setShowDeleteDialog(false);
  };

  return (
    <>
      <div>
        <div
          className={cn(
            "group flex items-center gap-2 rounded-md px-3 py-2 cursor-pointer transition-colors",
            isExpanded
              ? "bg-accent/40 text-foreground"
              : "text-sidebar-foreground/85 hover:bg-accent/30 hover:text-foreground"
          )}
          onClick={() => toggleCaseExpanded(caseSummary.case_id)}
        >
          <span className="shrink-0 text-muted-foreground/70">
            {isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronLeft className="h-3.5 w-3.5" />
            )}
          </span>

          <p className="flex-1 min-w-0 text-sm truncate">
            {caseSummary.case_name}
          </p>

          <div
            className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-6 w-6">
                  <MoreVertical className="h-3.5 w-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-48">
                <DropdownMenuItem onClick={openEditDialog}>
                  <Pencil className="h-3.5 w-3.5 me-2" />
                  تعديل القضية
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {caseSummary.status === "active" && (
                  <DropdownMenuItem onClick={() => handleStatusChange("closed")}>
                    <XCircle className="h-3.5 w-3.5 me-2" />
                    إغلاق القضية
                  </DropdownMenuItem>
                )}
                {caseSummary.status !== "archived" && (
                  <DropdownMenuItem onClick={() => handleStatusChange("archived")}>
                    <Archive className="h-3.5 w-3.5 me-2" />
                    أرشفة القضية
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => setShowDeleteDialog(true)}
                  className="text-destructive focus:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5 me-2" />
                  حذف القضية
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {isExpanded && <CaseConversations caseId={caseSummary.case_id} />}
      </div>

      {/* Delete confirmation dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>حذف القضية</AlertDialogTitle>
            <AlertDialogDescription>
              هل أنت متأكد من حذف هذه القضية؟ سيتم حذف جميع المحادثات المرتبطة بها.
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

      {/* Edit case dialog */}
      <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>تعديل القضية</DialogTitle>
            <DialogDescription>
              تعديل تفاصيل القضية
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {editError && (
              <div className="rounded-md bg-destructive/10 border border-destructive/20 p-2.5 text-sm text-destructive">
                {editError}
              </div>
            )}

            {/* Case name */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                اسم القضية
              </label>
              <input
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                dir="rtl"
              />
            </div>

            {/* Case type */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                نوع القضية
              </label>
              <select
                value={editType}
                onChange={(e) => setEditType(e.target.value as CaseType)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                dir="rtl"
              >
                {CASE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Priority */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                الأولوية
              </label>
              <select
                value={editPriority}
                onChange={(e) => setEditPriority(e.target.value as CasePriority)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                dir="rtl"
              >
                {PRIORITIES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                وصف القضية
                <span className="text-muted-foreground font-normal me-1">
                  {" "}(اختياري)
                </span>
              </label>
              <textarea
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                rows={3}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
                dir="rtl"
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowEditDialog(false)}>
              إلغاء
            </Button>
            <Button onClick={handleUpdate} disabled={updateCase.isPending}>
              {updateCase.isPending && (
                <Loader2 className="h-4 w-4 animate-spin me-2" />
              )}
              حفظ التعديلات
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
