"use client";

import { FileText, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function TemplatesEmptyPage() {
  const setCreateTemplateDialogOpen = useSidebarStore(
    (s) => s.setCreateTemplateDialogOpen,
  );

  const handleCreate = () => setCreateTemplateDialogOpen(true);

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground mb-6">
        <FileText className="h-7 w-7" />
      </div>

      <h1 className="text-xl font-bold text-foreground mb-2">اختر قالبًا</h1>
      <p className="text-muted-foreground text-sm mb-8 max-w-md">
        اختر قالبًا من القائمة لتحريره، أو أنشئ قالبًا جديدًا لإعادة استخدامه في
        عملك القانوني.
      </p>

      <Button onClick={handleCreate}>
        <Plus className="h-4 w-4 me-1.5" />
        قالب جديد
      </Button>
    </div>
  );
}
