#!/usr/bin/env node
// Generates src/theme/tokens.generated.ts from the web's design tokens
// (../frontend/app/globals.css) so the app and the web share ONE palette.
//
//   node scripts/gen-tokens.mjs          (from mobile/)
//
// Re-run after every /theme change on the web. No dependencies.
//
// Reads three blocks:
//   :root { … }                         light palette + radius/space/type/motion
//   @media (max-width: 639px) :root {…} phone type tiers (what a handset renders)
//   .dark, [data-theme="dark"] { … }    dark overrides (cascade over light)
//
// Colours already in hex/rgba pass through (React Native accepts both). OKLCH
// is converted to sRGB hex here. Any other colour syntax FAILS the script —
// silently dropping a token would ship a wrong colour.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const cssPath = resolve(here, "../../frontend/app/globals.css");
const outPath = resolve(here, "../src/theme/tokens.generated.ts");

const css = readFileSync(cssPath, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function fail(msg) {
  console.error(`gen-tokens: ${msg}`);
  process.exit(1);
}

/** Body of the first `{…}` block that starts after `marker` (brace-matched). */
function blockAfter(marker, from = 0) {
  const at = css.indexOf(marker, from);
  if (at < 0) fail(`block not found: ${marker}`);
  const open = css.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}") {
      depth--;
      if (depth === 0) return { body: css.slice(open + 1, i), end: i };
    }
  }
  return fail(`unbalanced braces after: ${marker}`);
}

function declarations(body) {
  const out = {};
  for (const m of body.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    out[m[1]] = m[2].trim().replace(/\s+/g, " ");
  }
  return out;
}

const camel = (name) => name.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase());

// ---------- OKLCH → sRGB ----------
function oklchToHex(value) {
  const m = value.match(/^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)\s*\)$/);
  if (!m) fail(`unsupported oklch(): ${value}`);
  const L = m[2] === "%" ? Number(m[1]) / 100 : Number(m[1]);
  const C = Number(m[3]);
  const h = (Number(m[4]) * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const mm = m_ ** 3;
  const s = s_ ** 3;
  const lin = [
    4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s,
  ];
  const hex = lin
    .map((x) => {
      const v = x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
      const n = Math.round(Math.min(1, Math.max(0, v)) * 255);
      return n.toString(16).padStart(2, "0");
    })
    .join("");
  return `#${hex.toUpperCase()}`;
}

const isHex = (v) => /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(v);
const isRgba = (v) => /^rgba?\([\d.\s,%]+\)$/.test(v);

function resolveVars(value, scope) {
  return value.replace(/var\(--([\w-]+)\)/g, (_, name) => {
    if (!(name in scope)) fail(`unresolved var(--${name})`);
    return resolveVars(scope[name], scope);
  });
}

function toColour(value, name) {
  if (isHex(value)) return value.toUpperCase();
  if (isRgba(value)) return value;
  if (value.startsWith("oklch(")) return oklchToHex(value);
  return fail(`unsupported colour for --${name}: ${value}`);
}

const looksLikeColour = (v) =>
  isHex(v) || isRgba(v) || v.startsWith("oklch(") || v.startsWith("hsl(") || v.startsWith("color(");

const SHADOW_KEYS = /^(shadow-|ring-focus$)/;
const px = (v, name) => {
  const m = v.match(/^(-?[\d.]+)px$/);
  if (!m) fail(`expected px for --${name}: ${v}`);
  return Number(m[1]);
};
const rem = (v, name) => {
  const m = v.match(/^([\d.]+)rem$/);
  if (!m) fail(`expected rem for --${name}: ${v}`);
  return Number(m[1]) * 16;
};

// ---------- read the three blocks ----------
const rootBlock = blockAfter(":root {");
const light = declarations(rootBlock.body);
const phoneAt = css.indexOf("@media (max-width: 639px)", rootBlock.end);
if (phoneAt < 0) fail("phone type override (@media max-width: 639px) not found");
const phone = declarations(blockAfter(":root {", phoneAt).body);
const darkAt = css.search(/\.dark,\s*\[data-theme="dark"\]\s*\{/);
if (darkAt < 0) fail('dark block (.dark, [data-theme="dark"]) not found');
const darkOverrides = declarations(blockAfter("{", darkAt).body);
if (Object.keys(darkOverrides).length < 20) fail("dark block looks wrong (too few tokens)");

function palette(scope) {
  const colours = {};
  const shadows = {};
  for (const [name, raw] of Object.entries(scope)) {
    if (SHADOW_KEYS.test(name)) {
      shadows[camel(name)] = resolveVars(raw, scope)
        .replace(/oklch\([^)]*\)/g, (o) => oklchToHex(o));
      continue;
    }
    // Only a bare `var(--x)` is a colour alias; composite values (font stacks)
    // may reference variables the CSS never defines (next/font's --font-arabic).
    const value = /^var\(--[\w-]+\)$/.test(raw) ? resolveVars(raw, scope) : raw;
    if (!looksLikeColour(value)) continue;
    colours[camel(name)] = toColour(value, name);
  }
  return { colours, shadows };
}

const lightPal = palette(light);
const darkScope = { ...light, ...darkOverrides };
const darkPal = palette(darkScope);

// Dark-only keys have no light counterpart (the web resolves them per theme).
// Keep the two palettes key-identical so `Colours` is one type.
const skipped = Object.keys(darkPal.colours).filter((k) => !(k in lightPal.colours));
for (const k of skipped) delete darkPal.colours[k];

const radius = {};
const space = {};
const textSize = {};
for (const [name, raw] of Object.entries(light)) {
  if (name.startsWith("radius")) radius[camel(name.replace(/^radius-?/, "") || "default")] = px(raw, name);
  else if (name.startsWith("space-")) space[name.slice(6)] = px(raw, name);
  else if (/^text-(display|h[1-3]|body|body-lg|label|caption|meta|code)$/.test(name)) {
    textSize[camel(name.slice(5))] = px(raw, name);
  }
}

// Phone tiers: font size in px, line height in px (rem or unitless × size).
const tiers = {};
for (const tier of ["xs", "sm", "base", "lg", "xl", "read"]) {
  const fsRaw = phone[`fs-${tier}`] ?? light[`fs-${tier}`];
  const lhRaw = phone[`lh-${tier}`] ?? light[`lh-${tier}`];
  if (!fsRaw || !lhRaw) fail(`missing --fs-/--lh-${tier}`);
  const fontSize = rem(fsRaw, `fs-${tier}`);
  const lineHeight = lhRaw.endsWith("rem") ? rem(lhRaw, `lh-${tier}`) : Math.round(fontSize * Number(lhRaw));
  tiers[tier] = { fontSize, lineHeight };
}

const motion = {};
for (const [name, raw] of Object.entries(light)) {
  if (name.startsWith("duration-")) motion[camel(name.slice(9))] = Number(raw.replace("ms", ""));
}

const json = (v) => JSON.stringify(v, null, 2);
const out = `// GENERATED by scripts/gen-tokens.mjs from frontend/app/globals.css — DO NOT EDIT.
// Re-run \`node scripts/gen-tokens.mjs\` after changing the web theme.
${skipped.length ? `// Dark-only tokens skipped (no light value): ${skipped.join(", ")}\n` : ""}
export const lightColours = ${json(lightPal.colours)} as const;

export type ColourName = keyof typeof lightColours;
export type Colours = Record<ColourName, string>;

export const darkColours: Colours = ${json(darkPal.colours)};

export const lightShadows = ${json(lightPal.shadows)} as const;

export type ShadowName = keyof typeof lightShadows;
export type Shadows = Record<ShadowName, string>;

export const darkShadows: Shadows = ${json(darkPal.shadows)};

export const radius = ${json(radius)} as const;

export const space = ${json(space)} as const;

/** Fixed sizes (px) — headings and chrome labels. */
export const textSize = ${json(textSize)} as const;

/** Phone reading tiers (px) — the web's max-width:639px override. */
export const typeTiers = ${json(tiers)} as const;

/** Durations (ms). */
export const motion = ${json(motion)} as const;
`;

writeFileSync(outPath, out.replace(/"([A-Za-z_$][\w$]*)":/g, "$1:"));
console.log(`gen-tokens: wrote ${outPath}`);
