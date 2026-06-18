"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { FileX, Loader2, MoreVertical, Trash2 } from "lucide-react";
import { cn, getRelativeTimeAr } from "@/lib/utils";
import {
  useTemplates,
  useCreateTemplate,
  useDeleteTemplate,
} from "@/hooks/use-templates";
import { useSidebarStore } from "@/stores/sidebar-store";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { UserTemplate } from "@/types";

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 pt-3 pb-2 shrink-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground/60">
        {children}
      </p>
    </div>
  );
}

function TemplateSkeleton() {
  return (
    <div className="space-y-1.5 px-3 py-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-7 rounded-md bg-muted/40 animate-pulse" />
      ))}
    </div>
  );
}

function TemplateItem({ template }: { template: UserTemplate }) {
  const router = useRouter();
  const params = useParams<{ id?: string }>();
  const deleteTemplate = useDeleteTemplate();
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  const isActive = params?.id === template.template_id;
  const title = template.title || "قالب بدون عنوان";

  const handleClick = () => {
    router.push(`/templates/${template.template_id}`);
  };

  const handleConfirmDelete = () => {
    deleteTemplate.mutate(template.template_id, {
      onSuccess: () => {
        if (isActive) router.push("/templates");
      },
    });
    setShowDeleteDialog(false);
  };

  return (
    <>
      <div
        className={cn(
          "group flex items-center gap-2 rounded-md px-3 py-2 cursor-pointer transition-colors",
          isActive
            ? "bg-accent text-accent-foreground"
            : "text-sidebar-foreground/85 hover:bg-accent/40 hover:text-foreground",
        )}
        onClick={handleClick}
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm truncate">{title}</p>
          <p className="text-[11px] text-muted-foreground/70 mt-0.5">
            {getRelativeTimeAr(template.updated_at)}
          </p>
        </div>

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
            <DropdownMenuContent align="start" className="w-40">
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

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>حذف القالب</AlertDialogTitle>
            <AlertDialogDescription>
              هل أنت متأكد من حذف هذا القالب؟
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

function CreateTemplateDialog() {
  const router = useRouter();
  const createTemplate = useCreateTemplate();
  const isOpen = useSidebarStore((s) => s.isCreateTemplateDialogOpen);
  const setOpen = useSidebarStore((s) => s.setCreateTemplateDialogOpen);

  const [title, setTitle] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const close = (open: boolean) => {
    setOpen(open);
    if (!open) {
      setTitle("");
      setFormError(null);
    }
  };

  const handleCreate = () => {
    if (!title.trim()) {
      setFormError("عنوان القالب مطلوب");
      return;
    }
    createTemplate.mutate(
      { title: title.trim(), content_md: "" },
      {
        onSuccess: (template) => {
          close(false);
          router.push(`/templates/${template.template_id}`);
        },
        onError: () => setFormError("حدث خطأ أثناء إنشاء القالب"),
      },
    );
  };

  return (
    <Dialog open={isOpen} onOpenChange={close}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>إنشاء قالب جديد</DialogTitle>
          <DialogDescription>أدخل عنوان القالب لإنشائه</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {formError && (
            <div className="rounded-md bg-destructive/10 border border-destructive/20 p-2.5 text-sm text-destructive">
              {formError}
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-sm font-medium text-foreground">
              عنوان القالب
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                if (formError) setFormError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleCreate();
                }
              }}
              autoFocus
              placeholder="مثال: قالب عقد إيجار"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              dir="rtl"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            إلغاء
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!title.trim() || createTemplate.isPending}
          >
            {createTemplate.isPending && (
              <Loader2 className="h-4 w-4 animate-spin me-2" />
            )}
            إنشاء القالب
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function TemplateList() {
  const { data, isLoading, isError } = useTemplates();
  const templates = data?.templates ?? [];

  let body: React.ReactNode;
  if (isLoading) {
    body = <TemplateSkeleton />;
  } else if (isError) {
    body = (
      <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
        <p className="text-sm text-destructive">حدث خطأ في تحميل القوالب</p>
      </div>
    );
  } else if (templates.length === 0) {
    body = (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center gap-3">
        <FileX className="h-9 w-9 text-muted-foreground/40" />
        <div>
          <p className="text-sm font-medium text-muted-foreground">
            لا توجد قوالب بعد
          </p>
          <p className="text-xs text-muted-foreground/70 mt-1">
            أنشئ قالبًا جديدًا لإعادة استخدامه في عملك
          </p>
        </div>
      </div>
    );
  } else {
    body = (
      <ScrollArea className="flex-1 min-h-0">
        <div className="px-2 pb-2 space-y-0.5">
          {templates.map((template) => (
            <TemplateItem key={template.template_id} template={template} />
          ))}
        </div>
      </ScrollArea>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <SectionHeader>قوالبي</SectionHeader>
      {body}
      <CreateTemplateDialog />
    </div>
  );
}
