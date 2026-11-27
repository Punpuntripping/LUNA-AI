// «بحث موسّع» — the merged answer (the extended-search merger's workspace item).
//
// Several searches run in parallel on the angles (أوجه) of the user's file; the
// merger writes ONE merged answer and publishes it as an ordinary
// `agent_search` workspace item with `metadata.subtype = "extended_synthesis"`
// (backend: agents/deep_search_v4/extended_merger/publisher.py). Its
// `content_md` is the answer with `[n]` citations — references load through the
// normal `workspace_item_references` path, like every `agent_search` item.
// Everything else (the scoreboard, what the reports disagreed on, the gaps, the
// angles left out, the reports it rests on) is structured `metadata`, parsed
// here. The child reports are ordinary `agent_search` items (subtype
// `legal_synthesis`) in the same conversation.
//
// Every key may be missing or malformed on bad data: `parseExtendedSynthesis`
// never throws, drops what it cannot read, and the view hides a section whose
// data is empty.
//
// Pure TypeScript — shared by web (`@/lib/contracts`) and mobile (`@contracts`).

import type { AngleKind, AngleStrength } from "./angles";
import { PLAN_SIDES_AR, type PlanSearchSides } from "./extended-plan";

export const EXTENDED_SYNTHESIS_SUBTYPE = "extended_synthesis";

export type ExtendedConfidence = "high" | "medium" | "low";

/** The scoreboard's states, in the legend's order. */
export type ExtendedAngleState = "supported" | "answered" | "open" | "against" | "not_researched";

/** Which side a child search covered. */
export type ExtendedReportMode = "reg_compliance_led" | "case_led" | "full";

/** One angle of the file, as the planner's angle map wrote it — an automated
 *  analysis of the file before the research, not the file's own text. */
export interface ExtendedAngle {
  /** "A1", "A2", … */
  id: string;
  /** null when missing / unknown (its tag is then hidden). */
  kind: AngleKind | null;
  strength: AngleStrength | null;
  title: string;
  /** The party the angle serves, as the file names it. */
  for_party: string;
  /** Owner key of the side the angle serves, lower-case; "unknown" when not known. */
  for_side: string;
  argument: string;
  legal_question: string;
  weakness: string;
  /** Code-labelled display strings of the sections it rests on. */
  sections: string[];
  ocr_suspect: boolean;
}

/** One row of the scoreboard: where an angle stands after the research. */
export interface ExtendedAngleStatus {
  /** An angle id ("A3"). */
  angle: string;
  status: ExtendedAngleState;
  /** One sentence: the basis for the status, or what would decide an open angle. */
  note: string;
}

/** A disagreement between the reports and how the merged answer handled it. */
export interface ExtendedResolution {
  topic: string;
  /** Report numbers (1-based) that disagreed. */
  reports: number[];
  decision: string;
  /** false = both views are shown («غير محسوم — عُرض الرأيان»). */
  settled: boolean;
}

/** An angle of the file no search covered. */
export interface ExtendedLeftOut {
  angle: string;
  title: string;
  why: string;
}

/** One child search the merged answer rests on. */
export interface ExtendedReport {
  /** 1-based, the plan's order. */
  n: number;
  title: string;
  mode: ExtendedReportMode | null;
  /** Angle ids the search covered. */
  angles: string[];
  /** Its own published `agent_search` item; null when none was published. */
  item_id: string | null;
  confidence: ExtendedConfidence | null;
  /** false = the search did not complete («لم يكتمل»). */
  ok: boolean;
}

export interface ExtendedSynthesis {
  confidence: ExtendedConfidence | null;
  detail_level: string | null;
  prompt_version: string | null;
  model_used: string | null;
  /** Owner key of the user's side, lower-case; "unknown" when not known. */
  client_side: string;
  run_id: string | null;
  ref_count: number | null;
  cited_count: number | null;
  /** Item ids of the child reports, report order. */
  children: string[];
  reports: ExtendedReport[];
  angles: ExtendedAngle[];
  /** The scoreboard, in order. */
  angle_status: ExtendedAngleStatus[];
  resolved: ExtendedResolution[];
  left_out: ExtendedLeftOut[];
  gaps: string[];
  /** Reference number → the report numbers that cited it. */
  ref_reports: Record<number, number[]>;
  stats: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

const CONFIDENCES: readonly string[] = ["high", "medium", "low"];
export const EXTENDED_ANGLE_STATES: readonly ExtendedAngleState[] = [
  "supported",
  "answered",
  "open",
  "against",
  "not_researched",
];
const MODES: readonly string[] = ["reg_compliance_led", "case_led", "full"];
const KINDS: readonly string[] = ["strengthen", "attack", "answer"];
const STRENGTHS: readonly string[] = ["strong", "medium", "weak"];

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function strOrNull(v: unknown): string | null {
  const s = str(v);
  return s ? s : null;
}

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function strings(v: unknown): string[] {
  return list(v).map(str).filter(Boolean);
}

function posInt(v: unknown): number | null {
  return typeof v === "number" && Number.isInteger(v) && v >= 1 ? v : null;
}

function count(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : null;
}

function oneOf<T extends string>(v: unknown, allowed: readonly string[]): T | null {
  return typeof v === "string" && allowed.includes(v) ? (v as T) : null;
}

/** An owner key, lower-case; empty / missing → "unknown". */
export function normalizeSide(v: unknown): string {
  const s = str(v).toLowerCase();
  return s || "unknown";
}

function parseAngle(raw: unknown): ExtendedAngle | null {
  if (!isObj(raw)) return null;
  const id = str(raw.id);
  if (!id) return null;
  return {
    id,
    kind: oneOf<AngleKind>(raw.kind, KINDS),
    strength: oneOf<AngleStrength>(raw.strength, STRENGTHS),
    title: str(raw.title),
    for_party: str(raw.for_party),
    for_side: normalizeSide(raw.for_side),
    argument: str(raw.argument),
    legal_question: str(raw.legal_question),
    weakness: str(raw.weakness),
    sections: strings(raw.sections),
    ocr_suspect: raw.ocr_suspect === true,
  };
}

function parseStatus(raw: unknown): ExtendedAngleStatus | null {
  if (!isObj(raw)) return null;
  const angle = str(raw.angle);
  const status = oneOf<ExtendedAngleState>(raw.status, EXTENDED_ANGLE_STATES);
  if (!angle || !status) return null;
  return { angle, status, note: str(raw.note) };
}

function parseResolution(raw: unknown): ExtendedResolution | null {
  if (!isObj(raw)) return null;
  const topic = str(raw.topic);
  const decision = str(raw.decision);
  if (!topic && !decision) return null;
  const reports: number[] = [];
  for (const n of list(raw.reports)) {
    const v = posInt(n);
    if (v !== null && !reports.includes(v)) reports.push(v);
  }
  return { topic, reports, decision, settled: raw.settled === true };
}

function parseLeftOut(raw: unknown): ExtendedLeftOut | null {
  if (!isObj(raw)) return null;
  const angle = str(raw.angle);
  const title = str(raw.title);
  if (!angle && !title) return null;
  return { angle, title, why: str(raw.why) };
}

function parseReport(raw: unknown, index: number): ExtendedReport | null {
  if (!isObj(raw)) return null;
  return {
    n: posInt(raw.n) ?? index + 1,
    title: str(raw.title),
    mode: oneOf<ExtendedReportMode>(raw.mode, MODES),
    angles: strings(raw.angles),
    item_id: strOrNull(raw.item_id),
    confidence: oneOf<ExtendedConfidence>(raw.confidence, CONFIDENCES),
    ok: raw.ok !== false,
  };
}

function parseRefReports(raw: unknown): Record<number, number[]> {
  const out: Record<number, number[]> = {};
  if (!isObj(raw)) return out;
  for (const [key, value] of Object.entries(raw)) {
    const n = Number(key);
    if (!Number.isInteger(n) || n < 1) continue;
    const nums = list(value).map(posInt).filter((v): v is number => v !== null);
    if (nums.length) out[n] = nums;
  }
  return out;
}

function compact<T>(items: unknown[], parse: (raw: unknown, index: number) => T | null): T[] {
  const out: T[] = [];
  items.forEach((raw, i) => {
    const v = parse(raw, i);
    if (v !== null) out.push(v);
  });
  return out;
}

/** True when a workspace item's `metadata` marks the merged «بحث موسّع» answer. */
export function isExtendedSynthesis(metadata: unknown): boolean {
  return isObj(metadata) && metadata.subtype === EXTENDED_SYNTHESIS_SUBTYPE;
}

/**
 * Read an `agent_search` item's `metadata` as the merged «بحث موسّع» answer.
 * Null unless `metadata.subtype === "extended_synthesis"` (the item then renders
 * as every other `agent_search` item). Never throws: a malformed key reads as
 * empty / null, a malformed entry inside a list is dropped, and a report
 * without a valid `n` takes its position (1-based).
 */
export function parseExtendedSynthesis(metadata: unknown): ExtendedSynthesis | null {
  if (!isObj(metadata) || metadata.subtype !== EXTENDED_SYNTHESIS_SUBTYPE) return null;
  const m = metadata;
  return {
    confidence: oneOf<ExtendedConfidence>(m.confidence, CONFIDENCES),
    detail_level: strOrNull(m.detail_level),
    prompt_version: strOrNull(m.prompt_version),
    model_used: strOrNull(m.model_used),
    client_side: normalizeSide(m.client_side),
    run_id: strOrNull(m.run_id),
    ref_count: count(m.ref_count),
    cited_count: count(m.cited_count),
    children: [...new Set(strings(m.children))],
    reports: compact(list(m.reports), parseReport),
    angles: compact(list(m.angles), parseAngle),
    angle_status: compact(list(m.angle_status), parseStatus),
    resolved: compact(list(m.resolved), parseResolution),
    left_out: compact(list(m.left_out), parseLeftOut),
    gaps: strings(m.gaps),
    ref_reports: parseRefReports(m.ref_reports),
    stats: isObj(m.stats) ? m.stats : {},
  };
}

// ---------------------------------------------------------------------------
// The view's words (the accepted preview, merger_inputs_2026-11-24) — one copy
// for both apps.
// ---------------------------------------------------------------------------

export const EXTENDED_SYNTHESIS_AR = {
  chip: "بحث موسّع",
  board: "نقاط القوة والضعف",
  boardAria: "توزيع حالات الأوجه",
  mine: "نقاط قوتك",
  theirs: "نقاط الخصم وما يجب الرد عليه",
  none: "لا شيء",
  analysis: "تحليل آلي للملف قبل البحث — ليس من نص الملف",
  argument: "الحجة",
  legalQuestion: "سؤال البحث",
  weakness: "ما يضعفه",
  sections: "الأقسام",
  strength: "قوة الوجه في الملف",
  opponent: "لصالح الخصم",
  ocrSuspect: "قد يكون خطأ قراءة",
  answer: "الجواب",
  resolved: "ما حُسم بين البحوث",
  settled: "محسوم",
  unsettled: "غير محسوم — عُرض الرأيان",
  gaps: "ما لم يُحسم بعد",
  leftOut: "أوجه لم تُبحث",
  reports: "البحوث التي بُني عليها",
  openReport: "افتح البحث",
  incomplete: "لم يكتمل",
} as const;

export const EXTENDED_ANGLE_STATE_LABELS: Record<ExtendedAngleState, string> = {
  supported: "ثابت",
  answered: "له ردّ",
  open: "مفتوح",
  against: "في غير صالح الموكّل",
  not_researched: "لم يُبحث",
};

export const EXTENDED_CONFIDENCE_LABELS: Record<ExtendedConfidence, string> = {
  high: "عالية",
  medium: "متوسطة",
  low: "منخفضة",
};

const MODE_SIDES: Record<ExtendedReportMode, PlanSearchSides> = {
  reg_compliance_led: "regulations",
  case_led: "judgments",
  full: "both",
};

/** الأنظمة / الأحكام / الأنظمة والأحكام — the plan card's words; null for an unknown mode. */
export function extendedReportSideAr(mode: ExtendedReportMode | null): string | null {
  return mode ? PLAN_SIDES_AR[MODE_SIDES[mode]] : null;
}

/** «الثقة: عالية»; null when the confidence is unknown. */
export function extendedConfidenceAr(confidence: ExtendedConfidence | null): string | null {
  return confidence ? `الثقة: ${EXTENDED_CONFIDENCE_LABELS[confidence]}` : null;
}

/** «بحث 2» — a report's short name (resolution chips). */
export function extendedReportLabelAr(n: number): string {
  return `بحث ${n}`;
}

/** The report a number names, or undefined. */
export function extendedReportByNumber(s: ExtendedSynthesis, n: number): ExtendedReport | undefined {
  return s.reports.find((r) => r.n === n);
}

/** A report's title, or «بحث n» when it has none. */
export function extendedReportTitle(report: ExtendedReport): string {
  return report.title || extendedReportLabelAr(report.n);
}

/** An angle's title by id — the id itself when the angle is unknown or untitled. */
export function extendedAngleTitle(s: ExtendedSynthesis, id: string): string {
  return s.angles.find((a) => a.id === id)?.title || id;
}

// ---------------------------------------------------------------------------
// The scoreboard
// ---------------------------------------------------------------------------

export interface ExtendedBoardRow {
  angle: ExtendedAngle;
  status: ExtendedAngleState;
  note: string;
  /** false = `angle_status` names an angle `angles` does not hold (a stand-in titled with its id). */
  known: boolean;
}

export interface ExtendedBoard {
  /** Every scoreboard row, in `angle_status` order. */
  rows: ExtendedBoardRow[];
  /** «نقاط قوتك». */
  mine: ExtendedBoardRow[];
  /** «نقاط الخصم وما يجب الرد عليه». */
  theirs: ExtendedBoardRow[];
  counts: Record<ExtendedAngleState, number>;
}

function standInAngle(id: string): ExtendedAngle {
  return {
    id,
    kind: null,
    strength: null,
    title: id,
    for_party: "",
    for_side: "unknown",
    argument: "",
    legal_question: "",
    weakness: "",
    sections: [],
    ocr_suspect: false,
  };
}

/**
 * The angle is one of the user's strengths («نقاط قوتك»): it is not an
 * opponent's point to answer (`kind !== "answer"`) AND it serves the user's
 * side — its side is the user's, or either side is unknown.
 */
export function isClientAngle(angle: Pick<ExtendedAngle, "kind" | "for_side">, clientSide: string): boolean {
  const side = normalizeSide(angle.for_side);
  const client = normalizeSide(clientSide);
  return angle.kind !== "answer" && (side === client || side === "unknown" || client === "unknown");
}

/**
 * The angle serves the other side: both sides are known and differ. Then the
 * card carries «لصالح الخصم[: party]».
 */
export function isOpponentAngle(angle: Pick<ExtendedAngle, "for_side">, clientSide: string): boolean {
  const side = normalizeSide(angle.for_side);
  const client = normalizeSide(clientSide);
  return side !== "unknown" && client !== "unknown" && side !== client;
}

/** «لصالح الخصم: المدعى عليه», or «لصالح الخصم» when the party is not named. */
export function extendedOpponentTagAr(angle: Pick<ExtendedAngle, "for_party">): string {
  return angle.for_party
    ? `${EXTENDED_SYNTHESIS_AR.opponent}: ${angle.for_party}`
    : EXTENDED_SYNTHESIS_AR.opponent;
}

/** The scoreboard rows (angles joined by id) split into the two groups, with the state counts. */
export function extendedBoard(s: ExtendedSynthesis): ExtendedBoard {
  const byId = new Map(s.angles.map((a) => [a.id, a]));
  const rows: ExtendedBoardRow[] = s.angle_status.map((st) => {
    const angle = byId.get(st.angle);
    return { angle: angle ?? standInAngle(st.angle), status: st.status, note: st.note, known: !!angle };
  });
  const counts = Object.fromEntries(EXTENDED_ANGLE_STATES.map((k) => [k, 0])) as Record<ExtendedAngleState, number>;
  for (const r of rows) counts[r.status] += 1;
  const mine = rows.filter((r) => isClientAngle(r.angle, s.client_side));
  const theirs = rows.filter((r) => !isClientAngle(r.angle, s.client_side));
  return { rows, mine, theirs, counts };
}

/**
 * The header's line, as separate parts: «بُني على N بحوث» (N = completed
 * reports) · «M مرجعاً» · «الثقة: …» · «K أوجه» (K = scoreboard rows). A part
 * whose data is missing is left out. `refCount` = the item's references.
 */
export function extendedMetaPartsAr(s: ExtendedSynthesis, refCount: number | null): string[] {
  const parts: string[] = [];
  if (s.reports.length > 0) parts.push(`بُني على ${s.reports.filter((r) => r.ok).length} بحوث`);
  if (refCount !== null && refCount >= 0) parts.push(`${refCount} مرجعاً`);
  const conf = extendedConfidenceAr(s.confidence);
  if (conf) parts.push(conf);
  if (s.angle_status.length > 0) parts.push(`${s.angle_status.length} أوجه`);
  return parts;
}
