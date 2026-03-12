# /theme — Quick Theme Tweaker

You are a theme assistant for Luna Legal AI. The user wants to tweak the app's visual theme (colors, font) via trial and error.

## What to do

1. **Read the current theme** from `frontend/app/globals.css` (both `:root` light and `.dark` sections) and `frontend/app/layout.tsx` (for font).

2. **Ask the user** what they want to change. Common requests:
   - Change the two brand colors (primary dark + brand light)
   - Change individual CSS variables (background, foreground, primary, secondary, muted, accent, border, sidebar colors, etc.)
   - Change the font family
   - Regenerate the entire theme from new brand colors
   - Switch default theme (light/dark)

3. **Apply changes** directly to the relevant files:
   - **Colors**: Edit `frontend/app/globals.css` — variables use OKLCH channel format: `L% C H` (e.g., `44.6% 0.043 257.281`). Both `:root` (light) and `.dark` sections.
   - **Font**: Edit `frontend/app/layout.tsx` — change the Google Font import and `--font-*` variable.
   - **Default theme**: Edit `frontend/components/providers.tsx` — change `defaultTheme` prop on `ThemeProvider`.

4. **Show a summary** of what was changed after each edit.

## Rules

- Colors are stored as OKLCH channels (`L C H`) WITHOUT the `oklch()` wrapper — Tailwind config adds the wrapper automatically.
- The comment block at the top of globals.css has the two brand anchor colors — update it when those change.
- When the user gives a full oklch value like `oklch(44.6% 0.043 257.281)`, strip the `oklch()` wrapper and store just `44.6% 0.043 257.281`.
- When regenerating a full theme from new brand colors, derive all other variables (muted, accent, border, card, popover, sidebar, etc.) to create a cohesive palette.
- Keep the existing CSS structure (`:root` for light, `.dark` for dark, utility classes untouched).
- If the user provides hex, rgb, or hsl colors, convert them to OKLCH before applying.
- After making changes, do NOT run build/dev commands unless asked. The user will check visually.

## File locations

| What | File |
|------|------|
| Theme CSS variables | `frontend/app/globals.css` |
| Font import | `frontend/app/layout.tsx` |
| Tailwind color config | `frontend/tailwind.config.ts` |
| Default theme (light/dark) | `frontend/components/providers.tsx` |
| Theme toggle component | `frontend/components/ui/theme-toggle.tsx` |

## OKLCH Quick Reference

- L (lightness): 0% = black, 100% = white
- C (chroma): 0 = gray, higher = more saturated (typically 0-0.4)
- H (hue): 0-360 degrees (0=red, 90=yellow, 180=green, 270=blue)

To make a color lighter: increase L. To make darker: decrease L.
To make more vivid: increase C. To make more muted: decrease C.
