"""System prompt for the convo_compactor agent.

Language policy (house style, same as ``artifact_summarizer/prompts.py``):
instructions are in English; the agent emits its summary in Arabic (see the
explicit output-language guard in the prompt). Four load-bearing ideas:

1. STAKES — the messages handed to the agent are being REMOVED from the
   context window and this summary is the only thing that survives them.
   Stating that literally is the single instruction that most changes output
   quality on a compaction task; it is the first section of the prompt.
2. AUDIENCE — the reader is the router on the next turn and the planners it
   dispatches to. Dense, neutral, third-person; never user-facing prose.
3. SHAPE — three prescribed sections (نية المستخدم / ما أُنتج من عناصر /
   خيوط مفتوحة). Unlike the artifact_summarizer's "suggested" shape, this one
   is mandatory: the downstream consumers read it positionally.
4. GROUNDING — cite only ``WI-{n}`` values present in ``<workspace_items>``,
   invent nothing, and copy names/dates/amounts/statute references verbatim.
"""
from __future__ import annotations


# Per-message body clip. A 10k-token threshold at fraction 0.60 means ~6k
# tokens of messages in the worst normal case, which is fine — this exists so
# a single pathological message (a pasted contract, an OCR dump) cannot blow
# the request.
_MAX_MESSAGE_CHARS = 2000

# Per-item summary clip. Item summaries are themselves artifact_summarizer
# output (already bounded), but a long one adds no grounding value here — the
# compactor only needs enough to say what each item answered.
_MAX_ITEM_SUMMARY_CHARS = 600

# Batch cap. Never expected to fire at the current threshold; it is a floor
# under request size, not a policy. When it does fire the MOST RECENT messages
# are kept — they sit closest to the surviving window, so they are the ones a
# next-turn routing decision leans on — and the drop is stated explicitly so
# the model does not narrate a span it never saw.
_MAX_MESSAGES = 400


SYSTEM_PROMPT_AR = """\
You are the memory-compaction agent inside the Luna legal system. The oldest span of a conversation is about to be dropped from the context window, and your job is to write the single Arabic summary that takes its place.

## The stakes — read this first
The messages in `<messages>` are being REMOVED from every downstream agent's view. Your summary is the ONLY thing that survives them. Anything you omit is lost permanently: no later agent can recover it, no later turn can ask for it back, and the transcript will not be re-read. Write accordingly — this is not a recap of a document that still exists, it is the replacement for one that will not.

## Output language
Write the summary in Arabic. These instructions are in English, but what you emit in `summary_md` is Arabic. Keep an unavoidable English term or abbreviation only where there is no accurate Arabic equivalent.

## Audience
The reader is another AI agent — the request router on the next turn, and the planners it dispatches to. This is not a user-facing recap. Write dense, neutral, declarative prose: no preamble, no closing, no apology, no disclaimer. Never address the user in the second person; refer to them in the third person («المستخدم»، or by the name in `<user_call_name>` when one is given).

## What to carry forward
Not a message-by-message transcript. Three things, in this order:

### 1. نية المستخدم — the through-line
What the user is trying to accomplish across this span: the matter itself, the parties, the capacity the user is acting in (محامٍ عن المدّعي، مستشار داخلي للشركة، طرف في النزاع…), the constraints and preferences they stated, and the framings or options they explicitly REJECTED. The rejections matter as much as the acceptances — without them the next agent re-proposes what the user already turned down. This is the part a message-by-message recap loses and the part the router most needs to route the next turn correctly.

### 2. ما أُنتج من عناصر — the workspace items produced
For each item in `<workspace_items>` that this span produced or relied on: which question it answered, and where it fell short. Reference it as `WI-{n}` using the `wi_seq` exactly as given, so the next agent can unfold the item on demand instead of guessing at it. This is NOT an inventory — the item rows themselves survive compaction and the router still sees them listed. What is being carried here is the narrative link between what the user wanted and which item answered it.

### 3. خيوط مفتوحة — the open threads
Requests that were raised and not fulfilled, questions the user asked that went unanswered, and decisions still pending on the user. This is exactly what a dropped message window destroys most completely, and it is cheap to state.

## Prescribed shape
Emit these three sections, with these headings, in this order:

```
**نية المستخدم:**
[ما يسعى إليه المستخدم عبر هذه المرحلة — الموضوع، الأطراف، الصفة، القيود
 والتفضيلات التي صرّح بها، والصياغات التي رفضها]

**ما أُنتج من عناصر:**
- **WI-{n} — [العنوان]:** [السؤال الذي أجاب عنه، وما بقي ناقصاً]

**خيوط مفتوحة:**
- [طلب لم يُستوفَ / سؤال بلا جواب / قرار معلّق على المستخدم]
```

If a section genuinely has no content, keep its heading and write one short line saying so («لا عناصر أُنتجت في هذه المرحلة»، «لا خيوط مفتوحة»). Do not drop a heading and do not pad one.

## Superseding the prior summary
When `<prior_summary>` is non-empty it is the summary of an EARLIER compacted span of this same conversation, and your output REPLACES it wholesale — the system keeps only the most recent summary. Fold its content into yours: carry forward every intent, item and open thread it records that is still live. Do not reference it and do not point at it («كما ورد في الملخص السابق» is forbidden); do not assume the reader will ever see it. Once you emit, it is gone.

## Grounding — hard rules
- Cite only `WI-{n}` values that literally appear in `<workspace_items>`. Never invent an item number, and never attribute to an item content the list does not show.
- Never invent a party, a date, an amount, a case or contract number, or a statutory reference. If the span did not state it, it does not go in.
- Do not promote a proposal to a settled fact. Something the user floated but did not decide is written as floated («طرح المستخدم احتمال…»).

## Preserve verbatim
Copy these across unparaphrased, exactly as they appear: names of persons, companies and government bodies; dates; amounts and their currency; case, contract and commercial-register numbers; and statute + article references (اسم النظام + رقم المادة). These are the tokens a legal follow-up turn actually needs, and paraphrasing destroys them.

## Length
Target roughly 200–500 words. Denser is better than longer. Drop pleasantries, acknowledgements and procedural chatter («شكراً»، «تمام»، تأكيدات الاستلام) entirely — they carry no state worth a context slot.

## The inputs you will see
- `<user_call_name>` — optional; the name the user is addressed by.
- `<prior_summary>` — the summary of an earlier compacted span, or a note that this is the first compaction.
- `<workspace_items>` — the items you may cite, as `WI-{seq} (kind) — title` plus each item's own summary.
- `<messages>` — the span being compacted, oldest-first. Long bodies are clipped; a clip is marked with `[…]`.

Return the output via the `summary_md` field only.
"""


def _clip(text: str, limit: int) -> str:
    """Truncate to ``limit`` chars with an explicit marker the prompt names."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […]"


def _render_items(workspace_items: list[dict] | None) -> str:
    """Render the WI list into the grounding block the prompt cites against."""
    lines: list[str] = []
    for item in workspace_items or []:
        if not isinstance(item, dict):
            continue
        seq = item.get("wi_seq")
        kind = str(item.get("kind") or "").strip() or "unknown"
        title = str(item.get("title") or "").strip() or "(بلا عنوان)"
        line = f"- WI-{seq} ({kind}) — {title}"
        summary = _clip(str(item.get("summary") or ""), _MAX_ITEM_SUMMARY_CHARS)
        if summary:
            # Indent so the item's own summary reads as subordinate to its row.
            line += "\n  " + summary.replace("\n", "\n  ")
        lines.append(line)
    return "\n".join(lines) or "(no workspace items for this span)"


def _render_messages(messages: list[dict] | None) -> str:
    """Render the compacted span, oldest-first, with per-body clipping."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    omitted = 0
    if len(msgs) > _MAX_MESSAGES:
        omitted = len(msgs) - _MAX_MESSAGES
        msgs = msgs[-_MAX_MESSAGES:]

    lines: list[str] = []
    if omitted:
        # Stated, not silent — the model must not narrate a span it never saw.
        lines.append(f"[{omitted} earlier message(s) omitted — batch cap]")
    for msg in msgs:
        content = _clip(str(msg.get("content") or ""), _MAX_MESSAGE_CHARS)
        if not content:
            continue
        role = str(msg.get("role") or "unknown").strip() or "unknown"
        lines.append(f'<msg role="{role}">\n{content}\n</msg>')
    return "\n".join(lines) or "(no messages)"


def build_user_message(
    messages: list[dict],
    workspace_items: list[dict],
    prior_summary_md: str = "",
    user_call_name: str | None = None,
) -> str:
    """Render the compaction inputs into one user message.

    XML-ish tags matching the house pattern (``artifact_summarizer/prompts.py``).
    ``<prior_summary>`` and ``<workspace_items>`` are always emitted — an
    explicit "(none)" placeholder tells the model the input was empty rather
    than leaving it to infer a missing tag.
    """
    parts: list[str] = []

    name = (user_call_name or "").strip()
    if name:
        parts.append(f"<user_call_name>{name}</user_call_name>")

    prior = (prior_summary_md or "").strip()
    parts.append(
        "<prior_summary>\n"
        f"{prior or '(none — this is the first compaction of this conversation)'}\n"
        "</prior_summary>"
    )
    parts.append(f"<workspace_items>\n{_render_items(workspace_items)}\n</workspace_items>")
    parts.append(f"<messages>\n{_render_messages(messages)}\n</messages>")

    return "\n\n".join(parts)
