"use client";

import { useParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useTemplate } from "@/hooks/use-templates";
import { TemplateEditor } from "@/components/templates/TemplateEditor";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function TemplatePage() {
  const params = useParams<{ id: string }>();
  const templateId = params.id;
  const { data: template, isLoading, isError } = useTemplate(templateId);

  if (!templateId) return null;

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isError || !template) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-center">
        <p className="text-sm text-destructive">حدث خطأ في تحميل القالب</p>
      </div>
    );
  }

  return <TemplateEditor template={template} />;
}
