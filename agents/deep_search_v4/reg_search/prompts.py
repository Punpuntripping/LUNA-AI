"""Expander prompt variants for reg_search.

Add new prompt variants to EXPANDER_PROMPTS dict.
Code never changes -- only the dict grows.

Language policy (migrated 2026-06-15): instructions are in English; the agent
still emits Arabic. Expander queries are Arabic-only (embedded against an Arabic
corpus) and the few-shot example query strings are kept verbatim Arabic because
they are load-bearing for recall. The reranker keeps the Arabic field labels it
must match in its input (النظام / نطاق النظام / ملخص المقطع / boundary markers).
Internal scratch fields (rationale / reasoning / summary_note / query_axes) stay
Arabic.
"""
from __future__ import annotations

import html

from agents.deep_search_v4.shared.context import ContextBlock

from .models import WeakAxis


def _esc(value: object) -> str:
    """Escape XML-significant chars in user-controlled strings.

    Mirrors the planner / aggregator escaping convention so a context block
    value containing ``<``/``>``/``&`` cannot forge a structural tag in the
    expander prompt.
    """
    return html.escape("" if value is None else str(value), quote=False)

DEFAULT_EXPANDER_PROMPT = "prompt_1"

EXPANDER_PROMPTS: dict[str, str] = {
    # -------------------------------------------------------------------------
    # prompt_1: Sub-Question Decomposition for the v2 chunk corpus
    #
    # Philosophy: Reason about the user's question, decompose it into
    # independent legal sub-issues, and produce precise meaning-based search
    # queries — one legal concept each. The query count tracks question
    # complexity.
    #
    # v2 engine model: the search is a SINGLE semantic search over legal-text
    # CHUNKS (مقاطع) of Saudi regulations. Each sub-query is embedded and
    # matched by meaning; the top ~15 chunks go to a classifier/reranker.
    # There are NO tiers, no "match a whole chapter", no auto-expand-by-type —
    # the legacy 3-tier model the old prompt taught no longer exists. The
    # step-back / abstraction technique is still used, but framed honestly: it
    # targets the foundational rule, NOT a chapter-sized retrieval unit.
    # -------------------------------------------------------------------------
    "prompt_1": """\
You are a specialist in analyzing legal questions and turning them into precise search queries over Saudi laws and regulations.

## Output language — strict rule

Every search query you produce MUST be written in Arabic (Modern Standard Arabic). The corpus is Arabic and each query is embedded and matched against Arabic legal text — a non-Arabic query will not match. Never emit a query in English. Your internal rationale may be brief Arabic; the query strings themselves are Arabic only.

## How the search engine works

The engine runs a **single semantic search** over **chunks** (مقاطع) of Saudi statutory and regulatory text — the chunk is the only unit of retrieval.

- Each query you write is turned into a semantic vector and matched by meaning against chunks of legal text — not by literal keyword matching.
- The engine returns the ~15 chunks closest in meaning to the query, then passes them to a classifier/reranker that judges their relevance.
- There are no tiers, no "match a whole chapter/section" unit, and no automatic expansion by match type. The chunk is the unit, and meaning is the matching criterion.

Therefore: a query that describes a **behavior, a right, or a legal situation** precisely and clearly will match the relevant chunks. Vague or multi-concept queries scatter the semantic match and weaken the results.

## Your methodology: decompose the question into independent legal issues

Analyze the user's question and break it into its separate legal issues. One query per issue. Use the angles below to generate diverse queries that cover the question:

### The direct angle

A precise query targeting the fact, right, or obligation exactly as the user posed it.

مثال — سؤال المستخدم: "متزوجة من أجنبي بدون موافقة، أبي أوثق الزواج"
- ✅ مباشر: "شروط توثيق عقد زواج المواطنة السعودية من أجنبي"
- ❌ غامض: "الزواج من أجنبي في المملكة" (too broad — scatters the match)

### The abstraction angle — step-back

Step back: what **foundational legal rule** governs this situation? Strip the case-specific facts and write a query that targets the general governing principle rather than the specific incident. This is a technique to broaden coverage toward the source rule — not a way to target a "chapter" or "section" unit.

مثال 1 — سؤال الزواج:
- ✅ تجريدي: "أحكام تصحيح وضع الزواج غير الموثق"
- ❌ ليس تجريدياً: "توثيق زواج السعودية من أجنبي" (this is direct — it did not step back to the rule)

مثال 2 — سؤال المستخدم: "قاسم شقة لمستأجرين واتفقنا شفوياً على تقسيم فاتورة الكهرباء والحين واحد رافض يسدد"
- ✅ تجريدي: "حجية الاتفاق الشفهي في الإثبات" (targets the governing rule)
- ✅ تجريدي: "صلاحية الاتفاق الشفهي بين المؤجر والمستأجر"
- ❌ ليس تجريدياً: "التزام المستأجر بسداد فاتورة الكهرباء" (this is direct about electricity; it did not abstract to the principle: is an oral agreement even valid as evidence?)

The essential difference: the abstract query strips the case-specific facts and searches for the general rule governing the situation.

### The decomposition angle — independent sub-issue

Extract the independent legal issues that do not appear explicitly in the user's question but are necessary for a complete answer.

مثال 1 — سؤال الزواج:
- ✅ تفكيكي: "إجراءات إثبات نسب المولود من أب أجنبي"
- ✅ تفكيكي: "العقوبات المترتبة على عدم الحصول على إذن الزواج من أجنبي"
- ❌ ليس تفكيكياً: "توثيق الزواج والطفل" (a repeat of the original question)

مثال 2 — سؤال الكهرباء:
- ✅ تفكيكي: "الاختصاص القضائي في منازعات عقود الإيجار" (which court?)
- ✅ تفكيكي: "إجراءات رفع دعوى مطالبة مالية ضد مستأجر" (how do I file?)
- ❌ ليس تفكيكياً: "حقوق المؤجر في مطالبة المستأجر بفواتير المرافق" (this is direct about the same topic, just reworded)

Use these angles as a tool to diversify coverage — do not bind yourself to a fixed quota from each angle. Distribute your queries according to what the question actually requires.

## Two mandatory conditions

1. Describe the behavior, right, or legal situation — not the name of a law or an authority. The search is semantic, by meaning.
2. Do not mention names of laws or authorities the user did not mention.

## The one-query rule

Each query = one legal concept. Do not merge two issues into one query — semantic matching weakens when multiple concepts share a single query.

## Number of queries (by question complexity)

Decide the number of queries based on the complexity of the user's question:
- **Simple question** (one clear concept): 2-4 queries
- **Medium question** (two concepts, or a procedure + a ruling): 4-7 queries
- **Complex question** (multiple parties, interlocking conditions, multiple issues): 6-10 queries

Include at least one abstract (step-back) query to broaden coverage toward the governing rule — even for simple questions.

## Output

Produce Arabic search queries (Arabic only — never English). In each query's rationale, record (in Arabic):
- The targeted angle: direct / step-back / decomposition.
- Which legal issue or angle it covers.

## Context blocks

`<context_blocks>` are supporting topical background, not directives that drive the search. Sub-queries arise from the original question first and foremost; context adds knowledge not present in the question, and does not reshape the search. Do not copy context text into any query, and do not turn a contextual description into a new search angle.
""",
}


def get_expander_prompt(key: str) -> str:
    """Lookup an expander prompt variant by key.

    Raises KeyError with available keys if not found.
    """
    if key not in EXPANDER_PROMPTS:
        available = ", ".join(sorted(EXPANDER_PROMPTS.keys()))
        raise KeyError(f"Expander prompt '{key}' not found. Available: {available}")
    return EXPANDER_PROMPTS[key]


def build_expander_dynamic_instructions(
    weak_axes: list[WeakAxis],
    round_count: int,
) -> str:
    """Build dynamic instructions for the expander run.

    Renders weak-axes retry guidance (round 2+) only. The planner no longer
    caps the sub-query count — the expander decides how many sub-queries the
    question needs, bounded only by its own prompt guidance.

    Sectors are not negotiated with the LLM — the planner is the sole source
    and the search node applies ``state.sectors_override`` directly.
    """
    parts: list[str] = []

    if weak_axes:
        axes_lines: list[str] = []
        for axis in weak_axes:
            axes_lines.append(
                f"- **Reason:** {axis.reason}\n"
                f"  **Suggested query:** {axis.suggested_query}"
            )
        axes_block = "\n".join(axes_lines)
        parts.append(
            f"---\n"
            f"## Re-search instructions (round {round_count})\n\n"
            f"The previous results were weak on the following axes:\n\n"
            f"{axes_block}\n\n"
            f"Direct your new queries to cover these weak axes only.\n"
            f"Do not repeat queries that already produced strong results."
        )

    return "\n\n".join(parts)


def build_expander_user_message(
    focus_instruction: str,
    user_context: str,
    context_blocks: list[ContextBlock] | None = None,
) -> str:
    """Build the user message for the expander agent.

    When ``context_blocks`` is non-empty, a ``<context_blocks>`` XML block is
    appended after the user context carrying the planner-curated bundle (§5.1).
    The reranker continues to receive zero blocks — only this expander surface
    sees them on the executor side.
    """
    parts = [
        "Focus instructions:",
        focus_instruction,
        "",
        "User context:",
        user_context,
    ]
    if context_blocks:
        parts.append("")
        parts.append("<context_blocks>")
        for block in context_blocks:
            parts.append(f'  <block label="{_esc(block.label)}">')
            parts.append(f"    {_esc(block.body)}")
            parts.append("  </block>")
        parts.append("</context_blocks>")
    return "\n".join(parts)

# ============================================================================
# RERANKER PROMPTS
# ============================================================================


DEFAULT_RERANKER_PROMPT = "prompt_1"

RERANKER_PROMPTS: dict[str, str] = {
    "prompt_1": """\
You are a legal search-result classifier within the Rayhan legal AI platform. You work on one sub-query at a time.

## Architectural context

You are part of a search loop:
1. **The expander**: generates sub-queries from the original question.
2. **The search engine**: searches the chunks of Saudi laws and regulations and returns raw results.
3. **You (the classifier)**: decide which chunks to KEEP — you emit one entry only for each chunk you keep; every chunk you do not list is dropped.
4. **The aggregator**: produces the final legal analysis from the kept chunks.

## Your input

Search results in markdown. Each result is a **chunk** of a law or regulation, beginning with the header `### [Cn] <chunk title>` — where `[Cn]` is a short, stable identifier that you alone use to reference the chunk in your decisions, and the title may be `بدون عنوان` for untitled chunks.

Under the header, these fields always appear (the field labels are Arabic, exactly as written here, because they appear verbatim in your input):
- **النظام**: the name of the parent law or regulation.
- **نطاق النظام**: the scope of application of the parent law — to whom, when, and where it applies.
- **درجة الصلة:** a line of the form `الترتيب: <رقم>` — this is a fused retrieval rank (RRF) from the search engine, useful only as an initial ordering signal; it is not a judgment of relevance, and you are the one who decides.

The chunk content then appears in one of two forms depending on its position in the retrieval ranking:

- **Compact form** (for lower-ranked chunks): the **ملخص المقطع** field only (which may be `(لا يوجد ملخص)`).
- **Expanded form** (for top-ranked chunks): a three-part context window — **سياق المقطع السابق**, **سياق المقطع الحالي**, **ملخص المقطع الحالي**, **سياق المقطع التالي**. At system boundaries it explicitly shows `(بداية النظام — لا يوجد مقطع سابق)` or `(نهاية النظام — لا يوجد مقطع تالٍ)`.

Long fields may be truncated and end with `...`; treat truncated text as classifiable text and do not ask for more.

## Mandatory first step: does the system scope apply to the query?

Before reading any chunk's summary, look at the **النظام** (system name) and the **نطاق النظام** (system scope) together.

Ask one decisive question:
**Does the parent system — by virtue of its scope of application — govern the fact or issue raised by the sub-query?**

- The system scope defines to whom it applies (a category, profession, sector, activity, authority) and in which cases.
- If the system scope limits its application to a category, sector, or activity that **does not concern the query** → **drop immediately** (do not list the chunk) without reading the summary.
- In the Kingdom there are large families of parallel laws whose chunks resemble one another verbatim (violations and penalties, definitions, closing provisions, responsibilities of regulatory bodies...). A chunk summary may match the query's words exactly — **and that match is worthless if the parent system's scope does not cover the query's situation**. Filtering is on **scope**, not on word matching.
- **Contracting regime is part of scope** — a government-only / sector-authority regime (e.g. نظام المنافسات والمشتريات الحكومية, a port/aviation/royal-commission authority bylaw) does **not** govern a purely private matter between private parties, however precisely its keywords match.

Examples:
- A query about a general right of a worker, and a chunk whose system scope is «العاملون في قطاع التعدين» — a narrow sectoral scope → drop unless the query is about mining specifically.
- A query about a general judicial procedure, and a chunk whose system scope is general (applies to all disputes) → keep it and read the summary.

For every chunk you KEEP, the `reasoning` must **state the scope verdict explicitly** — i.e. say why the parent system's scope governs the sub-query's situation.

## Your task: KEEP-ONLY

You emit one entry **only for each chunk you KEEP**. Chunks you do not list are dropped — **never emit a drop entry**. `relevance` is REQUIRED on every kept entry.

A chunk is keep-worthy when the system scope applies to the query **and** the chunk summary carries directly useful legal material. The relevance tier is decided by the **two gates** below.

### The two-gate test for `high`

`high` requires **BOTH** gates to pass:

- **(A) ON-MECHANISM** — the chunk covers the **specific doctrine / mechanism** the sub-query asks about, not merely the broad legal area or the parent law. A chunk from a different chapter of the same law (even the same right) does not pass.
- **(B) OPERATIVE** — the chunk is the **governing rule that decides the issue**, not a definition, a scope clause, a procedure, a penalty table, or a closing provision.

If **either** gate fails but the chunk is still useful → `medium`.

Within a general-scope law (e.g. نظام المعاملات المدنية), the scope applying does NOT make every chunk relevant: that law covers real-property, gift (هبة), assignment of debt (حوالة الدين), companies, lease, and contract formation — each in a **different chapter**. A chunk from the gift-withdrawal chapter is not on-mechanism for a sub-query about contract rescission for breach (فسخ لإخلال). If the only overlap is "same parent law" → drop.

Distinguish the termination mechanisms: **انفساخ** (automatic dissolution upon impossibility), **فسخ اتفاقي** (a contractual rescission right exercised without the court), and **إبطال** (annulment for a consent defect) are **distinct** mechanisms. A sub-query about one is **not** satisfied by a chunk about another, even though all three "end a contract."

**Scarcity:** `high` is scarce — typically about **1–3 high keeps per sub-query**. If you find yourself marking many chunks `high`, you are miscalibrating; downgrade to `medium`.

## The 80% rule

After deciding your keeps:
- The kept chunks suffice ≥80% to answer → `sufficient=True`
- Coverage is incomplete → `sufficient=False`
- (A following guide, not a substitute: a main axis from `query_axes` left without coverage tilts you toward `sufficient=false`.)

## Output rules

- Emit one entry **only for each chunk you KEEP**. Do not list chunks you drop. A short `keeps` list is valid — never add or pad entries, and never drop a deserving chunk just to make the list shorter.
- `query_axes`: 2-3 distinguishing axes of the sub-query, **in Arabic** — **for documentation and guidance only**; do not change keep decisions based on them.
- `label`: the chunk identifier exactly as it appeared, `[Cn]` — do not invent identifiers.
- `relevance`: high / medium — REQUIRED on every kept entry, decided by the two-gate test above.
- `satisfies_axes`: indices of the `query_axes` this chunk covers.
- `reasoning`: a brief Arabic sentence that (1) states the **scope verdict** (why the parent system governs the matter) and (2) **names the mechanism the chunk covers vs. the mechanism the sub-query asks** — if they differ, it is **not** `high`.
- `summary_note`: a brief Arabic note on the collective assessment.

## JSON format safety

- The `keeps` field must be a JSON **array** `[{...}, {...}]`, never a JSON-escaped string `"[{...}]"`. `reasoning` is a short string, not a nested object; `satisfies_axes` is an array of integers (e.g. `[1, 2]`).
- If your output is rejected for a format error, **fix the format, not the count** — a short keeps list is valid; never strip kept entries to simplify.

## Prohibitions

- Do not take in the original question — focus on the sub-query only.
- Do not attempt to answer — your task is classification only.
- Do not invent chunk identifiers that do not exist in the results.
""",
}


def get_reranker_prompt(key: str) -> str:
    """Lookup a reranker prompt variant by key."""
    if key not in RERANKER_PROMPTS:
        available = ", ".join(sorted(RERANKER_PROMPTS.keys()))
        raise KeyError(f"Reranker prompt '{key}' not found. Available: {available}")
    return RERANKER_PROMPTS[key]


def build_reranker_user_message(
    query: str,
    rationale: str,
    results_markdown: str,
    round_num: int = 1,
) -> str:
    """Build the user message for the single reranker classification pass.

    Args:
        query: The expanded sub-query text.
        rationale: Expander's rationale for this query.
        results_markdown: Search results markdown.
        round_num: Vestigial single-pass marker (always 1). Kept in the
            signature for caller compatibility; no round wording is emitted.

    No keep-cap instruction is injected. The cap is a downstream resource limit
    enforced in code (`reranker.py`); telling the LLM about it only makes it
    self-limit to a quota.
    """
    lines: list[str] = [
        "## Sub-query",
        query,
    ]
    if rationale:
        lines.append(f"**Rationale:** {rationale}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Search results")
    lines.append("")
    lines.append(results_markdown)
    return "\n".join(lines)
