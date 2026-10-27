import type { Config } from "tailwindcss";

/**
 * Luna Color System v2 — Tailwind config
 * Pairs with app/globals.css which defines the CSS variables.
 *
 * Two layers of tokens:
 *   1. New luna tokens (canvas, surface-*, text-*, brand-soft, accent-*, bubble-*, case-*)
 *   2. Legacy shadcn aliases (background, foreground, card, popover, muted, secondary,
 *      destructive, accent, sidebar) mapped to the luna palette so existing
 *      shadcn/ui components keep working without code changes.
 *
 * Every CSS-variable colour goes through `cssVar()` so opacity modifiers work.
 */

/**
 * What Tailwind 3.4 hands a colour FUNCTION (`lib/util/withAlphaVariable.js`):
 *   - unmodified utility (`bg-primary`) → `opacityValue: "var(--tw-bg-opacity, 1)"`
 *   - modifier (`bg-primary/5`, `/[0.15]`) → `opacityValue: "0.05"`, `"0.15"`
 *   - gradient implicit transparent stop → `opacityValue: 0` (a NUMBER)
 *   - `shadow-primary`, `toColorValue()` → `{}` (no opacityValue)
 */
type TailwindColorFn = (opts: {
  opacityValue?: string | number;
  opacityVariable?: string;
}) => string;

/** Tailwind's own "no modifier" alpha: `var(--tw-bg-opacity, 1)` and friends. */
const TW_DEFAULT_ALPHA_RE = /^var\(--tw-[a-z-]*opacity(?:,\s*1)?\)$/;

/** "0.05" → "5%", 0 → "0%", "15%" → "15%", "var(--x)" → "calc(var(--x) * 100%)". */
function alphaPercent(opacityValue: string | number): string {
  if (typeof opacityValue === "string" && opacityValue.trim().endsWith("%")) {
    return opacityValue.trim();
  }
  const n = typeof opacityValue === "number" ? opacityValue : Number(opacityValue);
  if (Number.isFinite(n)) return `${Number((n * 100).toFixed(4))}%`;
  return `calc(${opacityValue} * 100%)`;
}

/**
 * A theme colour backed by a CSS variable that SUPPORTS the `/N` opacity
 * modifier.
 *
 * WHY: the palette lives in `app/globals.css` as plain hex / oklch / rgba values
 * behind `var(--x)`, and Tailwind 3.4 cannot parse a bare `var()` into channels
 * — so with the old `"var(--x)"` strings every `bg-primary/5`,
 * `border-primary/60`, `bg-muted/30`, `ring-primary/40` … silently generated NO
 * CSS at all. A colour FUNCTION lets Tailwind ask for a given alpha, which we
 * answer with `color-mix()` against `transparent` (same pattern globals.css
 * already uses for the focus pulse).
 *
 * ⚠ UNMODIFIED UTILITIES ARE BYTE-IDENTICAL IN COLOUR. With no modifier
 * Tailwind passes its own `var(--tw-*-opacity, 1)`; that (and `undefined`) gets
 * the plain `var(--x)` back, exactly what these utilities emitted before — so
 * `bg-primary`, `text-primary`, `border-border` do not route through
 * `color-mix` and cannot shift by a rounding or gamut step. (The legacy
 * `bg-opacity-*` utilities stay inert for these colours, as they always were.)
 *
 * `color-mix` needs Chrome 111 / Safari 16.2 / Firefox 113. An older browser
 * drops the declaration — the same "no tint" it rendered before this existed.
 *
 * ⚠ The cast: Tailwind's RUNTIME accepts colour functions (see the type above),
 * but its published `ThemeConfig.colors` type only admits strings. The function
 * is never read as a string anywhere — nothing in this repo calls `theme()` or
 * `resolveConfig` on colours.
 */
function cssVar(variable: `--${string}`): string {
  const color: TailwindColorFn = ({ opacityValue }) => {
    if (
      opacityValue === undefined ||
      (typeof opacityValue === "string" && TW_DEFAULT_ALPHA_RE.test(opacityValue))
    ) {
      return `var(${variable})`;
    }
    // The NUMBER 0 comes only from the gradient plugin's implicit transparent
    // stop (`from-card`, `via-background`, …). Answer with exactly what Tailwind
    // emitted for these colours before (its own fallback), so those unmodified
    // gradients stay byte-identical — and never depend on `color-mix`, which on
    // an older browser would invalidate the whole gradient. (A `/0` MODIFIER
    // arrives as the string "0" and takes the color-mix path below.)
    if (opacityValue === 0) return "rgb(255 255 255 / 0)";
    return `color-mix(in srgb, var(${variable}) ${alphaPercent(opacityValue)}, transparent)`;
  };
  return color as unknown as string;
}

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "var(--font-arabic)",
          "Noto Naskh Arabic",
          "Tahoma",
          "Traditional Arabic",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },

      colors: {
        // ---------- Luna v2 tokens ----------
        canvas: cssVar("--canvas"),

        surface: {
          1: cssVar("--surface-1"),
          2: cssVar("--surface-2"),
          3: cssVar("--surface-3"),
          sidebar: cssVar("--surface-sidebar"),
        },

        "text-primary": cssVar("--text-primary"),
        "text-secondary": cssVar("--text-secondary"),
        "text-muted": cssVar("--text-muted"),
        "text-subtle": cssVar("--text-subtle"),

        primary: {
          DEFAULT: cssVar("--primary"),
          fg: cssVar("--primary-fg"),
          foreground: cssVar("--primary-fg"), // legacy shadcn alias
          hover: cssVar("--primary-hover"),
        },

        "brand-soft": {
          DEFAULT: cssVar("--brand-soft-bg"),
          fg: cssVar("--brand-soft-fg"),
        },

        // Direction 01 — pill chrome (sage @ 14% bg, sage-mauve fg)
        pill: {
          DEFAULT: cssVar("--pill-bg"),
          fg: cssVar("--pill-fg"),
        },

        // accent: shadcn semantic = "hover surface" (kept as surface-2),
        // luna semantic = brand sage available via .soft / .line subkeys.
        accent: {
          DEFAULT: cssVar("--surface-2"),
          foreground: cssVar("--text-primary"),
          brand: cssVar("--accent"),
          soft: cssVar("--accent-soft"),
          line: cssVar("--accent-line"),
        },

        bubble: {
          user: cssVar("--bubble-user-bg"),
          "user-fg": cssVar("--bubble-user-fg"),
        },

        success: { DEFAULT: cssVar("--success-bg"), fg: cssVar("--success-fg"), foreground: cssVar("--success-fg") },
        warning: { DEFAULT: cssVar("--warning-bg"), fg: cssVar("--warning-fg"), foreground: cssVar("--warning-fg") },
        error:   { DEFAULT: cssVar("--error-bg"),   fg: cssVar("--error-fg"),   foreground: cssVar("--error-fg")   },
        info:    { DEFAULT: cssVar("--info-bg"),    fg: cssVar("--info-fg"),    foreground: cssVar("--info-fg")    },

        case: {
          realestate: { DEFAULT: cssVar("--case-realestate-bg"), fg: cssVar("--case-realestate-fg") },
          commercial: { DEFAULT: cssVar("--case-commercial-bg"), fg: cssVar("--case-commercial-fg") },
          labor:      { DEFAULT: cssVar("--case-labor-bg"),      fg: cssVar("--case-labor-fg")      },
          criminal:   { DEFAULT: cssVar("--case-criminal-bg"),   fg: cssVar("--case-criminal-fg")   },
          personal:   { DEFAULT: cssVar("--case-personal-bg"),   fg: cssVar("--case-personal-fg")   },
          admin:      { DEFAULT: cssVar("--case-admin-bg"),      fg: cssVar("--case-admin-fg")      },
          execution:  { DEFAULT: cssVar("--case-execution-bg"),  fg: cssVar("--case-execution-fg")  },
        },

        ring: cssVar("--ring"),

        // code block plane (warm dark in both modes)
        code: {
          DEFAULT: cssVar("--code-bg"),
          head: cssVar("--code-head-bg"),
          border: cssVar("--code-border"),
          fg: cssVar("--code-fg"),
          muted: cssVar("--code-muted"),
        },

        // raw anchors (escape hatches)
        anchor: {
          aubergine: "#18141A",
          plum:      "#373541",
          royal:     "#4C4158",
          mauve:     "#6A6581",
          sage:      "#9BA9A5",
          forest:    "#242E29",
        },

        // ---------- Legacy shadcn aliases (don't break existing components) ----------
        background: cssVar("--canvas"),
        foreground: cssVar("--text-primary"),

        border: {
          DEFAULT: cssVar("--border"),
          strong: cssVar("--border-strong"),
        },
        input: cssVar("--border"),

        card: {
          DEFAULT: cssVar("--surface-1"),
          foreground: cssVar("--text-primary"),
        },
        popover: {
          DEFAULT: cssVar("--surface-1"),
          foreground: cssVar("--text-primary"),
        },
        muted: {
          DEFAULT: cssVar("--surface-2"),
          foreground: cssVar("--text-muted"),
        },
        secondary: {
          DEFAULT: cssVar("--surface-2"),
          foreground: cssVar("--text-primary"),
        },
        destructive: {
          DEFAULT: cssVar("--destructive"),
          foreground: cssVar("--destructive-foreground"),
        },
        sidebar: {
          DEFAULT: cssVar("--surface-sidebar"),
          foreground: cssVar("--text-primary"),
          border: cssVar("--border"),
        },
      },

      borderRadius: {
        sm:    "var(--radius-sm)",
        md:    "var(--radius-md)",
        DEFAULT: "var(--radius)",
        lg:    "var(--radius)",     // legacy alias
        xl:    "var(--radius-xl)",
        "2xl": "var(--radius-2xl)",
        bubble: "var(--radius-bubble)",
        "bubble-tail": "var(--radius-bubble-tail)",
        full:  "var(--radius-full)",
      },

      boxShadow: {
        xs: "var(--shadow-xs)",
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        ring: "var(--ring-focus)",
      },

      fontSize: {
        /* Stock Tailwind tiers, re-pointed at CSS variables so the reading
           scale can step up on phones without touching desktop. Desktop
           values are byte-identical to Tailwind's defaults — see the
           "Reading scale" block in globals.css. */
        xs:   ["var(--fs-xs)",   { lineHeight: "var(--lh-xs)"   }],
        sm:   ["var(--fs-sm)",   { lineHeight: "var(--lh-sm)"   }],
        base: ["var(--fs-base)", { lineHeight: "var(--lh-base)" }],
        lg:   ["var(--fs-lg)",   { lineHeight: "var(--lh-lg)"   }],
        xl:   ["var(--fs-xl)",   { lineHeight: "var(--lh-xl)"   }],
        /* Long-form reading tier: library bodies, blog, guides, summaries.
           18px/1.7 desktop, 17px phone — see "Reading scale" in globals.css. */
        read: ["var(--fs-read)", { lineHeight: "var(--lh-read)" }],

        display:   ["56px", { lineHeight: "1.05", letterSpacing: "-0.02em", fontWeight: "700" }],
        h1:        ["32px", { lineHeight: "1.20", letterSpacing: "-0.01em", fontWeight: "600" }],
        h2:        ["22px", { lineHeight: "1.30", fontWeight: "600" }],
        h3:        ["18px", { lineHeight: "1.40", fontWeight: "600" }],
        "body-lg": ["16px", { lineHeight: "1.70" }],
        body:      ["14px", { lineHeight: "1.70" }],
        label:     ["13px", { lineHeight: "1.40", fontWeight: "500" }],
        caption:   ["12px", { lineHeight: "1.50" }],
        meta:      ["11px", { lineHeight: "1.40", fontWeight: "500" }],
      },

      transitionTimingFunction: {
        standard:   "var(--ease-standard)",
        emphasized: "var(--ease-emphasized)",
      },

      transitionDuration: {
        fast: "150ms",
        base: "200ms",
        slow: "300ms",
      },

      keyframes: {
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        "bounce-dot": {
          "0%, 80%, 100%": { transform: "translateY(0)" },
          "40%": { transform: "translateY(-4px)" },
        },
        // Deep-search progress bar: a glossy highlight sweeps across the FILLED
        // portion so the honest n-of-4 fill still shows motion during the long
        // quiet stages. translateX (a physical transform, unaffected by
        // dir="rtl") runs +100% → -100%, i.e. right → left, matching Arabic
        // reading direction. The band is centred inside a full-width element,
        // so it traverses the whole fill exactly once per loop.
        "ds-bar-sweep": {
          "0%": { transform: "translateX(100%)" },
          "100%": { transform: "translateX(-100%)" },
        },
        // Deep-search recent-topics feed: each new sub-query line eases in.
        "ds-fade-in": {
          "0%": { opacity: "0", transform: "translateY(2px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        blink: "blink 1s step-end infinite",
        "bounce-dot": "bounce-dot 1.2s ease-in-out infinite",
        "ds-bar-sweep": "ds-bar-sweep 2s ease-in-out infinite",
        "ds-fade-in": "ds-fade-in 300ms var(--ease-standard) both",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
