import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import { Shaper, type TextImage } from "@/lib/og/shape-text";

// Dynamic Open Graph card, 1200×630, served at `/og?title=…[&kind=blog]`.
// Used by `openGraph.images` across the site so link unfurls (WhatsApp, X,
// LinkedIn) show a branded Arabic card instead of a bare URL.
//
// DESIGN. This is the marketing card kit's cover (`main`, landscape cut —
// marketing_dashboard/templates/cards/main.html + _card.html) redrawn for
// Satori, so a link preview reads as one of the social cards: a full-bleed
// accent masthead (chevron strip, lockup + «مساعدك القانوني الذكي»), the
// headline on the cream ground under an accent rule and kicker, and the accent
// pill carrying the contact cluster. Tokens, sizes and the headline ladder are
// the kit's. `kind=blog` adds the kicker «من مدوّنة ريحان» and moves a CTA
// into the pill — on a link preview the whole image IS the link, so «اقرأ
// المقال كاملاً» is a true statement there and nowhere else. The kit's muted
// caption under the pill is dropped: at preview size (~500px wide) it would be
// ~8px tall.
//
// TEXT. Every Arabic string is shaped by HarfBuzz in the kit's Noto Naskh
// Arabic and placed as an SVG image — see lib/og/shape-text.ts for why Satori's
// own text cannot do it. Satori still sets the Latin contact cluster, in Cairo.
//
// ASSETS are read with `fs` from `process.cwd()/assets` (NOT `fetch(new
// URL(..., import.meta.url))` — that silently failed to resolve in the
// standalone server). `next.config.mjs`'s `outputFileTracingIncludes` copies
// them, and HarfBuzz's wasm, into the standalone bundle. If loading fails the
// card still renders — masthead and pill, no text — rather than 500.

export const runtime = "nodejs";

// Kit tokens (templates/cards/base.css, light theme) and the kit's default
// accent (catalog.json `mode`: light + aubergine).
const KIT = {
  accent: "#4C4158",
  bg: "#F7F2EC",
  ink: "#18141A",
  cream: "#FCF8F2",
  creamSoft: "rgba(252, 248, 242, 0.8)",
  creamTagline: "rgba(252, 248, 242, 0.72)",
  creamRule: "rgba(252, 248, 242, 0.4)",
  chevron: "rgba(252, 248, 242, 0.34)",
} as const;

const WIDTH = 1200;
const HEIGHT = 630;
const INSET = 72;
const BODY_WIDTH = WIDTH - 2 * INSET;
const LOGO_HEIGHT = 48;
const LOGO_RATIO = 1.386; // both kit cuts share one 444×320 crop box

const DEFAULT_TITLE = "المساعد القانوني الذكي في الأنظمة السعودية";
const TAGLINE = "مساعدك القانوني الذكي";
const BLOG = { kicker: "من مدوّنة ريحان", cta: "اقرأ المقال كاملاً" } as const;

// Simple Icons (CC0), the same marks the kit's contact cluster draws.
const LINKEDIN_PATH =
  "M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z";
const X_PATH =
  "M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20.644h2.039L6.486 3.24H4.298Z";

interface Assets {
  shaper: Shaper;
  cairoRegular: Buffer;
  cairoBold: Buffer;
  logo: string;
}

let assets: Promise<Assets | null> | null = null;

function loadAssets(): Promise<Assets | null> {
  assets ??= (async () => {
    const root = process.cwd();
    const fonts = join(root, "assets", "fonts");
    const [naskh, cairoRegular, cairoBold, logo] = await Promise.all([
      readFile(join(fonts, "NotoNaskhArabic-wght.ttf")),
      readFile(join(fonts, "Cairo-Regular.ttf")),
      readFile(join(fonts, "Cairo-Bold.ttf")),
      readFile(join(root, "assets", "brand", "rayhan-logo-on-accent.png")),
    ]);
    return {
      shaper: new Shaper({
        arabic: new Uint8Array(naskh),
        latinRegular: new Uint8Array(cairoRegular),
        latinBold: new Uint8Array(cairoBold),
      }),
      cairoRegular,
      cairoBold,
      logo: `data:image/png;base64,${logo.toString("base64")}`,
    };
  })().catch((err) => {
    console.error("[og] asset load failed — rendering without text", err);
    assets = null; // retry on the next request
    return null;
  });
  return assets;
}

// The kit's landscape headline ladder (main.html): ≤30 chars → 84, ≤55 → 70,
// ≤78 → 62, ≤90 → 56, else 50.
function headlineSize(length: number): number {
  if (length <= 30) return 84;
  if (length <= 55) return 70;
  if (length <= 78) return 62;
  if (length <= 90) return 56;
  return 50;
}

/** A shaped text block; the negative margins give back the bleed so the
 * layout sees the CSS line boxes. */
function Text({ image }: { image: TextImage }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
    <img
      src={image.src}
      width={image.width}
      height={image.height + 2 * image.bleed}
      style={{ marginTop: -image.bleed, marginBottom: -image.bleed }}
    />
  );
}

function Icon({ path, size }: { path: string; size: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill={KIT.cream}>
      <path d={path} />
    </svg>
  );
}

/** rayhanai.com │ [in] rayhanai-sa  [X] rayhanai_sa — kit `brand.contact(20)`
 * inverted for the accent pill. Laid out LTR, as on the cards. */
function Contact() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, color: KIT.cream }}>
      <div style={{ display: "flex", fontSize: 20, fontWeight: 700 }}>rayhanai.com</div>
      <div style={{ display: "flex", width: 1, height: 20, backgroundColor: KIT.creamRule }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Icon path={LINKEDIN_PATH} size={19} />
        <div style={{ display: "flex", fontSize: 19, color: KIT.creamSoft }}>rayhanai-sa</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Icon path={X_PATH} size={19} />
        <div style={{ display: "flex", fontSize: 19, color: KIT.creamSoft }}>rayhanai_sa</div>
      </div>
    </div>
  );
}

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);

  const rawTitle = searchParams.get("title")?.trim() || DEFAULT_TITLE;
  const title = rawTitle.length > 120 ? `${rawTitle.slice(0, 119).replace(/\s+\S*$/, "")} …` : rawTitle;
  const blog = searchParams.get("kind") === "blog";

  const loaded = await loadAssets();
  const shaper = loaded?.shaper;

  const headline = shaper?.render(title, {
    size: headlineSize(title.length),
    weight: 700,
    color: KIT.ink,
    lineHeight: 1.35,
    maxWidth: BODY_WIDTH,
    balance: true,
  });
  const tagline = shaper?.render(TAGLINE, {
    size: 21,
    weight: 500,
    color: KIT.creamTagline,
    lineHeight: 1.4,
    maxWidth: BODY_WIDTH,
  });
  const kicker = blog
    ? shaper?.render(BLOG.kicker, {
        size: 24,
        weight: 600,
        color: KIT.accent,
        lineHeight: 1.4,
        maxWidth: BODY_WIDTH,
      })
    : undefined;
  const cta = blog
    ? shaper?.render(BLOG.cta, {
        size: 24,
        weight: 700,
        color: KIT.cream,
        lineHeight: 1.4,
        maxWidth: BODY_WIDTH,
      })
    : undefined;

  // Rows are `row-reverse` so JSX order is reading order, right to left.
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          backgroundColor: KIT.bg,
          fontFamily: "Cairo",
        }}
      >
        {/* Masthead — kit card.band(), landscape. */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            padding: "18px 0",
            backgroundColor: KIT.accent,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              height: 20,
              padding: "0 44px",
            }}
          >
            {Array.from({ length: 18 }, (_, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  width: 14,
                  height: 14,
                  borderTop: `3px solid ${KIT.chevron}`,
                  borderRight: `3px solid ${KIT.chevron}`,
                  transform: "rotate(-45deg)",
                }}
              />
            ))}
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "row-reverse",
              alignItems: "center",
              gap: 16,
              padding: `0 ${INSET}px`,
            }}
          >
            {loaded ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={loaded.logo}
                alt="ريحان"
                width={Math.round(LOGO_HEIGHT * LOGO_RATIO)}
                height={LOGO_HEIGHT}
              />
            ) : null}
            {tagline ? <Text image={tagline} /> : null}
          </div>
        </div>

        {/* Body — kicker block and headline, centred in what is left. */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            justifyContent: "center",
            flexGrow: 1,
            gap: 20,
            padding: `18px ${INSET}px 14px`,
          }}
        >
          {kicker ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 14 }}>
              <div
                style={{
                  display: "flex",
                  width: 64,
                  height: 5,
                  borderRadius: 3,
                  backgroundColor: KIT.accent,
                }}
              />
              <Text image={kicker} />
            </div>
          ) : null}
          {headline ? <Text image={headline} /> : null}
        </div>

        {/* Pill — kit card.pill(); the blog CTA takes the reading edge. */}
        <div style={{ display: "flex", padding: `0 ${INSET}px 26px` }}>
          <div
            style={{
              display: "flex",
              flexDirection: "row-reverse",
              flexGrow: 1,
              alignItems: "center",
              justifyContent: cta ? "space-between" : "center",
              padding: cta ? "10px 28px" : "12px 24px",
              borderRadius: 12,
              backgroundColor: KIT.accent,
            }}
          >
            {cta ? (
              <div style={{ display: "flex", flexDirection: "row-reverse", alignItems: "center", gap: 12 }}>
                <Text image={cta} />
                <svg
                  viewBox="0 0 24 24"
                  width={24}
                  height={24}
                  fill="none"
                  stroke={KIT.cream}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M19 12H5M11 6l-6 6 6 6" />
                </svg>
              </div>
            ) : null}
            <Contact />
          </div>
        </div>
      </div>
    ),
    {
      width: WIDTH,
      height: HEIGHT,
      ...(loaded
        ? {
            fonts: [
              { name: "Cairo", data: loaded.cairoRegular, weight: 400, style: "normal" },
              { name: "Cairo", data: loaded.cairoBold, weight: 700, style: "normal" },
            ],
          }
        : {}),
    },
  );
}
