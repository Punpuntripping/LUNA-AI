import type { SourceView } from "@contracts";

import { chunkMarkdown } from "./chunk-markdown";

// SourceView (every variant) → flat render rows for the reader's virtualised
// list. Pure, so the variant rules (labels, segment order, chunking) are unit
// tested without a renderer. Mirrors the web's SourceViewContent
// (components/workspace/ReferencePanel.tsx); exits are NOT rows — the reader's
// action bar owns them.

export type SourceRow =
  | { kind: "label"; key: string; text: string }
  | { kind: "md"; key: string; text: string }
  | { kind: "note"; key: string; text: string }
  | {
      kind: "image";
      key: string;
      url: string;
      width: number;
      height: number;
      n: number;
      title: string;
      description: string;
    }
  | {
      kind: "provenance";
      key: string;
      citation: { label: string; value: string }[];
      links: { title: string; href: string }[];
    };

export interface SourceRowOptions {
  /** Does the action bar offer a way to the full document? Drives the
   *  «— النص الكامل بالأسفل» suffix (never promise an exit that isn't there). */
  hasFullTextExit: boolean;
  /** «النظام» / «اللائحة» … — `referenceDefiniteType(ref)`. */
  definiteType: string;
}

/** The text «نسخ المحتوى» copies — what is on screen. Same rule as the web. */
export function extractSourceContent(view: SourceView): string {
  if (view.source_type === "case") return view.summary || view.content || "";
  if ("content" in view && typeof view.content === "string") return view.content;
  return "";
}

function mdRows(text: string, prefix: string): SourceRow[] {
  return chunkMarkdown(text).map((chunk, i) => ({ kind: "md", key: `${prefix}-${i}`, text: chunk }));
}

export function sourceRows(view: SourceView, { hasFullTextExit, definiteType }: SourceRowOptions): SourceRow[] {
  const content = extractSourceContent(view);

  switch (view.source_type) {
    case "case": {
      // A ruling reads as its ملخص, labelled so nobody mistakes it for the ruling.
      const rows: SourceRow[] = [];
      if (view.summary) {
        rows.push({ kind: "label", key: "label", text: hasFullTextExit ? "ملخص الحكم — النص الكامل بالأسفل" : "ملخص الحكم" });
      }
      rows.push(...mdRows(content, "md"));
      const citation = view.citation ?? [];
      const links = (view.official_sources ?? []).filter((l) => /^https?:\/\//i.test(l.href));
      if (citation.length > 0 || links.length > 0) rows.push({ kind: "provenance", key: "provenance", citation, links });
      return rows;
    }
    case "chunk": {
      const segments = view.display_segments ?? [];
      if (segments.length === 0) return mdRows(content, "md");
      const rows: SourceRow[] = [];
      segments.forEach((seg, i) => {
        if (seg.kind === "table") {
          // `md`, never `html`: raw markup is never rendered (plan §R3).
          if (seg.md.trim()) rows.push({ kind: "md", key: `table-${i}`, text: seg.md });
        } else if (seg.kind === "image") {
          if (/^https:\/\//i.test(seg.url)) {
            rows.push({
              kind: "image",
              key: `image-${i}`,
              url: seg.url,
              width: seg.width,
              height: seg.height,
              n: seg.n,
              title: seg.title,
              description: seg.description,
            });
          }
        } else {
          rows.push(...mdRows(seg.text, `text-${i}`));
        }
      });
      return rows;
    }
    case "gov_service":
      // Title + link only; the procedure lives on the entity's site.
      return hasFullTextExit
        ? [{ kind: "note", key: "note", text: "شروط هذه الخدمة ومستنداتها وخطواتها منشورة على موقع الجهة الرسمي." }]
        : [];
    case "circular": {
      const rows: SourceRow[] = [];
      if (view.entity_name) rows.push({ kind: "label", key: "label", text: `الجهة: ${view.entity_name}` });
      rows.push(...mdRows(content, "md"));
      return rows;
    }
    case "article_full": {
      // Verbatim statutory text: no «ملخص» framing.
      const num = view.article_num?.trim();
      const heading = [num ? `المادة ${num}` : "نص المادة", view.regulation_title?.trim()].filter(Boolean).join(" — ");
      return [{ kind: "label", key: "label", text: heading }, ...mdRows(content, "md")];
    }
    case "regulation_summary":
      return [
        {
          kind: "label",
          key: "label",
          text: hasFullTextExit ? `ملخص ${definiteType} — النص الكامل بالأسفل` : `ملخص ${definiteType}`,
        },
        ...mdRows(content, "md"),
      ];
    default:
      // Legacy (article / section / regulation) only — bare markdown.
      return mdRows(content, "md");
  }
}
