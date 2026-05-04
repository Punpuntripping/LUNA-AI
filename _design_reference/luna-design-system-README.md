# Luna Color System v2 — Committed

Three modes locked in:

- **Light L1** — *Herbarium Paper* (primary light)
- **Dark D1** — *Aubergine + Sage Voice* (primary dark)
- **Dark D4** — *Twilight Garden* (alt dark, botanical)

All built from the six anchor colors:

| # | Name             | Hex       | OKLCH                  | Family               |
|---|------------------|-----------|------------------------|----------------------|
| 1 | Aubergine Black  | `#18141A` | `oklch(15.5% .012 310)`| Neutral · darkest    |
| 2 | Plum Shadow      | `#373541` | `oklch(28%   .018 290)`| Neutral · mid-dark   |
| 3 | Royal Aubergine  | `#4C4158` | `oklch(36%   .035 310)`| Neutral · accent     |
| 4 | Dusty Mauve      | `#6A6581` | `oklch(48%   .04  295)`| Neutral · text-muted |
| 5 | Sage Mist        | `#9BA9A5` | `oklch(70%   .018 165)`| Brand · primary      |
| 6 | Forest Shadow    | `#242E29` | `oklch(22%   .018 165)`| Brand · deep         |

## Files

- `luna-colors.css` — drop into `app/globals.css` (replaces the lavender `:root` + `.dark` blocks)
- `luna-tailwind.config.ts` — paste the `colors` / `borderRadius` / `boxShadow` / `fontSize` blocks into `tailwind.config.ts` under `theme.extend`
- `luna-color-system-final.html` — the full visual spec (preview + token tables + component recipes)

## How to apply

```html
<!-- Default (L1 light) -->
<html lang="ar" dir="rtl">

<!-- D1 dark primary -->
<html lang="ar" dir="rtl" class="dark">

<!-- D4 twilight alt -->
<html lang="ar" dir="rtl" data-theme="dark-twilight">
```

## Quick class examples

```tsx
// Page chrome
<body className="bg-canvas text-text-primary font-sans">

// Card
<div className="bg-surface-1 border border-border rounded-xl p-4 shadow-xs">

// Primary button
<button className="bg-primary text-primary-fg hover:bg-primary-hover h-10 px-[18px] rounded-lg">
  إرسال
</button>

// Citation pill
<span className="bg-accent-soft text-accent rounded-full px-[7px] text-meta font-mono">
  ¶ 1
</span>

// User bubble (aubergine on light, sage on dark)
<div className="bg-bubble-user text-bubble-user-fg rounded-[18px_18px_6px_18px] p-[14px_18px] max-w-[78%]">

// Case-type pill
<span className="bg-case-realestate text-case-realestate-fg rounded-full px-[10px] py-[3px] text-meta">
  عقاري
</span>
```

## Pairing rule

Default pairing is **L1 ↔ D1**. Use **D4** only for marketing surfaces, hero screens, or data-dense dashboards where a botanical voice helps. Both dark themes share the L1 light counterpart.

## Migration notes

1. Replace the lavender block in `frontend/app/globals.css` with `luna-colors.css`.
2. Merge the Tailwind block from `luna-tailwind.config.ts` into `frontend/tailwind.config.ts`.
3. Search-and-replace legacy class names:
   - `bg-background` → `bg-canvas`
   - `text-foreground` → `text-text-primary`
   - `bg-card` → `bg-surface-1`
   - `bg-muted` → `bg-surface-2`
   - `bg-accent` (when meaning hover surface) → `bg-surface-2`
4. Update `assets/luna-mark.svg` so its fill uses the new `--primary` (`#4A6B5F` light / `#9BA9A5` dark).
5. Verify WCAG AA (4.5:1) on 14px body across all three modes.
