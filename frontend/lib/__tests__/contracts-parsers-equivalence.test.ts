import assert from "node:assert/strict";
import { test } from "node:test";

import { z } from "zod";

import { parseNextSteps } from "../contracts/next-steps";
import { isHttpUrlLike, parseTemplatesUsed } from "../contracts/templates-used";

/**
 * Pins the zod-free `next_steps` / `templates_used` parsers in `lib/contracts`
 * to the zod parsers the web shipped with (copied VERBATIM below from
 * `lib/next-steps.ts` / `lib/templates-used.ts` as of 2026-10-15), on the same
 * fixtures plus a seeded random sweep. The web files re-export the contracts
 * port only because this passes.
 *
 * 2026-10-15 (unified_responder plan §6.3): the reference below was extended
 * with the `answer` kind — exempt from one-per-kind dedup, at most two kept,
 * total cap still 3 — and the explicit `answer` cases at the bottom pin that
 * rule independently of the reference.
 *
 * Lives outside `lib/contracts/` because it imports zod (contracts may not).
 *
 * Run:  cd frontend && npx tsx --test lib/__tests__/contracts-parsers-equivalence.test.ts
 */

// ---- zod originals (verbatim, plus the `answer` rule) ------------------------

const MAX_NEXT_STEPS = 3;
const MAX_ANSWER_STEPS = 2;

const nextStepSchema = z.object({
  kind: z.enum(["narrow_search", "draft", "apply", "open", "answer"]),
  label: z.string().trim().min(1),
  prompt: z.string().trim().min(1),
});

function zodParseNextSteps(raw: unknown) {
  if (!Array.isArray(raw)) return [];
  const out: z.infer<typeof nextStepSchema>[] = [];
  const seenKinds = new Set<string>();
  let answers = 0;
  for (const item of raw) {
    const parsed = nextStepSchema.safeParse(item);
    if (!parsed.success) continue;
    if (parsed.data.kind === "answer") {
      if (answers >= MAX_ANSWER_STEPS) continue;
      answers += 1;
    } else {
      if (seenKinds.has(parsed.data.kind)) continue;
      seenKinds.add(parsed.data.kind);
    }
    out.push(parsed.data);
    if (out.length >= MAX_NEXT_STEPS) break;
  }
  return out;
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

const templateUsedSchema = z.object({
  template_id: z.string().trim().min(1),
  title: z.string().trim().min(1),
  scope: z.enum(["user", "system"]),
  subtype: z.string().nullable().optional(),
  court: z.string().nullable().optional(),
  sources: z.array(z.unknown()).optional(),
});

function zodParseTemplatesUsed(raw: unknown) {
  if (!Array.isArray(raw)) return undefined;
  const out: unknown[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const parsed = templateUsedSchema.safeParse(item);
    if (!parsed.success) continue;
    const { template_id, title, scope, subtype, court, sources } = parsed.data;
    if (seen.has(template_id)) continue;
    seen.add(template_id);
    out.push({
      template_id,
      title,
      scope,
      subtype: subtype ?? null,
      court: court ?? null,
      sources: (sources ?? []).filter(
        (s): s is string => typeof s === "string" && isHttpUrl(s.trim()),
      ).map((s) => s.trim()),
    });
  }
  return out;
}

// ---- fixtures ---------------------------------------------------------------

const ODD_VALUES: unknown[] = [
  undefined, null, 0, 1, NaN, true, "", " ", "  x  ", "x", [], {}, [1], ["a"],
  new Date(0), new Map(), new Set(), Promise.resolve(1), Object.create(null),
  { then: () => 0, catch: () => 0 }, new String("draft"), () => 0,
];

const NEXT_STEP_FIXTURES: unknown[] = [
  undefined, null, "x", 3, {}, [],
  [{ kind: "draft", label: "صياغة", prompt: "اكتب لي مذكرة" }],
  [
    { kind: "draft", label: "  أ  ", prompt: " ب " },
    { kind: "draft", label: "مكرر", prompt: "مكرر" },
    { kind: "apply", label: "ت", prompt: "ث" },
    { kind: "open", label: "ج", prompt: "ح" },
    { kind: "narrow_search", label: "خ", prompt: "د" },
  ],
  [
    { kind: "bogus", label: "a", prompt: "b" },
    { kind: "draft", label: "   ", prompt: "b" },
    { kind: "draft", label: "a", prompt: "" },
    { kind: "draft", label: 5, prompt: "b" },
    { kind: "DRAFT", label: "a", prompt: "b" },
    null, "str", [], new Date(), { kind: "apply", label: "a", prompt: "b", extra: 1 },
  ],
  [Object.assign(Object.create({ kind: "open" }), { label: "inherited", prompt: "p" })],
  [{ kind: "open", label: " x ", prompt: "\ny\t" }],
  // eslint-disable-next-line no-sparse-arrays
  [, { kind: "draft", label: "a", prompt: "b" }],
  [
    { kind: "answer", label: "نعم، يوجد شرط جزائي", prompt: "نعم، ينص العقد على شرط جزائي بقيمة …" },
    { kind: "answer", label: "لا يوجد شرط جزائي", prompt: "لا، لا ينص العقد على شرط جزائي" },
    { kind: "answer", label: "ثالث", prompt: "ثالث" },
    { kind: "draft", label: "صياغة إنذار", prompt: "صغ لي إنذاراً للمطور" },
  ],
  [
    { kind: "answer", label: "  ", prompt: "x" },
    { kind: "answer", label: "a", prompt: "b" },
    { kind: "answer", label: "a", prompt: "b" },
    { kind: "answer", label: "c", prompt: "d" },
  ],
];

const URLS = [
  "https://rayhanai.com/blog/abc",
  "http://example.com",
  "  https://example.com/x  ",
  "HTTPS://EXAMPLE.COM",
  "ftp://example.com",
  "javascript:alert(1)",
  "example.com",
  "/relative",
  "",
  "https://",
  "http:example.com",
  "https://exa mple.com",
  "https://user@host.com",
  "https://[::1]/",
  "https://1.2.3.999/",
  "https://example.com/path?q=1#h",
  "https://مثال.السعودية/مسار",
];

const TEMPLATE_FIXTURES: unknown[] = [
  undefined, null, "x", {}, [],
  [{ template_id: "t1", title: "عقد", scope: "user", subtype: null, court: null, sources: [] }],
  [{ template_id: " t1 ", title: " عقد ", scope: "system" }],
  [
    { template_id: "t1", title: "a", scope: "user" },
    { template_id: "t1", title: "dup", scope: "user" },
    { template_id: "t2", title: "b", scope: "admin" },
    { template_id: "", title: "b", scope: "user" },
    { template_id: "t3", title: "  ", scope: "user" },
    { template_id: "t4", title: "c", scope: "user", subtype: 5 },
    { template_id: "t5", title: "c", scope: "user", court: false },
    { template_id: "t6", title: "c", scope: "user", sources: null },
    { template_id: "t7", title: "c", scope: "user", sources: "https://x.com" },
    { template_id: "t8", title: "c", scope: "user", sources: [1, null, ...URLS] },
    { template_id: 9, title: "c", scope: "user" },
    null, [], new Date(),
  ],
  // eslint-disable-next-line no-sparse-arrays
  [{ template_id: "t9", title: "c", scope: "user", sources: [, "https://a.com", , "x"] }],
];

// Seeded PRNG (mulberry32) so a failure is reproducible.
function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick<T>(r: () => number, xs: readonly T[]): T {
  return xs[Math.floor(r() * xs.length)];
}

function randomNextStep(r: () => number): unknown {
  if (r() < 0.15) return pick(r, ODD_VALUES);
  const o: Record<string, unknown> = {};
  if (r() < 0.9) o.kind = pick(r, ["narrow_search", "draft", "apply", "open", "answer", "answer", "x", "", 1, null]);
  if (r() < 0.9) o.label = pick(r, ["a", " a ", "", "  ", "\t", 1, null, undefined, "نص"]);
  if (r() < 0.9) o.prompt = pick(r, ["p", " p ", "", "\n", 0, null, "طلب"]);
  return o;
}

function randomTemplate(r: () => number): unknown {
  if (r() < 0.15) return pick(r, ODD_VALUES);
  const o: Record<string, unknown> = {};
  if (r() < 0.9) o.template_id = pick(r, ["t1", "t2", " t1", "", " ", 1, null]);
  if (r() < 0.9) o.title = pick(r, ["a", " b ", "", 2, null]);
  if (r() < 0.9) o.scope = pick(r, ["user", "system", "x", null]);
  if (r() < 0.5) o.subtype = pick(r, ["s", null, undefined, 1, ""]);
  if (r() < 0.5) o.court = pick(r, ["c", null, undefined, true]);
  if (r() < 0.6) {
    o.sources = r() < 0.2
      ? pick(r, [null, "https://a.com", 1, {}])
      : Array.from({ length: Math.floor(r() * 4) }, () => pick(r, [...URLS, 1, null]));
  }
  return o;
}

// ---- tests ------------------------------------------------------------------

test("parseNextSteps: contracts port ≡ zod original (fixtures)", () => {
  for (const raw of NEXT_STEP_FIXTURES) {
    assert.deepStrictEqual(parseNextSteps(raw), zodParseNextSteps(raw));
  }
  for (const v of ODD_VALUES) {
    assert.deepStrictEqual(parseNextSteps([v]), zodParseNextSteps([v]));
    assert.deepStrictEqual(parseNextSteps(v), zodParseNextSteps(v));
  }
});

test("parseNextSteps: contracts port ≡ zod original (5,000 random lists)", () => {
  const r = rng(20261015);
  for (let i = 0; i < 5000; i++) {
    const raw = Array.from({ length: Math.floor(r() * 6) }, () => randomNextStep(r));
    assert.deepStrictEqual(parseNextSteps(raw), zodParseNextSteps(raw), JSON.stringify(raw));
  }
});

// ---- answer kind (unified_responder plan §6.3) ------------------------------

const ANS_1 = { kind: "answer", label: "نعم، يوجد شرط جزائي", prompt: "نعم، ينص العقد على شرط جزائي بقيمة …" };
const ANS_2 = { kind: "answer", label: "لا يوجد شرط جزائي", prompt: "لا، لا ينص العقد على شرط جزائي" };
const ANS_3 = { kind: "answer", label: "يوجد مهلة سماح", prompt: "ينص العقد على مهلة سماح مدتها …" };
const DRAFT = { kind: "draft", label: "صياغة إنذار للمطور", prompt: "صغ لي إنذاراً للمطور" };

test("parseNextSteps: 2 answer chips + 1 action are all kept, in order", () => {
  assert.deepStrictEqual(parseNextSteps([ANS_1, ANS_2, DRAFT]), [ANS_1, ANS_2, DRAFT]);
  assert.deepStrictEqual(parseNextSteps([ANS_1, DRAFT, ANS_2]), [ANS_1, DRAFT, ANS_2]);
});

test("parseNextSteps: 3 answer chips → the first 2 are kept", () => {
  assert.deepStrictEqual(parseNextSteps([ANS_1, ANS_2, ANS_3]), [ANS_1, ANS_2]);
  // The skipped 3rd answer does not use up the total cap.
  assert.deepStrictEqual(parseNextSteps([ANS_1, ANS_2, ANS_3, DRAFT]), [ANS_1, ANS_2, DRAFT]);
});

test("parseNextSteps: a duplicate non-answer kind is still deduped", () => {
  const draft2 = { kind: "draft", label: "مكرر", prompt: "مكرر" };
  const apply = { kind: "apply", label: "تطبيق", prompt: "طبّق" };
  assert.deepStrictEqual(parseNextSteps([DRAFT, draft2, apply]), [DRAFT, apply]);
  assert.deepStrictEqual(parseNextSteps([ANS_1, DRAFT, draft2]), [ANS_1, DRAFT]);
});

test("parseNextSteps: an unknown kind is dropped", () => {
  const bogus = { kind: "reply", label: "a", prompt: "b" };
  assert.deepStrictEqual(parseNextSteps([bogus, ANS_1]), [ANS_1]);
  assert.deepStrictEqual(parseNextSteps([{ ...ANS_1, kind: "ANSWER" }]), []);
});

test("parseNextSteps: total cap stays 3 with answer chips", () => {
  const apply = { kind: "apply", label: "تطبيق", prompt: "طبّق" };
  assert.deepStrictEqual(parseNextSteps([ANS_1, DRAFT, apply, ANS_2]), [ANS_1, DRAFT, apply]);
});

test("parseTemplatesUsed: contracts port + web URL check ≡ zod original (fixtures)", () => {
  for (const raw of TEMPLATE_FIXTURES) {
    assert.deepStrictEqual(parseTemplatesUsed(raw, isHttpUrl), zodParseTemplatesUsed(raw));
  }
  for (const v of ODD_VALUES) {
    assert.deepStrictEqual(parseTemplatesUsed([v], isHttpUrl), zodParseTemplatesUsed([v]));
    assert.deepStrictEqual(parseTemplatesUsed(v, isHttpUrl), zodParseTemplatesUsed(v));
  }
});

test("parseTemplatesUsed: contracts port + web URL check ≡ zod original (5,000 random lists)", () => {
  const r = rng(1015);
  for (let i = 0; i < 5000; i++) {
    const raw = Array.from({ length: Math.floor(r() * 5) }, () => randomTemplate(r));
    assert.deepStrictEqual(
      parseTemplatesUsed(raw, isHttpUrl),
      zodParseTemplatesUsed(raw),
      JSON.stringify(raw),
    );
  }
});

test("isHttpUrlLike (mobile default) agrees with WHATWG URL on every realistic URL", () => {
  const realistic = [
    "https://rayhanai.com/blog/abc",
    "http://example.com",
    "HTTPS://EXAMPLE.COM",
    "https://example.com/path?q=1#h",
    "https://مثال.السعودية/مسار",
    "ftp://example.com",
    "javascript:alert(1)",
    "example.com",
    "/relative",
    "",
    "https://",
    "https://exa mple.com",
  ];
  for (const u of realistic) assert.equal(isHttpUrlLike(u), isHttpUrl(u), u);
  // Documented divergences (see templates-used.ts) — pinned so they are deliberate.
  assert.equal(isHttpUrlLike("http:example.com"), false);
  assert.equal(isHttpUrl("http:example.com"), true);
  assert.equal(isHttpUrlLike("https://user@host.com"), false);
  assert.equal(isHttpUrlLike("https://1.2.3.999/"), true);
  assert.equal(isHttpUrl("https://1.2.3.999/"), false);
});
