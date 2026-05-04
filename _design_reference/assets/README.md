# Luna Assets

- `luna-mark.svg` — square brand mark, primary-filled with `لونا` wordmark. Reconstructed from the sidebar/login styling in `frontend/components/sidebar/SidebarHeader.tsx` and `frontend/app/login/page.tsx` (both render an `h-8/h-16 w-8/w-16 rounded-lg/rounded-2xl bg-primary text-primary-foreground` div with the text `لونا`). No source SVG exists in the codebase.
- `luna-lockup.svg` — horizontal lockup: mark + `لونا القانونية` + tagline. Also reconstructed.

**Flag:** If Luna has official brand files (a vector logo, a brand sheet, custom illustrations), drop them in here and delete these reconstructions.

**Icons:** Luna uses [Lucide](https://lucide.dev) — no static icon files ship. Import `lucide-react` in React, or load `https://unpkg.com/lucide@latest` in HTML prototypes.
