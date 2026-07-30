import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";

// Dynamic Open Graph card generator, 1200×630, served at `/og?title=…`.
// Used by `openGraph.images` on the landing page and blog articles so link
// unfurls (WhatsApp, X, LinkedIn) show a branded Arabic card instead of a bare
// URL.
//
// FONT NOTE: `ImageResponse` (Satori) needs RAW font bytes — CSS / `next/font`
// fonts do NOT apply, and its built-in default font has NO Arabic glyphs (Arabic
// would render as tofu boxes). The repo had no usable .ttf under
// `marketing/brand/` (only variable .woff2 files, which Satori cannot read).
//
// The app's brand face is **Noto Naskh Arabic**, but Satori's font engine
// CRASHES on it at render time — `lookupType: 5 substFormat: 3 is not yet
// supported` → `memory access out of bounds` — because that naskh font uses
// advanced OpenType GSUB contextual-substitution tables Satori can't parse
// (verified empirically 2026-07-22). So the OG card uses **Cairo** instead: a
// modern Arabic Google font that Satori renders cleanly with correct
// letter-joining and RTL shaping. Static Cairo TTFs are vendored into
// `frontend/assets/fonts/`.
//
// The fonts are read with `fs` from `process.cwd()/assets/fonts` (NOT
// `fetch(new URL(..., import.meta.url))` — that pattern silently failed to
// resolve the emitted asset in the standalone server, leaving Satori to
// auto-fetch Noto Naskh from Google and crash). `next.config.mjs`'s
// `outputFileTracingIncludes` copies the TTFs into the standalone bundle so the
// path resolves in production too. If the bytes can't be read we degrade to the
// default font rather than 500 — but every glyph the card renders IS covered by
// Cairo, so the dynamic-fallback crash path is never reached in practice.

export const runtime = "nodejs";

// Brand palette — light-mode tokens from `app/globals.css`.
const BRAND = {
  bg: "#F7F2EC", // warm cream canvas
  primary: "#4A6B5F", // forest-teal
  primaryFg: "#FCF8F2", // cream ink on teal
  ink: "#18141A", // near-black title text
  muted: "#6A6581", // subtitle / secondary
  border: "#E0D6CA",
} as const;

const WIDTH = 1200;
const HEIGHT = 630;
const FONT_FAMILY = "Cairo";

interface LoadedFont {
  name: string;
  data: Buffer;
  weight: 400 | 700;
  style: "normal";
}

async function loadFonts(): Promise<LoadedFont[]> {
  try {
    const dir = join(process.cwd(), "assets", "fonts");
    const [regular, bold] = await Promise.all([
      readFile(join(dir, "Cairo-Regular.ttf")),
      readFile(join(dir, "Cairo-Bold.ttf")),
    ]);
    return [
      { name: FONT_FAMILY, data: regular, weight: 400, style: "normal" },
      { name: FONT_FAMILY, data: bold, weight: 700, style: "normal" },
    ];
  } catch {
    // Font bytes unavailable → fall back to Satori's default font. Arabic will
    // not shape correctly, but the route still returns a valid PNG.
    return [];
  }
}

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);

  const rawTitle =
    searchParams.get("title")?.trim() ||
    "المساعد القانوني الذكي في الأنظمة السعودية";
  const title =
    rawTitle.length > 120 ? `${rawTitle.slice(0, 119)}…` : rawTitle;
  const subtitle = searchParams.get("subtitle")?.trim() || "";

  const fonts = await loadFonts();

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          direction: "rtl",
          backgroundColor: BRAND.bg,
          padding: "72px 80px",
          fontFamily: FONT_FAMILY,
          position: "relative",
        }}
      >
        {/* Accent bar along the RTL start edge (right). */}
        <div
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: 16,
            backgroundColor: BRAND.primary,
            display: "flex",
          }}
        />

        {/* Brand row: teal badge + wordmark. */}
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 84,
              height: 84,
              borderRadius: 22,
              backgroundColor: BRAND.primary,
              color: BRAND.primaryFg,
              fontSize: 34,
              fontWeight: 700,
            }}
          >
            ريحان
          </div>
          <div
            style={{
              display: "flex",
              fontSize: 40,
              fontWeight: 700,
              color: BRAND.ink,
            }}
          >
            ريحان
          </div>
        </div>

        {/* Title block — grows to fill, bottom-aligned title. */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 24,
          }}
        >
          <div
            style={{
              display: "flex",
              fontSize: title.length > 60 ? 60 : 72,
              fontWeight: 700,
              lineHeight: 1.35,
              color: BRAND.ink,
              textAlign: "right",
              maxWidth: 1040,
            }}
          >
            {title}
          </div>
          {subtitle ? (
            <div
              style={{
                display: "flex",
                fontSize: 34,
                lineHeight: 1.5,
                color: BRAND.muted,
                textAlign: "right",
                maxWidth: 1000,
              }}
            >
              {subtitle}
            </div>
          ) : null}
        </div>

        {/* Footer row: tagline + domain. */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderTop: `2px solid ${BRAND.border}`,
            paddingTop: 28,
          }}
        >
          <div style={{ display: "flex", fontSize: 30, color: BRAND.muted }}>
            المساعد القانوني الذكي
          </div>
          <div
            style={{
              display: "flex",
              fontSize: 30,
              fontWeight: 700,
              color: BRAND.primary,
              direction: "ltr",
            }}
          >
            rayhanai.com
          </div>
        </div>
      </div>
    ),
    fonts.length > 0
      ? { width: WIDTH, height: HEIGHT, fonts }
      : { width: WIDTH, height: HEIGHT },
  );
}
