# Manifest screenshots (PWA plan §1B) — NOT YET PRODUCED

Android Chrome shows the rich install sheet (with a screenshot carousel) only
when the manifest lists screenshots. They are deliberately NOT referenced from
`app/manifest.ts` yet: a listed file that 404s makes Chrome drop the whole
array and warn in DevTools → Application → Manifest.

## Required files

| File | Size (px) | `form_factor` | Content |
|---|---|---|---|
| `chat-narrow-1.png` | 1080 × 1920 | `narrow` | Chat screen, light theme, an anonymised demo conversation with a finished answer and its references |
| `chat-narrow-2.png` | 1080 × 1920 | `narrow` | Conversations list (`/chats`) or the workspace panel on mobile, light theme |
| `chat-wide.png` | 1920 × 1080 | `wide` | Desktop chat + workspace two-pane view, light theme |

Rules:
- PNG, exact sizes above (aspect ratio must be identical within a form factor;
  each side between 320 and 3840 px, long side ≤ 2.3 × short side).
- Anonymised demo account only — no real client names, ID numbers or case facts
  (pitch-deck anonymisation rule).
- Latin digits only in anything visible.
- Captured without browser chrome (DevTools device mode or Playwright
  `page.screenshot`).

## Wiring once the files exist

Add to the object returned by `app/manifest.ts`:

```ts
screenshots: [
  { src: "/manifest-screenshots/chat-narrow-1.png", sizes: "1080x1920", type: "image/png", form_factor: "narrow", label: "المحادثة مع ريحان" },
  { src: "/manifest-screenshots/chat-narrow-2.png", sizes: "1080x1920", type: "image/png", form_factor: "narrow", label: "محادثاتي" },
  { src: "/manifest-screenshots/chat-wide.png", sizes: "1920x1080", type: "image/png", form_factor: "wide", label: "ريحان على الحاسب" },
],
```
