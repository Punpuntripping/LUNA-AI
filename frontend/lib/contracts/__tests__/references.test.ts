import assert from "node:assert/strict";
import { test } from "node:test";

import { parseRefusal, refusalCardCopy, type RefusalCtas } from "../library-refusal";
import {
  officialUrl,
  principleBodyLine,
  principleDecisionLines,
  principlePagesLabel,
  principleSourceText,
  referenceCopyLabel,
  referenceLabel,
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

test("official principles: label, copy label, exits, source view lines", () => {
  const body = "مجلس القضاء الأعلى بهيئته الدائمة";
  const p = ref({
    n: 3,
    domain: "principles",
    source_type: "official_principle",
    ref_id: "principle:00000000-0000-0000-0000-000000000001",
    title: "المبدأ رقم 23 — الوعد",
    regulation_title: body,
    entity_name: body,
    url: "https://drive.example/book.pdf",
    decision_line: "القرار 162/5 بتاريخ 24/3/1415هـ",
  });
  assert.equal(referenceCopyLabel(p), `المبدأ رقم 23 — الوعد — ${body}، القرار 162/5 بتاريخ 24/3/1415هـ`);
  // Empty parts drop out; the body is never duplicated.
  assert.equal(referenceCopyLabel(ref({ ...p, decision_line: undefined })), `المبدأ رقم 23 — الوعد — ${body}`);
  assert.equal(
    referenceCopyLabel(ref({ ...p, entity_name: "", regulation_title: "" })),
    "المبدأ رقم 23 — الوعد — القرار 162/5 بتاريخ 24/3/1415هـ",
  );
  assert.equal(referenceCopyLabel(ref({ ...p, title: `مبدأ — ${body}` })).split(body).length, 2);
  // A titleless principle never borrows the body's name as its title.
  assert.equal(referenceLabel(ref({ ...p, title: "" })), "مبدأ قضائي");
  assert.equal(principleBodyLine(p), body);
  assert.equal(principleBodyLine(ref({})), "");
  // The official exit is the source PDF, from the row or (fallback) the view.
  assert.equal(officialUrl(p), "https://drive.example/book.pdf");
  const view: SourceView = {
    source_type: "official_principle",
    title: "المبدأ رقم 23 — الوعد",
    chapter: "الوعد",
    text: "الوعد ملزم ديانةً لا قضاءً.",
    decisions: [
      { body, decision_no: "162/5", date_hijri: "24/3/1415" },
      { body: "المحكمة العليا", decision_no: "", date_hijri: "1/2/1440هـ" },
    ],
    pages: [344, 343],
    url: "https://drive.example/view.pdf",
  };
  assert.equal(officialUrl(ref({ ...p, url: "" }), view), "https://drive.example/view.pdf");
  assert.equal(referenceLibraryExitLabel(p), "فتح المبدأ في ريحان");
  assert.deepEqual(principleDecisionLines(view), [
    `${body} — القرار 162/5 بتاريخ 24/3/1415هـ`,
    "المحكمة العليا — بتاريخ 1/2/1440هـ",
  ]);
  assert.equal(principlePagesLabel(view.pages), "ص 343–344");
  assert.equal(principlePagesLabel([343]), "ص 343");
  assert.equal(principlePagesLabel([350, 343]), "ص 343، 350");
  assert.equal(principlePagesLabel([]), "");
  assert.equal(
    principleSourceText(view),
    `الوعد ملزم ديانةً لا قضاءً.

${body} — القرار 162/5 بتاريخ 24/3/1415هـ
المحكمة العليا — بتاريخ 1/2/1440هـ
ص 343–344`,
  );
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
