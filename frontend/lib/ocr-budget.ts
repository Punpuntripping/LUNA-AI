/**
 * OCR page budget — the composer's preview of how many pages of each attached
 * PDF will be read (plan .claude/plans/ocr_page_budget.md, D2–D6).
 *
 * MIRROR of `shared/quota/ocr_budget.py` — the server decides; this only tells
 * the user beforehand. Change both together. The preview can be slightly off
 * if another conversation spends pages between now and the send (accepted).
 */
import type { UsageReport } from "@/types";

/** D3 — a plan ending within this window lifts the per-file cap. */
const EXPIRY_GRACE_MS = 7 * 24 * 60 * 60 * 1000;
/** D6 — the middle and last blocks are each ⌊N / 5⌋ pages. */
const EDGE_DIVISOR = 5;

export interface OcrBudget {
  /** Plan's monthly pages; null = unlimited. */
  total: number | null;
  used: number;
  /** null = no per-file cap (unlimited, or the plan ends within 7 days). */
  perFileCap: number | null;
}

export interface OcrFilePreview {
  /** Pages that will be read, 1-based ascending. */
  pages: number[];
  totalPages: number;
}

export function budgetFromUsage(report: UsageReport | undefined, now = Date.now()): OcrBudget | null {
  const bar = report?.ocr?.monthly;
  if (!report || report.locked || !bar) return null;
  const total = bar.limit;
  const used = Math.max(0, Math.floor(bar.used || 0));
  if (total === null) return { total: null, used, perFileCap: null };
  const ends = report.plan?.expires_at ? Date.parse(report.plan.expires_at) : NaN;
  const endsSoon =
    !report.plan?.expired && Number.isFinite(ends) && ends > now && ends <= now + EXPIRY_GRACE_MS;
  return {
    total,
    used,
    perFileCap: endsSoon ? null : Math.max(1, Math.floor((total * 2) / 3)),
  };
}

/** D6 — which `n` pages of a `total`-page document are read. */
export function pickPages(total: number, n: number): number[] {
  if (n <= 0) return [];
  const range = (from: number, count: number) => Array.from({ length: count }, (_, i) => from + i);
  if (n >= total) return range(1, total);
  const edge = Math.floor(n / EDGE_DIVISOR);
  const first = n - 2 * edge;
  const gapLen = total - edge - first;
  const midStart = first + 1 + Math.floor((gapLen - edge) / 2);
  return [...range(1, first), ...range(midStart, edge), ...range(total - edge + 1, edge)];
}

/**
 * D4 + D5 — plan the composer's PDFs together, smallest first. Files with no
 * known page count are left out (the server learns their length when it reads them).
 */
export function planFiles(
  files: { id: string; pageCount?: number }[],
  budget: OcrBudget,
): Map<string, OcrFilePreview> {
  const known = files
    .map((f, i) => ({ ...f, i }))
    .filter((f): f is { id: string; pageCount: number; i: number } => !!f.pageCount && f.pageCount > 0)
    .sort((a, b) => a.pageCount - b.pageCount || a.i - b.i);
  let remaining = budget.total === null ? null : Math.max(0, budget.total - budget.used);
  const out = new Map<string, OcrFilePreview>();
  for (const f of known) {
    let n = f.pageCount;
    if (budget.perFileCap !== null) n = Math.min(n, budget.perFileCap);
    if (remaining !== null) n = Math.min(n, remaining);
    const pages = pickPages(f.pageCount, n);
    out.set(f.id, { pages, totalPages: f.pageCount });
    if (remaining !== null) remaining -= pages.length;
  }
  return out;
}

/** «1–60، 314–333، 587–606» — Latin digits, en dash, Arabic comma. */
export function formatRanges(pages: number[]): string {
  const runs: [number, number][] = [];
  for (const p of pages) {
    const last = runs[runs.length - 1];
    if (last && p === last[1] + 1) last[1] = p;
    else runs.push([p, p]);
  }
  return runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join("، ");
}
