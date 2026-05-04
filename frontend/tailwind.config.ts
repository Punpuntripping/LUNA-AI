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
 */

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
          "var(--font-ibm-plex-arabic)",
          "IBM Plex Sans Arabic",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },

      colors: {
        // ---------- Luna v2 tokens ----------
        canvas: "var(--canvas)",

        surface: {
          1: "var(--surface-1)",
          2: "var(--surface-2)",
          3: "var(--surface-3)",
          sidebar: "var(--surface-sidebar)",
        },

        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-muted": "var(--text-muted)",
        "text-subtle": "var(--text-subtle)",

        primary: {
          DEFAULT: "var(--primary)",
          fg: "var(--primary-fg)",
          foreground: "var(--primary-fg)", // legacy shadcn alias
          hover: "var(--primary-hover)",
        },

        "brand-soft": {
          DEFAULT: "var(--brand-soft-bg)",
          fg: "var(--brand-soft-fg)",
        },

        // accent: shadcn semantic = "hover surface" (kept as surface-2),
        // luna semantic = brand sage available via .soft / .line subkeys.
        accent: {
          DEFAULT: "var(--surface-2)",
          foreground: "var(--text-primary)",
          brand: "var(--accent)",
          soft: "var(--accent-soft)",
          line: "var(--accent-line)",
        },

        bubble: {
          user: "var(--bubble-user-bg)",
          "user-fg": "var(--bubble-user-fg)",
        },

        success: { DEFAULT: "var(--success-bg)", fg: "var(--success-fg)", foreground: "var(--success-fg)" },
        warning: { DEFAULT: "var(--warning-bg)", fg: "var(--warning-fg)", foreground: "var(--warning-fg)" },
        error:   { DEFAULT: "var(--error-bg)",   fg: "var(--error-fg)",   foreground: "var(--error-fg)"   },
        info:    { DEFAULT: "var(--info-bg)",    fg: "var(--info-fg)",    foreground: "var(--info-fg)"    },

        case: {
          realestate: { DEFAULT: "var(--case-realestate-bg)", fg: "var(--case-realestate-fg)" },
          commercial: { DEFAULT: "var(--case-commercial-bg)", fg: "var(--case-commercial-fg)" },
          labor:      { DEFAULT: "var(--case-labor-bg)",      fg: "var(--case-labor-fg)"      },
          criminal:   { DEFAULT: "var(--case-criminal-bg)",   fg: "var(--case-criminal-fg)"   },
          personal:   { DEFAULT: "var(--case-personal-bg)",   fg: "var(--case-personal-fg)"   },
          admin:      { DEFAULT: "var(--case-admin-bg)",      fg: "var(--case-admin-fg)"      },
          execution:  { DEFAULT: "var(--case-execution-bg)",  fg: "var(--case-execution-fg)"  },
        },

        ring: "var(--ring)",

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
        background: "var(--canvas)",
        foreground: "var(--text-primary)",

        border: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
        },
        input: "var(--border)",

        card: {
          DEFAULT: "var(--surface-1)",
          foreground: "var(--text-primary)",
        },
        popover: {
          DEFAULT: "var(--surface-1)",
          foreground: "var(--text-primary)",
        },
        muted: {
          DEFAULT: "var(--surface-2)",
          foreground: "var(--text-muted)",
        },
        secondary: {
          DEFAULT: "var(--surface-2)",
          foreground: "var(--text-primary)",
        },
        destructive: {
          DEFAULT: "var(--error-bg)",
          foreground: "var(--error-fg)",
        },
        sidebar: {
          DEFAULT: "var(--surface-sidebar)",
          foreground: "var(--text-primary)",
          border: "var(--border)",
        },
      },

      borderRadius: {
        sm:    "var(--radius-sm)",
        md:    "var(--radius-md)",
        DEFAULT: "var(--radius)",
        lg:    "var(--radius)",     // legacy alias
        xl:    "var(--radius-xl)",
        "2xl": "var(--radius-2xl)",
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
        display:   ["56px", { lineHeight: "1.05", letterSpacing: "-0.02em", fontWeight: "700" }],
        h1:        ["32px", { lineHeight: "1.20", letterSpacing: "-0.01em", fontWeight: "600" }],
        h2:        ["22px", { lineHeight: "1.30", fontWeight: "600" }],
        h3:        ["18px", { lineHeight: "1.40", fontWeight: "600" }],
        "body-lg": ["16px", { lineHeight: "1.70" }],
        body:      ["14px", { lineHeight: "1.65" }],
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
      },
      animation: {
        blink: "blink 1s step-end infinite",
        "bounce-dot": "bounce-dot 1.2s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
