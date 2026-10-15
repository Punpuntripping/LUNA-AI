import assert from "node:assert/strict";
import { test } from "node:test";

import { parseRefusal, refusalCardCopy, type RefusalCtas } from "../library-refusal";
import {
  officialUrl,
  referenceCopyLabel,
  referenceLibraryExitLabel,
} from "../reference-labels";
import { appendReferencesForCopy } from "../references-copy";
import type { Reference, SourceView } from "../references";

/**
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

function ref(over: Partial<Reference>): Reference {
  return {
    n: 1,
    source_type: "chunk",
    domain: "regulations",
    relevance: "high",
    regulation_title: "نظام العمل",
    title: "",
    snippet: "",
    ref_id: "reg:1",
    landing_url: "",
    service_url: "",
    url: "",
    details_url: "",
    entity_name: "",
    cross_refs: [],
    source_view: null,
    has_source: true,
    ...over,
  };
}

test("officialUrl: the row's URL wins; the view is the fallback; '' = no exit", () => {
  const view: SourceView = {
    source_type: "chunk",
    title: "t",
    content: "c",
    regulation_title: "r",
    regulation_source_url: "https://view.example/reg",
  };
  assert.equal(officialUrl(ref({ landing_url: "https://row.example" }), view), "https://row.example");
  assert.equal(officialUrl(ref({}), view), "https://view.example/reg");
  assert.equal(officialUrl(ref({})), "");
  // compliance never falls back to the portal `url`.
  assert.equal(officialUrl(ref({ domain: "compliance", url: "https://portal" })), "");
});

test("library exit labels", () => {
  assert.equal(referenceLibraryExitLabel(ref({})), "فتح النظام في ريحان");
  assert.equal(referenceLibraryExitLabel(ref({ doc_type: "لائحة تنفيذية" })), "فتح اللائحة التنفيذية في ريحان");
  assert.equal(referenceLibraryExitLabel(ref({ doc_type: "غير محدد" })), "فتح النظام في ريحان");
  assert.equal(referenceLibraryExitLabel(ref({ domain: "cases" })), "فتح الحكم في ريحان");
  assert.equal(referenceLibraryExitLabel(ref({ domain: "articles", doc_type: "لائحة" })), "فتح المادة في ريحان");
  assert.equal(referenceLibraryExitLabel(ref({ domain: "compliance" })), "افتح الدليل الشامل للخدمة في ريحان");
});

test("copy labels + «المراجع» block", () => {
  const cases = ref({ n: 2, domain: "cases", title: "موضوع", case_number: "434939", hijri_year: "1443", entity_name: "وزارة العدل" });
  assert.equal(referenceCopyLabel(cases), "434939 — 1443 — وزارة العدل");
  const reg = ref({ n: 1, title: "نظام العمل" });
  assert.equal(
    appendReferencesForCopy("النص [1] [2]", [cases, reg]),
    "النص [1] [2]\n\nالمراجع\n1-نظام العمل\n2-434939 — 1443 — وزارة العدل",
  );
  assert.equal(appendReferencesForCopy("  ", [reg]), "المراجع\n1-نظام العمل");
  assert.equal(appendReferencesForCopy("x", []), "x");
});

test("parseRefusal is defensive", () => {
  assert.deepEqual(parseRefusal(null), {
    reason: "unresolvable", message: "", used: 0, limit: null, resets_at: null, stored_count: null,
  });
  assert.deepEqual(
    parseRefusal({ reason: "quota_exhausted", used: 3.7, limit: 10, resets_at: "2026-11-01", detail: "د" }),
    { reason: "quota_exhausted", message: "د", used: 3, limit: 10, resets_at: "2026-11-01", stored_count: null },
  );
});

test("refusalCardCopy: CTAs come from the caller — the app can drop or replace them", () => {
  const none: RefusalCtas = { upgrade: null, signup: null };
  for (const reason of ["quota_exhausted", "frozen_library", "locked", "anonymous", "unresolvable"] as const) {
    const card = refusalCardCopy({ reason, resetsAt: null, storedCount: 3 }, none);
    assert.equal(card.ctaHref, undefined, reason);
    assert.equal("ctaHref" in card, false, reason);
    assert.ok(card.title.length > 0 && card.body.length > 0);
  }
  const app: RefusalCtas = {
    upgrade: { ctaLabel: "عرض الباقات", ctaHref: "/paywall" },
    signup: { ctaLabel: "إنشاء حساب مجاني", ctaHref: "/signup" },
  };
  assert.equal(refusalCardCopy({ reason: "quota_exhausted", resetsAt: null, storedCount: null }, app).ctaHref, "/paywall");
  assert.equal(refusalCardCopy({ reason: "anonymous", resetsAt: null, storedCount: null }, app).ctaHref, "/signup");
  // Latin digits in the shelf count and the reset date.
  const frozen = refusalCardCopy({ reason: "frozen_library", resetsAt: null, storedCount: 12 }, none);
  assert.match(frozen.body, /لديك 12 مصدراً/);
  const quota = refusalCardCopy({ reason: "quota_exhausted", resetsAt: "2026-11-01T12:00:00Z", storedCount: null }, none);
  assert.match(quota.body, /يتجدّد رصيدك في 1 نوفمبر 2026\./);
});
