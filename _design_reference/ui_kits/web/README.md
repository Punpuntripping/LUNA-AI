# Luna Web UI Kit

Pixel-fidelity recreation of the Luna web app (Arabic/RTL legal assistant). Components are cosmetic-only — they look like the real thing, but don't implement backend calls.

## Files

- `index.html` — loads React 18 + Babel + Lucide CDN and boots the interactive prototype
- `Primitives.jsx` — `Button`, `Pill`, `Card`, `Input`, `Icon` (Lucide wrapper), `tokens` object
- `Sidebar.jsx` — collapsible sidebar with Conversations / Cases tabs + footer
- `Chat.jsx` — `AgentSelector`, `ChatInput`, `Citation`, `MessageBubble`, `TemplateCards`
- `App.jsx` — `LoginScreen`, `ArtifactPanel`, `ChatView`, `App` shell

## Prototype flow

1. Lands on **Login** — `laila@firm.sa` / `••••••••` pre-filled, click "تسجيل الدخول"
2. Enters the **chat view** with sidebar + empty state + template cards
3. Clicking a template card, or typing + send, appends a user message and simulates a streamed assistant reply with citation pills
4. Toggle **المخرجات** (top-right) to open the Artifacts panel
5. Toggle sidebar with the panel-collapse button; switch between **المحادثات** and **القضايا** tabs
6. Click the agent pill above the input to cycle agent families

## Faithful to source

- All colors, radii, spacing come from `frontend/app/globals.css` + `frontend/tailwind.config.ts`
- Arabic strings are the exact strings shipped in `frontend/components/**/*.tsx`
- Icons are **Lucide** (same library used by the codebase via `lucide-react`); names match (`send`, `plus`, `briefcase`, etc.)
- Message bubble, sidebar, chat input layouts mirror the component tree in `frontend/components/{chat,sidebar}/`

## What's skipped on purpose

- Real auth, streaming, file upload, markdown rendering
- Settings page, case detail page, memories, at-command palette (present in source, omitted here for kit scope)
- Dark mode toggle (tokens exist in `colors_and_type.css` — add a `.dark` class to test)
