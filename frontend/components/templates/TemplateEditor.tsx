"use client";

import { useRouter } from "next/navigation";
import { CopyPlus, Loader2 } from "lucide-react";
import { MarkdownDocEditor } from "@/components/workspace/MarkdownDocEditor";
import { Button } from "@/components/ui/button";
import { TemplateScopeBadge } from "@/components/templates/TemplateScopeBadge";
import { useCreateTemplate, useUpdateTemplate } from "@/hooks/use-templates";
import type { UserTemplate } from "@/types";

interface TemplateEditorProps {
  template: UserTemplate;
}

/** A system template is never saved from here — the editor is read-only. */
const noopSave = () => Promise.resolve();

/**
 * Editor for a قالب. Reuses the shared ``MarkdownDocEditor`` with the same
 * edit/preview + debounced autosave UX as a workspace note, wired to
 * ``useUpdateTemplate``. No agent lock and no references — templates are plain
 * markdown documents.
 *
 * A system template («قالب عام», ``scope === "system"``) is read-only: the
 * backend 403s a PATCH, so body and title are locked, autosave never runs, and
 * a header bar offers «انسخ إلى قوالبي» — a private copy the user can edit.
 */
export function TemplateEditor({ template }: TemplateEditorProps) {
  const update = useUpdateTemplate();
  const isSystem = template.scope === "system";

  const handleSave = (patch: { title?: string; content_md?: string }) =>
    update.mutateAsync({ templateId: template.template_id, data: patch });

  return (
    <MarkdownDocEditor
      // One editor instance per قالب — never carry one template's local
      // title/body/autosave state over to another.
      key={template.template_id}
      docId={template.template_id}
      initialTitle={template.title}
      initialContent={template.content_md ?? ""}
      updatedAt={template.updated_at}
      onSave={isSystem ? noopSave : handleSave}
      readOnly={isSystem}
      titleReadOnly={isSystem}
      hideSaveStatus={isSystem}
      headerSlot={isSystem ? <SystemTemplateBar template={template} /> : undefined}
      titleRequired
      titlePlaceholder="عنوان القالب..."
      bodyPlaceholder="اكتب محتوى القالب هنا..."
    />
  );
}

function SystemTemplateBar({ template }: { template: UserTemplate }) {
  const router = useRouter();
  const create = useCreateTemplate();

  const handleCopy = () => {
    create.mutate(
      { title: template.title, content_md: template.content_md },
      {
        onSuccess: (created) => {
          router.push(`/templates/${created.template_id}`);
        },
      },
    );
  };

  return (
    <div
      dir="rtl"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground"
    >
      <TemplateScopeBadge scope="system" />
      <span>قالب عام من ريحان — للقراءة فقط.</span>

      {template.sources.length > 0 && (
        <span className="inline-flex flex-wrap items-center gap-1.5">
          <span>المصادر:</span>
          {template.sources.map((url, i) => (
            <a
              key={`${i}-${url}`}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              title={url}
              aria-label={`المصدر ${i + 1} (يفتح في نافذة جديدة)`}
              className="tabular-nums text-foreground/70 underline-offset-4 hover:text-foreground hover:underline"
            >
              [{i + 1}]
            </a>
          ))}
        </span>
      )}

      <div className="ms-auto flex items-center gap-2">
        {create.isError && (
          <span className="text-destructive">تعذّر نسخ القالب. حاول مرة أخرى.</span>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          onClick={handleCopy}
          disabled={create.isPending}
        >
          {create.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <CopyPlus className="h-3.5 w-3.5" />
          )}
          انسخ إلى قوالبي
        </Button>
      </div>
    </div>
  );
}
