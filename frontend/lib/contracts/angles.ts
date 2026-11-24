// Angle map («الأوجه») — the user's angle maps, shown first in «الملفات» (the
// chat pane's «الملفات» tab and the /files page; .claude/plans/angle_map_ui.md
// rev 3). Pure TypeScript, shared with the mobile app.
//
// A map belongs to the MESSAGE the files were sent in (one map per message
// group, `attachment_angle_maps`): a conversation can hold several.

import type { FileDocType } from "./files";

// ---------------------------------------------------------------------------
// Wire types — `GET /files/angle-maps[?conversation_id=]` (light rows) and
// `GET /files/angle-maps/{map_id}` (one map in full).
// ---------------------------------------------------------------------------

/** يقوّي موقف الموكّل · يطعن في الخصم · نقطة قوة للخصم يجب الرد عليها. */
export type AngleKind = "strengthen" | "attack" | "answer";
export type AngleStrength = "strong" | "medium" | "weak";
export type AngleClientConfidence = "clear" | "likely" | "unclear";
/**
 * A section's side, computed by the backend from the section's owner
 * (`angle_mapper.labels.relative_label`) — relative to the client, or to the
 * angle's own side when the client's side is not known. null = cannot be told.
 */
export type AngleSectionSide = "own" | "opp" | "neutral";
export type AngleGapKind = "missing" | "contradiction" | "unanswered";

/** A section an angle or a gap rests on, with its side from that viewpoint. */
export interface AngleSectionRef {
  /** "D1.S7" — section 7 of the message's first document. */
  ref: string;
  side: AngleSectionSide | null;
}

export interface AngleMapAngle {
  /** Most decisive first, 1-based. */
  seq: number;
  kind: AngleKind;
  strength: AngleStrength;
  /** The owner key of the side the angle serves (claimant, respondent…). */
  for_side: string;
  /** That party as the file names it. */
  for_party: string;
  title: string;
  argument: string;
  /** «سؤال البحث» — the legal question a search should answer. */
  legal_question: string;
  /** «ما يضعفه» — "" when nothing cuts against it. */
  weakness: string;
  /** Rests on a figure or date the OCR could have misread. */
  ocr_suspect: boolean;
  /** Most important first — the model's order, NOT a path through the file. */
  sections: AngleSectionRef[];
}

export interface AngleMapGap {
  kind: AngleGapKind;
  issue: string;
  ocr_suspect: boolean;
  sections: AngleSectionRef[];
}

export interface AngleMapSection {
  ref: string;
  /** D-number, 1-based (upload order inside the message). */
  document: number;
  seq: number;
  what: string;
  /** core | supporting | metadata | boilerplate | repeat */
  value: string;
  owner_class: string | null;
  owner: string | null;
  brought_by: string | null;
  page_from: number | null;
  page_to: number | null;
  /** Side relative to the client (null when the client's side is unknown). */
  side: AngleSectionSide | null;
  /** The section's original text. */
  content_md: string;
}

export interface AngleMapDocument {
  /** D-number, 1-based. */
  index: number;
  item_id: string;
  title: string;
  doc_type: FileDocType | null;
  wi_seq: number | null;
}

export interface AngleMapClient {
  /** Owner key, or "unknown" / "" when the user's words do not say. */
  side: string;
  party: string;
  /** Model-written: the user's words that show the side. Analysis, not a quote of the file. */
  basis: string;
  confidence: AngleClientConfidence;
  /** The client's key / name in documents whose own proceeding names it differently. */
  by_document: { document: number; side: string; party: string }[];
}

export interface AngleMap {
  map_id: string;
  conversation_id: string | null;
  conversation_title: string | null;
  message_id: string | null;
  /** ISO 8601. */
  created_at: string;
  prompt_version: string;
  client: AngleMapClient;
  /** The message's mapped documents, D-order. */
  documents: AngleMapDocument[];
  /** Files of the message the map could not read (no text, no sections yet…). */
  left_out: { item_id: string; title: string; reason: string }[];
  /** Only the sections an angle or a gap rests on. */
  sections: AngleMapSection[];
  angles: AngleMapAngle[];
  gaps: AngleMapGap[];
}

/** One row of `GET /files/angle-maps` — a map without its sections or angles. */
export interface AngleMapSummary {
  map_id: string;
  conversation_id: string | null;
  conversation_title: string | null;
  message_id: string | null;
  /** ISO 8601. */
  created_at: string;
  documents: AngleMapDocument[];
  angle_count: number;
  kinds: Record<AngleKind, number>;
}

/** `GET /files/angle-maps` — the user's fresh maps, newest first. */
export interface AngleMapListResponse {
  maps: AngleMapSummary[];
}

// ---------------------------------------------------------------------------
// Labels — the preview page's wording (scripts/angle_map_preview.py), first
// drafts for the owner.
// ---------------------------------------------------------------------------

export const ANGLE_KINDS: readonly AngleKind[] = ["strengthen", "attack", "answer"];

export const ANGLE_KIND_LABELS: Record<AngleKind, string> = {
  strengthen: "يقوّي",
  attack: "يطعن",
  answer: "يجب الرد",
};

/** The longer form, for the legend. */
export const ANGLE_KIND_HINTS: Record<AngleKind, string> = {
  strengthen: "يقوّي موقف الموكّل",
  attack: "يطعن في الخصم أو في دليل يخدمه",
  answer: "نقطة قوة للخصم يجب الرد عليها",
};

export const ANGLE_STRENGTH_LABELS: Record<AngleStrength, string> = {
  strong: "قوي",
  medium: "متوسط",
  weak: "ضعيف",
};

export const ANGLE_SIDE_LABELS: Record<AngleSectionSide, string> = {
  own: "الموكّل",
  opp: "الخصم",
  neutral: "محايد",
};

export const ANGLE_CONFIDENCE_LABELS: Record<AngleClientConfidence, string> = {
  clear: "واضح",
  likely: "مرجّح",
  unclear: "غير واضح",
};

export const ANGLE_GAP_LABELS: Record<AngleGapKind, string> = {
  contradiction: "تعارض",
  missing: "ناقص",
  unanswered: "بلا رد",
};

export function coerceAngleKind(value: unknown): AngleKind {
  return value === "attack" || value === "answer" ? value : "strengthen";
}

export function coerceAngleStrength(value: unknown): AngleStrength {
  return value === "strong" || value === "weak" ? value : "medium";
}

// ---------------------------------------------------------------------------
// Refs
// ---------------------------------------------------------------------------

const REF_RE = /^D(\d+)[.·]S(\d+)$/;

/** "D2.S7" (or the preview's "D2·S7") → { document: 2, seq: 7 }; null when malformed. */
export function parseAngleRef(ref: string): { document: number; seq: number } | null {
  const m = REF_RE.exec(String(ref ?? "").trim());
  if (!m) return null;
  return { document: Number(m[1]), seq: Number(m[2]) };
}

/** The D-numbers an angle rests on, in D-order, without repeats. */
export function angleDocuments(angle: Pick<AngleMapAngle, "sections">): number[] {
  const docs = new Set<number>();
  for (const s of angle.sections) {
    const parsed = parseAngleRef(s.ref);
    if (parsed) docs.add(parsed.document);
  }
  return [...docs].sort((a, b) => a - b);
}

/** «يربط بين المستندات»: the angle's sections come from 2+ documents. */
export function isCrossDocumentAngle(angle: Pick<AngleMapAngle, "sections">): boolean {
  return angleDocuments(angle).length > 1;
}

export function angleKindCounts(angles: readonly Pick<AngleMapAngle, "kind">[]): Record<AngleKind, number> {
  const counts: Record<AngleKind, number> = { strengthen: 0, attack: 0, answer: 0 };
  for (const a of angles) counts[coerceAngleKind(a.kind)] += 1;
  return counts;
}

// ---------------------------------------------------------------------------
// The map: one row per angle, one column per document
// ---------------------------------------------------------------------------

export interface AngleMapRow {
  angle: AngleMapAngle;
  /** One cell per document (D-order): the angle's sections there, document order. */
  cells: { document: number; sections: AngleMapSection[] }[];
  /** Index (0-based, D-order) of the first / last column the angle reaches; null when none. */
  first: number | null;
  last: number | null;
}

/**
 * The drawn map (owner, 2026-11-23: "the other option, simplify it" — the mock-up):
 * the documents as columns, each angle a row; a row's cells hold the numbers of
 * the angle's sections in each document, and one straight line runs along the
 * row from the first document it reaches to the last. Refs that name no known
 * section are skipped (a flaky run can leave them).
 */
export function angleMapRows(map: AngleMap): AngleMapRow[] {
  const byRef = new Map(map.sections.map((s) => [s.ref, s]));
  const docs = [...map.documents].sort((a, b) => a.index - b.index);
  return map.angles.map((angle) => {
    const seen = new Set<string>();
    const sections: AngleMapSection[] = [];
    for (const r of angle.sections) {
      const s = byRef.get(r.ref);
      if (s && !seen.has(s.ref)) {
        seen.add(s.ref);
        sections.push(s);
      }
    }
    const cells = docs.map((d) => ({
      document: d.index,
      sections: sections.filter((s) => s.document === d.index).sort((a, b) => a.seq - b.seq),
    }));
    const reached = cells.map((c, i) => (c.sections.length > 0 ? i : -1)).filter((i) => i >= 0);
    return {
      angle,
      cells,
      first: reached.length ? reached[0] : null,
      last: reached.length ? reached[reached.length - 1] : null,
    };
  });
}

/** The section behind a ref, or null. */
export function angleMapSection(map: AngleMap, ref: string): AngleMapSection | null {
  return map.sections.find((s) => s.ref === ref) ?? null;
}

/** The angles resting on a section, ascending. */
export function anglesOfSection(map: AngleMap, ref: string): AngleMapAngle[] {
  return map.angles.filter((a) => a.sections.some((s) => s.ref === ref));
}

// ---------------------------------------------------------------------------
// Which map, and how its client reads
// ---------------------------------------------------------------------------

export interface AngleMapConversationGroup<T extends Pick<AngleMapSummary, "conversation_id" | "conversation_title">> {
  conversation_id: string | null;
  conversation_title: string | null;
  maps: T[];
}

/**
 * Maps grouped by conversation for the /files list: groups in the order their
 * newest map comes (the input is newest first), maps inside a group newest
 * first. Maps with no conversation form one group of their own.
 */
export function groupAngleMapsByConversation<
  T extends Pick<AngleMapSummary, "conversation_id" | "conversation_title">,
>(maps: readonly T[]): AngleMapConversationGroup<T>[] {
  const groups = new Map<string, AngleMapConversationGroup<T>>();
  for (const m of maps) {
    const key = m.conversation_id ?? "";
    let g = groups.get(key);
    if (!g) {
      g = { conversation_id: m.conversation_id, conversation_title: m.conversation_title, maps: [] };
      groups.set(key, g);
    }
    g.maps.push(m);
  }
  return [...groups.values()];
}

/** The client's side is known (a party key the labels can use). */
export function angleClientKnown(client: Pick<AngleMapClient, "side">): boolean {
  const side = String(client.side ?? "").trim().toLowerCase();
  return side !== "" && side !== "unknown";
}

/** The map's documents a document number names, for chips («المستند 2»). */
export function angleDocumentLabel(index: number): string {
  return `المستند ${index}`;
}
