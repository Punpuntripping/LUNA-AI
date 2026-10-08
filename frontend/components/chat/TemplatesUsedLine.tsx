"use client";

import { memo } from "react";
import Link from "next/link";
import { LayoutTemplate } from "lucide-react";
import { cn } from "@/lib/utils";
import { TemplateScopeBadge } from "@/components/templates/TemplateScopeBadge";
import type { TemplateUsed } from "@/types";

interface TemplatesUsedLineProps {
  /** Validated ``templates_used`` — ``[]`` = drafted without a template. */
  templates: TemplateUsed[];
  messageId: string;
  className?: string;
}

/**
 * «القوالب المستخدمة في الكتابة» — a compact, muted provenance row under an
 * assistant answer whose turn produced a draft: which قالب(s) the writer built
 * on, each linking to ``/templates/{id}``, with its scope and the sources the
 * template was derived from.
 *
 * Deliberately NOT chip-styled: ``NextStepChips`` are rounded outline buttons
 * that act; this is a plain text line that informs.
 */
export const TemplatesUsedLine = memo(function TemplatesUsedLine({
  templates,
  messageId,
  className,
}: TemplatesUsedLineProps) {
  const labelId = `templates-used-${messageId}`;
  return (
    <div
      dir="rtl"
      role="group"
      aria-labelledby={labelId}
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground",
        className,
      )}
    >
      <span className="inline-flex items-center gap-1">
        <LayoutTemplate className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span id={labelId}>القوالب المستخدمة في الكتابة:</span>
      </span>

      {templates.length === 0 ? (
        <span>لم يُستخدم قالب — بُني الهيكل تلقائياً</span>
      ) : (
        templates.map((t, idx) => (
          <span
            key={t.template_id}
            className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1"
          >
            {idx > 0 && (
              <span aria-hidden="true" className="text-muted-foreground/50">
                ·
              </span>
            )}
            <Link
              href={`/templates/${t.template_id}`}
              className="max-w-full truncate text-foreground/80 underline-offset-4 hover:underline"
            >
              {t.title}
            </Link>
            <TemplateScopeBadge scope={t.scope} short />
            {t.sources.length > 0 && (
              <span className="inline-flex items-center gap-1">
                <span>المصادر:</span>
                {t.sources.map((url, i) => (
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
          </span>
        ))
      )}
    </div>
  );
});
