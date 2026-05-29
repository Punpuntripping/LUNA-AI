"use client";

import { MarkdownDocEditor } from "@/components/workspace/MarkdownDocEditor";
import { useUpdateTemplate } from "@/hooks/use-templates";
import type { UserTemplate } from "@/types";

interface TemplateEditorProps {
  template: UserTemplate;
}

/**
 * Editor for a user template (قالب). Reuses the shared ``MarkdownDocEditor``
 * with the same edit/preview + debounced autosave UX as a workspace note,
 * wired to ``useUpdateTemplate``. No agent lock and no references — templates
 * are plain user-global markdown documents.
 */
export function TemplateEditor({ template }: TemplateEditorProps) {
  const update = useUpdateTemplate();

  const handleSave = (patch: { title?: string; content_md?: string }) =>
    update.mutateAsync({ templateId: template.template_id, data: patch });

  return (
    <MarkdownDocEditor
      docId={template.template_id}
      initialTitle={template.title}
      initialContent={template.content_md ?? ""}
      updatedAt={template.updated_at}
      onSave={handleSave}
      titleRequired
      titlePlaceholder="عنوان القالب..."
      bodyPlaceholder="اكتب محتوى القالب هنا..."
    />
  );
}
