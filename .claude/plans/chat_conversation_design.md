# Chat Conversation Design — Message Layout System

Implemented 2026-07-30. Research-backed redesign of the chat thread (MessageBubble, MessageList, ChatInput, FailedResponseBubble). Sources: Setproduct AI-chat anatomy, NN/g explainable-AI citations, rtlstyling.com, UAE Design System typography, shadcn Bubble, aiuxdesign.guide.

## Core principle

**Asymmetry pairs the turn.** The user's question is a compact tinted bubble; the assistant's answer is unframed full-column prose. The shape difference — not headers, avatars, or dividers — is what tells question from answer. Both share one column rail (`max-w-3xl`).

## The rules (do not regress these)

1. **User message** = shrink-wrapped bubble at the **inline-start** (RIGHT in RTL). Owner decision 2026-07-30: the mirrored WhatsApp convention (inline-end/left) was tried first and rejected — the question must start from the right like natural Arabic. Everything in the thread anchors right; differentiation is carried by tint + shrink-wrap, not side.
   - `w-fit max-w-[85%] sm:max-w-[80%] rounded-2xl bg-muted px-4 py-2.5`
   - `dir="auto"` on the bubble (lawyer pasting English clause gets LTR rendering), `text-start` inside.
   - Sender name (owner decision 2026-07-30): small muted caption above the bubble on the same right rail — `user.full_name_ar` from auth store (narrow string selector to keep memo cheap), fallback "أنت". The (جواب) badge shares this row. No avatar, no timestamp up there.
   - Gutter actions sit on the bubble's outer (inline-end/left) side.
2. **Assistant message** = flat prose on the canvas, no card/border/shadow. Visible "ريحان" caption above the answer (owner decision 2026-07-30), identical format to the user caption — `text-[11px] font-medium text-muted-foreground`, same right rail. Only two framed exceptions: agent-question callout (`السؤال`, primary accent, `border-s-4` logical) and failed state (destructive card).
3. **Turn rhythm (3:1)** — question→answer binds tight, answer→next question separates wide:
   - user root `mb-3`; assistant root `mb-10`; agent-question `mb-3` (binds to the جواب reply below).
   - **Nothing may consume vertical space inside the turn gap.** That's why user copy/edit/timestamp actions live in the *gutter beside the bubble* (hover-revealed, vertically centered), not below it.
4. **Metadata is demoted**: no header rows, no floating timestamps. Assistant model + relative time sit at the inline-end of the hover action bar (`ms-auto`, `text-[11px]`, model wrapped `dir="ltr"` + `[unicode-bidi:isolate]`). User timestamp appears in the hover gutter.
5. **Sources are content, not chrome**: المصدر / referenced-card / template chips render in one always-visible flex-wrap row under the answer — never hover-gated.
6. **Hover reveal** uses opacity only (`opacity-0 group-hover/bubble:opacity-100 group-focus-within/bubble:opacity-100 max-sm:opacity-100`); assistant bar has reserved `h-8` so reveal never shifts layout.
7. **Arabic typography**: assistant answers are **justified** (owner decision 2026-07-30 — «مضبوط», flush at both edges like a legal document; overrides the generic "never justify Arabic on the web" guidance). Applied on the assistant content CONTAINER (not MarkdownRenderer — it's shared with blog/legal/forms): `[&_p]:text-justify [&_li]:text-justify [&_p]:leading-[1.85] [&_li]:leading-[1.85] [text-justify:inter-word]`, skipped for the agent-question callout. Container placement means the streaming path is justified too. No `ch` units for Arabic measure.
8. **One column rail everywhere**: thread wrapper AND composer inner wrapper are `mx-auto w-full max-w-3xl`. Thinking/progress rows align `justify-start` (inline-start = right), same edge the answer text starts on.
9. Skeletons mirror the real layout: everything `me-auto` (right-anchored); user rows `w-1/2`, assistant rows `w-5/6`.

## Follow-ups considered, not built
- Numbered `[n]` markers already bidi-isolated via CitationMarker; المراجع footer markers still not clickable (see project_writer_reference_numbering).
