import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PLAN_CARD_AR,
  parseExtendedPlan,
  planButtonLabelAr,
  planCardView,
  planCheaperLineAr,
  planCostLineAr,
  planPickRefused,
  planPointsAr,
  planSearchLineAr,
  planSearches,
  planStatusLineAr,
  type ExtendedPlanCard,
} from "../extended-plan";

// The §4.4 mock, as the backend writes it.
const RAW = {
  version: 1,
  run_id: "run-1",
  points_left: 18.7,
  options: [
    {
      id: "plan",
      label: "نفّذ الخطة",
      points: 14,
      searches: [
        { title: "استحقاق قيمة أمر التغيير والملحق", sides: "both" },
        { title: "جدول الدفعات والفواتير", sides: "regulations" },
        { title: "مبلغ 43,700 دون أمر تغيير ثانٍ", sides: "regulations" },
      ],
    },
    {
      id: "cheaper",
      label: "نفّذ الأوفر",
      points: 11,
      searches: [
        { title: "استحقاق قيمة أمر التغيير والملحق", sides: "both" },
        { title: "الدفعات والمبلغ دون أمر تغيير", sides: "regulations" },
      ],
    },
    { id: "ordinary", label: "بحث عادي", points: 5, searches: [] },
  ],
  cheaper_note: "أقدر أدمج 2 مع 3 في بحث واحد",
  cheaper_saving: 3,
  outcome: null,
  outcome_at: null,
};

function card(over: Record<string, unknown> = {}): ExtendedPlanCard {
  const out = parseExtendedPlan({ ...RAW, ...over });
  assert.ok(out);
  return out;
}

test("a valid card parses; points_left is floored, option order kept", () => {
  const c = card();
  assert.equal(c.run_id, "run-1");
  assert.equal(c.points_left, 18);
  assert.deepEqual(
    c.options.map((o) => o.id),
    ["plan", "cheaper", "ordinary"],
  );
  assert.equal(c.cheaper_saving, 3);
  assert.equal(c.outcome, null);
});

test("missing or malformed cards are null", () => {
  assert.equal(parseExtendedPlan(undefined), null);
  assert.equal(parseExtendedPlan("x"), null);
  assert.equal(parseExtendedPlan([]), null);
  assert.equal(parseExtendedPlan({ ...RAW, version: 2 }), null);
  assert.equal(parseExtendedPlan({ ...RAW, run_id: 7 }), null);
  // No `plan` option → malformed.
  assert.equal(
    parseExtendedPlan({ ...RAW, options: RAW.options.filter((o) => o.id !== "plan") }),
    null,
  );
});

test("bad options / searches / outcome are dropped, not fatal", () => {
  const c = card({
    options: [
      { ...RAW.options[0], searches: [...RAW.options[0].searches, { title: "x", sides: "web" }, null] },
      { id: "other", label: "؟", points: 1, searches: [] },
      { id: "ordinary", label: "بحث عادي", points: "5", searches: [] },
    ],
    outcome: "maybe",
  });
  assert.deepEqual(
    c.options.map((o) => o.id),
    ["plan"],
  );
  assert.equal(c.options[0].searches.length, 3);
  assert.equal(c.outcome, null);
});

test("points_left unknown → null, and the cost line drops «المتبقي لك»", () => {
  const c = card({ points_left: null });
  assert.equal(c.points_left, null);
  assert.equal(planCostLineAr(c), "التكلفة التقريبية: نحو 14 نقطة");
});

test("the card's lines (§4.4 mock)", () => {
  const c = card();
  assert.deepEqual(planSearches(c).map(planSearchLineAr), [
    "استحقاق قيمة أمر التغيير والملحق — الأنظمة والأحكام",
    "جدول الدفعات والفواتير — الأنظمة",
    "مبلغ 43,700 دون أمر تغيير ثانٍ — الأنظمة",
  ]);
  assert.equal(planCostLineAr(c), "التكلفة التقريبية: نحو 14 نقطة · المتبقي لك: 18 نقطة");
  assert.equal(planCheaperLineAr(c), "أقدر أدمج 2 مع 3 في بحث واحد — وأوفّر نحو 3 نقاط");
  assert.deepEqual(c.options.map(planButtonLabelAr), [
    "نفّذ الخطة · 14",
    "نفّذ الأوفر · 11",
    "بحث عادي · 5",
  ]);
});

test("points wording: «نقاط» for 3–10 only", () => {
  assert.equal(planPointsAr(1), "1 نقطة");
  assert.equal(planPointsAr(2), "2 نقطة");
  assert.equal(planPointsAr(3), "3 نقاط");
  assert.equal(planPointsAr(10), "10 نقاط");
  assert.equal(planPointsAr(11), "11 نقطة");
  assert.equal(planPointsAr(2.6), "3 نقاط");
});

test("cheaper line: needs a cheaper option and a note; saving falls back to plan − cheaper", () => {
  assert.equal(planCheaperLineAr(card({ cheaper_note: null })), null);
  assert.equal(
    planCheaperLineAr(card({ options: RAW.options.filter((o) => o.id !== "cheaper") })),
    null,
  );
  assert.equal(
    planCheaperLineAr(card({ cheaper_saving: null, cheaper_note: "أدمج 2 مع 3." })),
    "أدمج 2 مع 3 — وأوفّر نحو 3 نقاط",
  );
  assert.equal(
    planCheaperLineAr(card({ cheaper_saving: 0 })),
    "أقدر أدمج 2 مع 3 في بحث واحد",
  );
});

test("view: the latest unanswered card is active while nothing runs", () => {
  const c = card();
  assert.deepEqual(planCardView(c, { isLatest: true, busy: false, pick: null }), { state: "active" });
  assert.deepEqual(planCardView(c, { isLatest: true, busy: true, pick: null }), { state: "waiting" });
  assert.deepEqual(planCardView(c, { isLatest: false, busy: false, pick: null }), { state: "closed" });
  assert.equal(planStatusLineAr(c, { state: "active" }), PLAN_CARD_AR.hint);
  assert.equal(planStatusLineAr(c, { state: "closed" }), null);
});

test("view: a click locks at once and shows the pick until the outcome lands", () => {
  const c = card();
  // Sending (busy) or the reply already follows the card.
  assert.deepEqual(planCardView(c, { isLatest: true, busy: true, pick: "cheaper" }), {
    state: "chosen",
    option: "cheaper",
  });
  assert.deepEqual(planCardView(c, { isLatest: false, busy: false, pick: "cheaper" }), {
    state: "chosen",
    option: "cheaper",
  });
  // A refused send: the user row is gone, the card is the latest again.
  assert.deepEqual(planCardView(c, { isLatest: true, busy: false, pick: "cheaper" }), { state: "active" });
  assert.equal(
    planStatusLineAr(c, { state: "chosen", option: "cheaper" }),
    "تم اختيار: نفّذ الأوفر",
  );
});

test("pick refused: a send that ended with the card still latest and no outcome", () => {
  const c = card();
  // The refused send: user row gone, nothing in flight → forget the pick.
  assert.equal(planPickRefused(c, { isLatest: true, busy: false, pick: "plan" }), true);
  // Still sending (the double-click guard's window), or the reply follows the card.
  assert.equal(planPickRefused(c, { isLatest: true, busy: true, pick: "plan" }), false);
  assert.equal(planPickRefused(c, { isLatest: false, busy: false, pick: "plan" }), false);
  // No pick, or the server already answered the card.
  assert.equal(planPickRefused(c, { isLatest: true, busy: false, pick: null }), false);
  assert.equal(planPickRefused(card({ outcome: "plan" }), { isLatest: true, busy: false, pick: "plan" }), false);
  // Kept, the stale pick would read «تم اختيار» once a typed message follows the card.
  assert.deepEqual(planCardView(c, { isLatest: false, busy: false, pick: "plan" }), {
    state: "chosen",
    option: "plan",
  });
  assert.deepEqual(planCardView(c, { isLatest: false, busy: false, pick: null }), { state: "closed" });
});

test("view: the server outcome wins over everything", () => {
  const ui = { isLatest: true, busy: false, pick: null } as const;
  assert.deepEqual(planCardView(card({ outcome: "ordinary" }), { ...ui, pick: "plan" }), {
    state: "chosen",
    option: "ordinary",
  });
  for (const outcome of ["typed", "expired"]) {
    const c = card({ outcome });
    assert.deepEqual(planCardView(c, ui), { state: "expired" });
    assert.equal(planStatusLineAr(c, { state: "expired" }), "انتهت صلاحية الخطة");
  }
  const c = card({ outcome: "insufficient" });
  assert.deepEqual(planCardView(c, ui), { state: "insufficient" });
  assert.equal(planStatusLineAr(c, { state: "insufficient" }), "لم يعد رصيدك يكفي لهذه الخطة");
});

test("chosen line falls back to the fixed label when the option is missing", () => {
  const c = card({ options: RAW.options.filter((o) => o.id === "plan") });
  assert.equal(planStatusLineAr(c, { state: "chosen", option: "ordinary" }), "تم اختيار: بحث عادي");
});
