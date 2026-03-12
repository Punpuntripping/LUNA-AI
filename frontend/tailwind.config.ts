import type { Config } from "tailwindcss";

/**
 * Helper: creates an oklch color value from a CSS variable,
 * with support for Tailwind's opacity modifier syntax (e.g. bg-primary/90).
 *
 * The CSS variable stores raw OKLCH channels: "L C H"
 * This function produces: oklch(L C H / <alpha>)
 */
function oklchVar(variable: string) {
  return `oklch(var(--${variable}) / <alpha-value>)`;
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
        sans: ["var(--font-ibm-plex-arabic)", "system-ui", "sans-serif"],
      },
      colors: {
        border: oklchVar("border"),
        input: oklchVar("input"),
        ring: oklchVar("ring"),
        background: oklchVar("background"),
        foreground: oklchVar("foreground"),
        primary: {
          DEFAULT: oklchVar("primary"),
          foreground: oklchVar("primary-foreground"),
        },
        secondary: {
          DEFAULT: oklchVar("secondary"),
          foreground: oklchVar("secondary-foreground"),
        },
        muted: {
          DEFAULT: oklchVar("muted"),
          foreground: oklchVar("muted-foreground"),
        },
        accent: {
          DEFAULT: oklchVar("accent"),
          foreground: oklchVar("accent-foreground"),
        },
        destructive: {
          DEFAULT: oklchVar("destructive"),
          foreground: oklchVar("destructive-foreground"),
        },
        card: {
          DEFAULT: oklchVar("card"),
          foreground: oklchVar("card-foreground"),
        },
        popover: {
          DEFAULT: oklchVar("popover"),
          foreground: oklchVar("popover-foreground"),
        },
        sidebar: {
          DEFAULT: oklchVar("sidebar-background"),
          foreground: oklchVar("sidebar-foreground"),
          border: oklchVar("sidebar-border"),
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
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
