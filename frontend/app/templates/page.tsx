"use client";

import { FileText, LayoutTemplate, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useSidebarStore } from "@/stores/sidebar-store";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function TemplatesEmptyPage() {
  const router = useRouter();
  const setCreateTemplateDialogOpen = useSidebarStore(
    (s) => s.setCreateTemplateDialogOpen,
  );

  const handleCreate = () => setCreateTemplateDialogOpen(true);

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground mb-6">
        <FileText className="h-7 w-7" />
      </div>

      <h1 className="text-xl font-bold text-foreground mb-2">قوالبي</h1>
      <p className="text-muted-foreground text-sm mb-8 max-w-md">
        أنشئ قالبًا جديدًا لإعادة استخدامه في عملك القانوني، أو تصفح قوالبك
        المحفوظة.
      </p>

      <div className="flex items-center gap-3">
        <Button onClick={handleCreate}>
          <Plus className="h-4 w-4 me-1.5" />
          قالب جديد
        </Button>
        <Button variant="outline" onClick={() => router.push("/templates/mine")}>
          <LayoutTemplate className="h-4 w-4 me-1.5" />
          تصفح قوالبي
        </Button>
      </div>
    </div>
  );
}
