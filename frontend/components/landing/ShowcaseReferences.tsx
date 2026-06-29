"use client";

import { useState } from "react";
import {
  ExternalLink,
  FileText,
  Scale,
  Gavel,
  Building2,
  type LucideIcon,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { cn } from "@/lib/utils";
import type { ShowcaseCitation } from "./content";

/** Domain → icon, resolved client-side (the icon component can't cross the
 *  server→client prop boundary). Mirrors ReferencePanel's DOMAIN_META. */
const DOMAIN_ICON: Record<ShowcaseCitation["domain"], LucideIcon> = {
  regulations: Scale,
  cases: Gavel,
  compliance: Building2,
};

/**
 * The showcase «المراجع» panel — mirrors the in-app ReferencePanel. Renders the
 * real citation cards and, for any citation carrying ``sourceMd``, lets «عرض
 * المصدر» open the verbatim source text in a dialog (a live demo of the
 * in-app source viewer). Client component because of the dialog state.
 */
export function ShowcaseReferences({
  citations,
  totalRefs,
}: {
  citations: ShowcaseCitation[];
  totalRefs: number;
}) {
  const [openSource, setOpenSource] = useState<ShowcaseCitation | null>(null);
  const remaining = Math.max(0, totalRefs - citations.length);

  return (
    <div className="border-t border-border pt-4">
      <h3 className="mb-3 text-sm font-semibold text-foreground">
        المراجع <span className="text-muted-foreground">({totalRefs})</span>
      </h3>

      <ul className="flex flex-col gap-2">
        {citations.map((c) => (
          <li key={c.n}>
            <CitationCard citation={c} onViewSource={() => setOpenSource(c)} />
          </li>
        ))}
      </ul>

      {remaining > 0 && (
        <p className="mt-2.5 text-center text-xs text-muted-foreground">
          و{remaining} مرجعاً آخر في التقرير الكامل
        </p>
      )}

      <Dialog
        open={openSource !== null}
        onOpenChange={(o) => {
          if (!o) setOpenSource(null);
        }}
      >
        <DialogContent className="max-w-2xl" dir="rtl">
          {openSource && (
            <>
              <DialogHeader>
                <DialogTitle className="text-base">
                  {openSource.title}
                </DialogTitle>
              </DialogHeader>
              <div
                className="markdown-content max-h-[60vh] overflow-y-auto text-sm leading-relaxed text-foreground"
                dir="rtl"
              >
                {openSource.sourceMd && (
                  <MarkdownRenderer content={openSource.sourceMd} />
                )}
              </div>
              <a
                href={openSource.url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
              >
                <ExternalLink className="h-3 w-3" />
                فتح المصدر الرسمي
              </a>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CitationCard({
  citation,
  onViewSource,
}: {
  citation: ShowcaseCitation;
  onViewSource: () => void;
}) {
  const Icon = DOMAIN_ICON[citation.domain];
  return (
    <div className="rounded-lg border bg-card px-3 py-2.5 transition-colors hover:border-primary/40">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md bg-muted px-1.5 text-xs font-semibold tabular-nums text-foreground">
          {citation.n}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Icon className={cn("h-3.5 w-3.5 shrink-0", citation.tint)} />
            <span className="text-[11px] font-medium text-muted-foreground">
              {citation.label}
            </span>
            <span
              className="h-1.5 w-1.5 rounded-full bg-emerald-500"
              title="صلة عالية"
            />
          </div>

          <p className="mt-0.5 text-sm font-medium leading-snug text-foreground">
            {citation.title}
          </p>
          {citation.provider && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {citation.provider}
            </p>
          )}
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {citation.snippet}
          </p>

          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {citation.sourceMd && (
              <button
                type="button"
                onClick={onViewSource}
                className="inline-flex h-6 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-foreground transition-colors hover:bg-accent"
              >
                <FileText className="h-3 w-3" />
                عرض المصدر
              </button>
            )}
            <a
              href={citation.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-primary transition-colors hover:bg-accent"
            >
              <ExternalLink className="h-3 w-3" />
              فتح الرابط
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
