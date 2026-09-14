import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import { Shaper, type TextImage } from "@/lib/og/shape-text";

// Dynamic Open Graph card, 1200×630, served at `/og?title=…&v=N[&kind=blog]`.
// Used by `openGraph.images` across the site — always through `lib/seo/og.ts`,
// never hand-built — so link unfurls (WhatsApp, X, LinkedIn) show a branded
// Arabic card instead of a bare URL.
//
// DESIGN. One deep-green field, cream headline. A link preview is ~500px wide
// and lands in a column of white cards; the green field is what separates it
// from them at a glance, and it carries the brand colour without asking the
// reader to resolve a small logo. Reading order is RTL: the lockup takes the
// right of the header and the headline the right of the stage, both against a
// left margin the tagline and the domain occupy. `kind=blog` adds the kicker
// «من مدوّنة ريحان» over a sage rule and the CTA «اقرأ المقال كاملاً» in the
// footer — in a link preview the whole image IS the link, so the CTA is a true
// statement there and nowhere else.
//
// The kit's social handles are deliberately NOT drawn: at preview scale
// «rayhanai_sa» is ~8px tall, which buys nothing and crowds the domain.
//
// TEXT. Every Arabic string is shaped by HarfBuzz in Noto Naskh Arabic and
// placed as an SVG image — see lib/og/shape-text.ts for why Satori's own text
// cannot do it. Satori still sets the Latin domain, in Cairo.
//
// ASSETS are read with `fs` from `process.cwd()/assets` (NOT `fetch(new
// URL(..., import.meta.url))` — that silently failed to resolve in the
// standalone server). `next.config.mjs`'s `outputFileTracingIncludes` copies
// them, and HarfBuzz's wasm, into the standalone bundle. If loading fails the
// card still renders — the green field and its rules, no text — rather than
// 500.

export const runtime = "nodejs";

const KIT = {
  ground: "#22362C", // deep green, one step under --primary-hover
  cream: "#FCF8F2",
  creamSoft: "rgba(252, 248, 242, 0.72)",
  creamRule: "rgba(252, 248, 242, 0.28)",
  sage: "#A8B6B0",
} as const;

const WIDTH = 1200;
const HEIGHT = 630;
const INSET = 76;
const BODY_WIDTH = WIDTH - 2 * INSET;
const LOGO_HEIGHT = 96;
const LOGO_RATIO = 480 / 326; // the cream lockup's own crop box

const DEFAULT_TITLE = "المساعد القانوني الذكي في الأنظمة السعودية";
const TAGLINE = "مساعدك القانوني الذكي";
const BLOG = { kicker: "من مدوّنة ريحان", cta: "اقرأ المقال كاملاً" } as const;

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
      // The cream-ink lockup: `public/brand/lockup-on-light.png` — the
      // OUTLINE-leaf artwork — with every opaque pixel repainted `#FCF8F2`.
      // `lockup-on-dark.png` cannot stand in: its leaf body is charcoal so it
      // can merge into the dark canvas `#1A1917`, and on this green it reads
      // as a dark blob instead of line art.
      readFile(join(root, "assets", "brand", "rayhan-logo-cream-ink.png")),
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

// The marketing kit's landscape headline ladder (main.html): ≤30 chars → 84,
// ≤55 → 70, ≤78 → 62, ≤90 → 56, else 50.
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

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);

  const rawTitle = searchParams.get("title")?.trim() || DEFAULT_TITLE;
  const title = rawTitle.length > 120 ? `${rawTitle.slice(0, 119).replace(/\s+\S*$/, "")} …` : rawTitle;
  const blog = searchParams.get("kind") === "blog";

  const loaded = await loadAssets();
  const shaper = loaded?.shaper;
  const line = (text: string, size: number, weight: 400 | 500 | 600 | 700, color: string) =>
    shaper?.render(text, { size, weight, color, lineHeight: 1.4, maxWidth: BODY_WIDTH });

  const headline = shaper?.render(title, {
    size: headlineSize(title.length),
    weight: 700,
    color: KIT.cream,
    lineHeight: 1.35,
    maxWidth: BODY_WIDTH,
    balance: true,
  });
  const tagline = line(TAGLINE, 24, 500, KIT.creamSoft);
  const kicker = blog ? line(BLOG.kicker, 25, 600, KIT.sage) : undefined;
  const cta = blog ? line(BLOG.cta, 24, 600, KIT.creamSoft) : undefined;

  // Rows are `row-reverse` so JSX order is reading order, right to left.
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          backgroundColor: KIT.ground,
          padding: `54px ${INSET}px 48px`,
          fontFamily: "Cairo",
        }}
      >
        {/* Header — lockup on the reading edge, tagline against the margin. */}
        <div
          style={{
            display: "flex",
            flexDirection: "row-reverse",
            alignItems: "center",
            justifyContent: "space-between",
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

        {/* Stage — kicker block and headline, centred in what is left. */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            justifyContent: "center",
            flexGrow: 1,
            gap: 22,
          }}
        >
          {kicker ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 14 }}>
              <div style={{ display: "flex", width: 72, height: 5, borderRadius: 3, backgroundColor: KIT.sage }} />
              <Text image={kicker} />
            </div>
          ) : null}
          {headline ? <Text image={headline} /> : null}
        </div>

        {/* Footer — hairline, then the CTA on the reading edge and the domain
            against the margin, where a reader looks for an address. */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", height: 1, backgroundColor: KIT.creamRule, marginBottom: 22 }} />
          <div
            style={{
              display: "flex",
              flexDirection: "row-reverse",
              alignItems: "center",
              justifyContent: "space-between",
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
                  stroke={KIT.sage}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M19 12H5M11 6l-6 6 6 6" />
                </svg>
              </div>
            ) : (
              <div style={{ display: "flex" }} />
            )}
            <div style={{ display: "flex", fontSize: 24, fontWeight: 700, color: KIT.cream }}>rayhanai.com</div>
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
