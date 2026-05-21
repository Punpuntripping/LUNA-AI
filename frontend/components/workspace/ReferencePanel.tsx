"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Scale,
  Gavel,
  Building2,
  ExternalLink,
  ChevronDown,
  FileText,
  Link2,
  Copy,
  Check,
} from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { cn } from "@/lib/utils";
import type { Reference, ReferenceDomain, SourceView } from "@/types";

interface ReferencePanelProps {
  references: Reference[];
  /**
   * When non-null, the matching ``<li id="ref-{n}">`` is scrolled into view
   * and briefly flashes. Set by ``openWorkspaceItemAtReference`` in the chat
   * store; cleared via ``onFlashDone`` (called from the ``<li>`` animation-
   * end handler) so the same marker can be re-clicked.
   */
  focusedReferenceN?: number | null;
  /** Called after the flash animation completes. */
  onFlashDone?: () => void;
}

const DOMAIN_META: Record<
  ReferenceDomain,
  { label: string; icon: typeof Scale; tint: string }
> = {
  regulations: { label: "نظام", icon: Scale, tint: "text-sky-600 dark:text-sky-400" },
  cases: { label: "قضية", icon: Gavel, tint: "text-amber-600 dark:text-amber-400" },
  compliance: {
    label: "خدمة حكومية",
    icon: Building2,
    tint: "text-emerald-600 dark:text-emerald-400",
  },
};

/**
 * JSON-driven reference list for a deep_search ``agent_search`` artifact.
 *
 * Renders one card per ``Reference`` (from ``metadata.references``), switching
 * on ``domain``. Each card exposes the primary external link and — when a
 * ``source_view`` payload is present — a popup with the full original source.
 *
 * Window C: each card carries ``id="ref-{n}"`` so chat-bubble citation
 * markers can scroll the matching card into view and trigger a brief flash
 * via the ``data-flash`` attribute + ``ref-flash`` keyframe (globals.css).
 */
export function ReferencePanel({
  references,
  focusedReferenceN,
  onFlashDone,
}: ReferencePanelProps) {
  const [openView, setOpenView] = useState<Reference | null>(null);
  // Per-card refs so we can scrollIntoView the focused one without a global
  // querySelector on every focus change. Only used for the no-source-view
  // fallback path — references that DO carry a source_view open the popup
  // dialog directly (same as clicking the «عرض المصدر» button on the card).
  const itemRefs = useRef<Map<number, HTMLLIElement | null>>(new Map());

  // Stable sorted list — memoized so the effect below doesn't fire on every
  // parent re-render (a fresh array literal would defeat React's dep check).
  const ordered = useMemo(
    () => [...(references ?? [])].sort((a, b) => a.n - b.n),
    [references],
  );

  useEffect(() => {
    if (focusedReferenceN == null) return;
    const ref = ordered.find((r) => r.n === focusedReferenceN);
    if (!ref) return;
    // Preferred path: behave exactly like clicking the card's «عرض المصدر»
    // button — open the source-view dialog with the full original source.
    // Same component, same content, same close affordance.
    if (ref.source_view) {
      setOpenView(ref);
      return;
    }
    // Fallback: reference has no source_view payload (legacy rows, manually
    // authored references, etc). Scroll the card into view and flash it so
    // the user at least sees which entry was meant.
    const el = itemRefs.current.get(focusedReferenceN);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.setAttribute("data-flash", "true");
  }, [focusedReferenceN, ordered]);

  if (!references || references.length === 0) return null;

  const handleAnimationEnd = (n: number) => {
    const el = itemRefs.current.get(n);
    if (el) el.removeAttribute("data-flash");
    onFlashDone?.();
  };

  return (
    <div dir="rtl" className="mt-6 border-t pt-4">
      <h3 className="mb-3 text-sm font-semibold text-foreground">
        المراجع <span className="text-muted-foreground">({ordered.length})</span>
      </h3>
      <ul className="flex flex-col gap-2">
        {ordered.map((ref) => (
          <ReferenceCard
            key={`${ref.n}-${ref.ref_id}`}
            reference={ref}
            registerRef={(node) => {
              if (node) {
                itemRefs.current.set(ref.n, node);
              } else {
                itemRefs.current.delete(ref.n);
              }
            }}
            onAnimationEnd={() => handleAnimationEnd(ref.n)}
            onViewSource={() => setOpenView(ref)}
          />
        ))}
      </ul>

      <Dialog
        open={openView !== null}
        onOpenChange={(o) => {
          if (o) return;
          setOpenView(null);
          // Mirror the flash-end semantics: tell the parent the focused
          // reference has been consumed so repeat-clicking the same ``[n]``
          // re-opens the dialog instead of going no-op on the unchanged
          // focusedReferenceN value.
          onFlashDone?.();
        }}
      >
        <DialogContent className="max-w-2xl" dir="rtl">
          {openView && <SourceViewBody reference={openView} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reference card
// ---------------------------------------------------------------------------

function ReferenceCard({
  reference,
  registerRef,
  onAnimationEnd,
  onViewSource,
}: {
  reference: Reference;
  registerRef: (node: HTMLLIElement | null) => void;
  onAnimationEnd: () => void;
  onViewSource: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const meta = DOMAIN_META[reference.domain] ?? DOMAIN_META.regulations;
  const Icon = meta.icon;

  const primaryUrl = referencePrimaryUrl(reference);
  const label = referenceLabel(reference);
  const crossRefs = reference.domain === "regulations" ? reference.cross_refs : [];

  return (
    <li
      ref={registerRef}
      id={`ref-${reference.n}`}
      onAnimationEnd={onAnimationEnd}
      className="rounded-lg border bg-card px-3 py-2.5 ref-flash-target"
    >
      <div className="flex items-start gap-2.5">
        {/* [n] badge */}
        <span className="mt-0.5 flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md bg-muted px-1.5 text-xs font-semibold tabular-nums text-foreground">
          {reference.n}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.tint)} />
            <span className="text-[11px] font-medium text-muted-foreground">
              {meta.label}
            </span>
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                reference.relevance === "high" ? "bg-emerald-500" : "bg-amber-400"
              )}
              title={reference.relevance === "high" ? "صلة عالية" : "صلة متوسطة"}
            />
          </div>

          <p className="mt-0.5 text-sm font-medium leading-snug text-foreground">
            {label}
          </p>

          {reference.snippet && (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
              {reference.snippet}
            </p>
          )}

          {/* Actions */}
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {reference.source_view && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-[11px]"
                onClick={onViewSource}
              >
                <FileText className="h-3 w-3" />
                عرض المصدر
              </Button>
            )}
            {primaryUrl && (
              <a
                href={primaryUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  buttonVariants({ variant: "ghost", size: "sm" }),
                  "h-6 gap-1 px-2 text-[11px]"
                )}
              >
                <ExternalLink className="h-3 w-3" />
                فتح الرابط
              </a>
            )}
            {crossRefs.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 gap-1 px-2 text-[11px]"
                onClick={() => setExpanded((v) => !v)}
              >
                <ChevronDown
                  className={cn(
                    "h-3 w-3 transition-transform",
                    expanded && "rotate-180"
                  )}
                />
                إحالات ({crossRefs.length})
              </Button>
            )}
          </div>

          {/* Cross-refs */}
          {expanded && crossRefs.length > 0 && (
            <ul className="mt-1.5 flex flex-col gap-1 border-t pt-1.5">
              {crossRefs.map((cr, i) => (
                <li key={i} className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {[cr.target_reg_title, crossRefUnit(cr)]
                      .filter(Boolean)
                      .join(" — ")}
                  </span>
                  {cr.content && (
                    <span className="block leading-relaxed">{cr.content}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Source view dialog body
// ---------------------------------------------------------------------------

function SourceViewBody({ reference }: { reference: Reference }) {
  const view = reference.source_view;
  if (!view) return null;
  const sourceContent = extractSourceContent(view);

  return (
    <>
      <DialogHeader>
        <DialogTitle className="text-base">{view.title || referenceLabel(reference)}</DialogTitle>
      </DialogHeader>
      <div className="max-h-[60vh] overflow-y-auto text-sm leading-relaxed text-foreground" dir="rtl">
        <SourceViewContent view={view} sourceContent={sourceContent} />
      </div>
      {sourceContent ? <SourceCopyButton content={sourceContent} /> : null}
    </>
  );
}

function SourceCopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // See ArtifactPreview: clipboard can fail silently — the user can
      // still highlight & copy by hand.
    }
  };
  return (
    <div className="mt-3 flex justify-start">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="h-7 gap-1.5 px-2 text-[11px]"
        onClick={handleCopy}
      >
        {copied ? (
          <>
            <Check className="h-3 w-3" />
            تم النسخ
          </>
        ) : (
          <>
            <Copy className="h-3 w-3" />
            نسخ المحتوى
          </>
        )}
      </Button>
    </div>
  );
}

function extractSourceContent(view: SourceView): string {
  if ("content" in view && typeof view.content === "string") return view.content;
  return "";
}

function SourceViewContent({
  view,
  sourceContent,
}: {
  view: SourceView;
  sourceContent: string;
}) {
  if (view.source_type === "chunk") {
    return (
      <div className="space-y-3">
        {sourceContent && <MarkdownRenderer content={sourceContent} />}
        <SourceLink label="رابط النظام" url={view.regulation_source_url} />
        <SourceLink label="ملف PDF" url={view.regulation_pdf_link?.url} />
      </div>
    );
  }
  if (view.source_type === "case") {
    return (
      <div className="space-y-3">
        <SourceLink label="تفاصيل الحكم" url={view.details_url} />
      </div>
    );
  }
  if (view.source_type === "gov_service") {
    return (
      <div className="space-y-3">
        <SourceLink label="المنصة الوطنية" url={view.national_platform_url} />
        <SourceLink label="رابط الخدمة" url={view.service_url} />
      </div>
    );
  }
  // Legacy variants (article / section / regulation).
  return (
    <div className="space-y-3">
      {sourceContent && <MarkdownRenderer content={sourceContent} />}
    </div>
  );
}

function SourceLink({ label, url }: { label: string; url?: string }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
    >
      <Link2 className="h-3 w-3" />
      {label}
    </a>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Best human-readable title for a reference card. */
function referenceLabel(ref: Reference): string {
  if (ref.title) return ref.title;
  if (ref.domain === "cases") {
    return [ref.entity_name, ref.regulation_title].filter(Boolean).join(" — ") || "قضية";
  }
  return ref.regulation_title || ref.ref_id || "مرجع";
}

/** The single external URL a card's "فتح الرابط" button targets. */
function referencePrimaryUrl(ref: Reference): string {
  switch (ref.domain) {
    case "regulations":
      return ref.landing_url || "";
    case "compliance":
      return ref.service_url || ref.url || "";
    case "cases":
      return ref.details_url || "";
    default:
      return "";
  }
}

/** Render a cross-ref target unit like "مادة:12" when a number is present. */
function crossRefUnit(cr: { target_type: string; target_number: number | null }): string {
  if (cr.target_number == null) return cr.target_type || "";
  return `${cr.target_type}:${cr.target_number}`;
}
