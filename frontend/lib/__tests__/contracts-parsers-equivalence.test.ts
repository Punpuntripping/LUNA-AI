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
 * Lives outside `lib/contracts/` because it imports zod (contracts may not).
 *
 * Run:  cd frontend && npx tsx --test lib/__tests__/contracts-parsers-equivalence.test.ts
 */

// ---- verbatim zod originals -------------------------------------------------

const MAX_NEXT_STEPS = 3;

const nextStepSchema = z.object({
  kind: z.enum(["narrow_search", "draft", "apply", "open"]),
  label: z.string().trim().min(1),
  prompt: z.string().trim().min(1),
});

function zodParseNextSteps(raw: unknown) {
  if (!Array.isArray(raw)) return [];
  const out: z.infer<typeof nextStepSchema>[] = [];
  const seenKinds = new Set<string>();
  for (const item of raw) {
    const parsed = nextStepSchema.safeParse(item);
    if (!parsed.success) continue;
    if (seenKinds.has(parsed.data.kind)) continue;
    seenKinds.add(parsed.data.kind);
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
  if (r() < 0.9) o.kind = pick(r, ["narrow_search", "draft", "apply", "open", "x", "", 1, null]);
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
