"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useCreateTemplate } from "@/hooks/use-templates";
import { useSidebarStore } from "@/stores/sidebar-store";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * «قالب جديد» dialog, driven by the sidebar store's
 * isCreateTemplateDialogOpen flag so any surface under /templates can open it
 * (the empty-state landing, the قوالبي grid). Mounted once in
 * TemplatesLayoutClient.
 */
export function CreateTemplateDialog() {
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
