import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EXTENDED_SYNTHESIS_AR,
  extendedAngleTitle,
  extendedBoard,
  extendedConfidenceAr,
  extendedMetaPartsAr,
  extendedOpponentTagAr,
  extendedReportByNumber,
  extendedReportSideAr,
  extendedReportTitle,
  isClientAngle,
  isExtendedSynthesis,
  isOpponentAngle,
  parseExtendedSynthesis,
  type ExtendedSynthesis,
} from "../extended-synthesis";

// Invented text only — a made-up supply contract dispute.
const RAW = {
  subtype: "extended_synthesis",
  confidence: "medium",
  detail_level: "medium",
  prompt_version: "merger-v1",
  model_used: "test-model",
  client_side: "claimant",
  run_id: "run-1",
  ref_count: 6,
  cited_count: 5,
  children: ["item-r1", "item-r2", "item-r1"],
  reports: [
    { n: 1, title: "التأخر في التوريد", mode: "full", angles: ["A1", "A2"], item_id: "item-r1", confidence: "high", ok: true },
    { n: 2, title: "الشرط الجزائي", mode: "reg_compliance_led", angles: ["A3"], item_id: "item-r2", confidence: "medium", ok: true },
    { n: 3, title: "سوابق قضائية", mode: "case_led", angles: ["A4"], item_id: null, confidence: null, ok: false },
  ],
  angles: [
    { id: "A1", kind: "strengthen", strength: "strong", title: "تأخر المورد ثابت بالمراسلات", for_party: "المدعي", for_side: "claimant", argument: "حجة أولى", legal_question: "سؤال أول", weakness: "", sections: ["المستند 1 · القسم 2"], ocr_suspect: false },
    { id: "A2", kind: "attack", strength: "medium", title: "الإشعار لم يُرسل في موعده", for_party: "المدعي", for_side: "unknown", argument: "حجة ثانية", legal_question: "", weakness: "ضعف ما", sections: [], ocr_suspect: true },
    { id: "A3", kind: "answer", strength: "weak", title: "دفع الخصم بالقوة القاهرة", for_party: "المدعى عليه", for_side: "respondent", argument: "", legal_question: "", weakness: "", sections: [], ocr_suspect: false },
    { id: "A4", kind: "strengthen", strength: "strong", title: "حجة تخدم الخصم", for_party: "المدعى عليه", for_side: "Respondent", argument: "", legal_question: "", weakness: "", sections: [], ocr_suspect: false },
  ],
  angle_status: [
    { angle: "A1", status: "supported", note: "تدعمه المادة المذكورة." },
    { angle: "A2", status: "open", note: "يحسمه تاريخ الإشعار." },
    { angle: "A3", status: "answered", note: "للدفع رد في العقد." },
    { angle: "A4", status: "against", note: "يميل الحكم لصالح الخصم." },
    { angle: "A9", status: "not_researched", note: "" },
  ],
  resolved: [
    { topic: "بداية احتساب المدة", reports: [1, 2], decision: "من تاريخ الإشعار.", settled: true },
    { topic: "مقدار التعويض", reports: [2, 3], decision: "عُرض الرأيان.", settled: false },
  ],
  left_out: [{ angle: "A5", title: "مسألة الاختصاص", why: "خارج نطاق السؤال." }],
  gaps: ["لم يُعثر على نص صريح في المدة.", "  "],
  ref_reports: { "1": [1, 2], "2": [3] },
  stats: { shared_refs: 2 },
};

function parsed(over: Record<string, unknown> = {}): ExtendedSynthesis {
  const out = parseExtendedSynthesis({ ...RAW, ...over });
  assert.ok(out);
  return out;
}

test("a valid item parses; ids, numbers and labels kept", () => {
  const s = parsed();
  assert.equal(s.confidence, "medium");
  assert.equal(s.client_side, "claimant");
  assert.equal(s.ref_count, 6);
  assert.deepEqual(s.children, ["item-r1", "item-r2"]);
  assert.deepEqual(
    s.reports.map((r) => [r.n, r.mode, r.item_id, r.confidence, r.ok]),
    [
      [1, "full", "item-r1", "high", true],
      [2, "reg_compliance_led", "item-r2", "medium", true],
      [3, "case_led", null, null, false],
    ],
  );
  assert.equal(s.angles.length, 4);
  assert.equal(s.angles[3].for_side, "respondent");
  assert.equal(s.angle_status.length, 5);
  assert.equal(s.resolved.length, 2);
  assert.deepEqual(s.gaps, ["لم يُعثر على نص صريح في المدة."]);
  assert.deepEqual(s.ref_reports, { 1: [1, 2], 2: [3] });
  assert.deepEqual(s.stats, { shared_refs: 2 });
});

test("only the extended_synthesis subtype parses", () => {
  assert.equal(parseExtendedSynthesis(undefined), null);
  assert.equal(parseExtendedSynthesis(null), null);
  assert.equal(parseExtendedSynthesis("x"), null);
  assert.equal(parseExtendedSynthesis([]), null);
  assert.equal(parseExtendedSynthesis({ ...RAW, subtype: "legal_synthesis" }), null);
  assert.equal(parseExtendedSynthesis({}), null);
  assert.equal(isExtendedSynthesis(RAW), true);
  assert.equal(isExtendedSynthesis({ subtype: "legal_synthesis" }), false);
  assert.equal(isExtendedSynthesis(null), false);
});

test("the subtype alone parses to an empty, safe shape", () => {
  const s = parsed({
    confidence: undefined,
    client_side: undefined,
    run_id: undefined,
    ref_count: undefined,
    cited_count: undefined,
    children: undefined,
    reports: undefined,
    angles: undefined,
    angle_status: undefined,
    resolved: undefined,
    left_out: undefined,
    gaps: undefined,
    ref_reports: undefined,
    stats: undefined,
  });
  assert.equal(s.confidence, null);
  assert.equal(s.client_side, "unknown");
  assert.equal(s.ref_count, null);
  assert.deepEqual([s.reports, s.angles, s.angle_status, s.resolved, s.left_out, s.gaps, s.children], [[], [], [], [], [], [], []]);
  assert.deepEqual(s.ref_reports, {});
  assert.deepEqual(extendedBoard(s).rows, []);
  assert.deepEqual(extendedMetaPartsAr(s, null), []);
});

test("malformed entries are dropped, not fatal", () => {
  const s = parsed({
    confidence: "certain",
    ref_count: -3,
    children: "item-r1",
    reports: [null, "x", { title: "بلا رقم", mode: "web", item_id: "", ok: "yes" }],
    angles: [{ title: "بلا معرّف" }, { id: "A1", kind: "praise", strength: 7, sections: [1, "س 1"] }],
    angle_status: [{ angle: "A1", status: "maybe" }, { status: "open" }, { angle: "A1", status: "open", note: 4 }],
    resolved: [{ reports: [1] }, { topic: "موضوع", reports: [0, 1.5, 2, 2, "3"] }],
    left_out: [{ why: "سبب فقط" }, { angle: "A7" }],
    gaps: "نص",
    ref_reports: { x: [1], "0": [1], "3": [1, "2", 0], "4": [] },
  });
  assert.equal(s.confidence, null);
  assert.equal(s.ref_count, null);
  assert.deepEqual(s.children, []);
  // Non-objects dropped; a report without a valid n takes its position (here 3rd → 3).
  assert.equal(s.reports.length, 1);
  assert.deepEqual(s.reports[0], {
    n: 3,
    title: "بلا رقم",
    mode: null,
    angles: [],
    item_id: null,
    confidence: null,
    ok: true,
  });
  assert.equal(s.angles.length, 1);
  assert.equal(s.angles[0].kind, null);
  assert.equal(s.angles[0].strength, null);
  assert.deepEqual(s.angles[0].sections, ["س 1"]);
  assert.deepEqual(s.angle_status, [{ angle: "A1", status: "open", note: "" }]);
  assert.deepEqual(s.resolved, [{ topic: "موضوع", reports: [2], decision: "", settled: false }]);
  assert.deepEqual(s.left_out, [{ angle: "A7", title: "", why: "" }]);
  assert.deepEqual(s.gaps, []);
  assert.deepEqual(s.ref_reports, { 3: [1] });
});

test("the scoreboard: rows in order, unknown angles get a stand-in, counts per state", () => {
  const b = extendedBoard(parsed());
  assert.deepEqual(
    b.rows.map((r) => [r.angle.id, r.status, r.known]),
    [
      ["A1", "supported", true],
      ["A2", "open", true],
      ["A3", "answered", true],
      ["A4", "against", true],
      ["A9", "not_researched", false],
    ],
  );
  assert.equal(b.rows[4].angle.title, "A9");
  assert.deepEqual(b.counts, { supported: 1, answered: 1, open: 1, against: 1, not_researched: 1 });
});

test("the two groups: the client's strengths vs the opponent's points", () => {
  const b = extendedBoard(parsed());
  // A1 serves the claimant; A2's side is unknown; the stand-in A9 has an unknown side.
  assert.deepEqual(b.mine.map((r) => r.angle.id), ["A1", "A2", "A9"]);
  // A3 is a point to answer; A4 serves the respondent.
  assert.deepEqual(b.theirs.map((r) => r.angle.id), ["A3", "A4"]);
});

test("client side unknown: everything but the points to answer is «نقاط قوتك»", () => {
  const b = extendedBoard(parsed({ client_side: "unknown" }));
  assert.deepEqual(b.mine.map((r) => r.angle.id), ["A1", "A2", "A4", "A9"]);
  assert.deepEqual(b.theirs.map((r) => r.angle.id), ["A3"]);
  // An empty side reads as unknown too.
  assert.deepEqual(extendedBoard(parsed({ client_side: "" })).theirs.map((r) => r.angle.id), ["A3"]);
});

test("isClientAngle / isOpponentAngle / the opponent tag", () => {
  assert.equal(isClientAngle({ kind: "strengthen", for_side: "claimant" }, "claimant"), true);
  assert.equal(isClientAngle({ kind: "answer", for_side: "claimant" }, "claimant"), false);
  assert.equal(isClientAngle({ kind: "attack", for_side: "respondent" }, "claimant"), false);
  assert.equal(isClientAngle({ kind: null, for_side: "unknown" }, "claimant"), true);
  assert.equal(isOpponentAngle({ for_side: "respondent" }, "claimant"), true);
  assert.equal(isOpponentAngle({ for_side: "claimant" }, "claimant"), false);
  assert.equal(isOpponentAngle({ for_side: "unknown" }, "claimant"), false);
  assert.equal(isOpponentAngle({ for_side: "respondent" }, "unknown"), false);
  assert.equal(extendedOpponentTagAr({ for_party: "المدعى عليه" }), "لصالح الخصم: المدعى عليه");
  assert.equal(extendedOpponentTagAr({ for_party: "" }), EXTENDED_SYNTHESIS_AR.opponent);
});

test("the header line: completed reports, references, confidence, angles", () => {
  const s = parsed();
  assert.deepEqual(extendedMetaPartsAr(s, 6), ["بُني على 2 بحوث", "6 مرجعاً", "الثقة: متوسطة", "5 أوجه"]);
  // Unknown reference count and confidence → those parts are left out.
  assert.deepEqual(extendedMetaPartsAr(parsed({ confidence: null }), null), ["بُني على 2 بحوث", "5 أوجه"]);
});

test("report helpers: side, confidence, title, lookup, angle titles", () => {
  const s = parsed();
  assert.equal(extendedReportSideAr("reg_compliance_led"), "الأنظمة");
  assert.equal(extendedReportSideAr("case_led"), "الأحكام");
  assert.equal(extendedReportSideAr("full"), "الأنظمة والأحكام");
  assert.equal(extendedReportSideAr(null), null);
  assert.equal(extendedConfidenceAr("high"), "الثقة: عالية");
  assert.equal(extendedConfidenceAr(null), null);
  assert.equal(extendedReportByNumber(s, 2)?.item_id, "item-r2");
  assert.equal(extendedReportByNumber(s, 9), undefined);
  assert.equal(extendedReportTitle({ ...s.reports[0], title: "" }), "بحث 1");
  assert.equal(extendedAngleTitle(s, "A3"), "دفع الخصم بالقوة القاهرة");
  assert.equal(extendedAngleTitle(s, "A9"), "A9");
});
