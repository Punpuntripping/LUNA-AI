# Plan — Writer Planner (pre-writer distillation + plan loop)

> Adds a conversational planner phase **in front of** `writer`. The planner
> examines what the user provided, decides per-batch whether to inline raw
> content or call the shared `item_analyzer` (separate plan), optionally
> searches the system template library, presents a plan to the user, iterates
> on feedback, and only then hands a full package to the writer.
>
> Mirrors the shape of `agents/deep_search_v4/planner/` (decider + dynamic
> instructions + deferred tools + pause/resume). Reuses every learning from
> that pipeline.
>
> **Companion plan**: distillation is owned by the shared item analyzer —
> spec at `.claude/plans/item_analyzer.md`. This plan only describes how the
> writer planner *calls* it.

---

> ## ⚠️ READ FIRST — Item Analyzer redesigned + built (2026-05-25)
>
> The integration section of this plan (`fetch_items`, `AnalyzedItem` with
> `text_md`/`source`, the 1000-word distill threshold, the gating-policy
> tests) was written against the **old item_analyzer plan**, which has been
> **superseded**. The actual analyzer that ships is documented in
> `.claude/plans/item_analyzer_v2.md` and is **built, tested (32/32 green),
> and ready to call**.
>
> The integration spec for the writer-planner has been updated below
> (see **§ Item Analyzer Integration (UPDATED 2026-05-25)** right after
> this callout). When implementing this plan:
>
> 1. Use the new section as the source of truth for the analyzer call.
> 2. Treat any mention of `fetch_items`, `query=None`-passthrough, the
>    1000-word threshold, or `source='raw\|distilled'` in the older sections
>    below as **historical context only** — do not implement them.
> 3. The new `AnalyzedItem` shape, the verdict-walk algorithm, and the new
>    writer-side XML rendering are spelled out in the new section.

---

## WI-{seq} alias protocol (Phase 2 — 2026-05-26)

The writer_planner adheres to the cross-agent alias protocol documented in
`.claude/plans/agent_communication_protocol.md`. **UUIDs never reach the
LLM I/O surface.** Every workspace item reference in the planner's prompt,
its tool calls, and its structured output uses the `WI-{seq}` alias — the
conversation-scoped integer label from `workspace_items.wi_seq`
(migration 052).

### Contract — what the planner LLM sees and emits

| Where | Type the LLM sees | Resolved to (orchestrator side) |
|---|---|---|
| `<attached_items>` / `<prior_artifacts>` lines | `WI-{seq}` aliases as the primary handle | n/a — render-only |
| `analyze_items(query, targeted_wi=[...])` tool | `list[str]` of `WI-{seq}` aliases | Tool resolves to `list[UUID]` before `AnalyzerCall(targeted_wi=...)` |
| `PlannerDecision.selected_wis` | `list[str]` of `WI-{seq}` aliases | Runner resolves to `list[UUID]` for walkers |
| `PlannerDecision.role_assignments` | `dict[str, PlannerRole]` keyed by `WI-{seq}` | Runner re-keys to `dict[UUID, PlannerRole]` |

### Resolution seams (single source per stage)

- **Build**: `build_writer_planner_deps` computes
  `deps.wi_alias_map: dict[int, str]` from `attached_items` +
  `prior_artifacts` after both are hydrated. Items without a `wi_seq`
  (case-only / pre-052) are skipped — they're unreachable from the LLM
  surface anyway. `WriterPlannerDeps.resolve_wi_alias(alias)` returns
  the UUID or `None`, and accepts raw UUIDs verbatim (defense-in-depth).
- **Tool**: `analyze_items` resolves each alias via
  `deps.resolve_wi_alias` BEFORE calling `analyze(AnalyzerCall(...))`.
  An unknown alias raises `ModelRetry("العنصر WI-X غير موجود...")` so
  the LLM sees the error and self-corrects on the next round. The
  analyzer (Layer-4 sibling) continues to receive raw UUIDs — no change
  to that boundary.
- **Runner**: After `agent.run()` returns a `PlannerDecision`, the
  runner calls `_resolve_decision_aliases(decision, deps)` to convert
  `selected_wis` → `list[UUID]` and re-key `role_assignments` →
  `dict[UUID, role]`. Unknown aliases on the final emission are
  **dropped with a warning** (defense in depth — the tool's `ModelRetry`
  is the primary gate during the agent run).
- **Walkers**: `build_analyzed_items_from_verdicts` and
  `build_analyzed_items_direct` stay UUID-based against the DB. They
  receive the resolver's UUID outputs verbatim.

### Where this stops

The protocol affects only the planner-LLM I/O surface. It does NOT:

- Change `workspace_items.kind` enum values (e.g. `'agent_writer'` stays).
- Change the `agent_writer` slot key in `AGENT_MODELS`.
- Change the `caller_id="writer_planner"` literal passed to the analyzer.
- Change the walkers' internal UUID-based Supabase queries.
- Touch `agents/writer/` — the executor side speaks UUIDs end-to-end.

See `.claude/plans/agent_communication_protocol.md` for the full spec
(scope, edge cases, DB columns, what the protocol intentionally does NOT
do).

---

## Full-refs unfolding (Phase 3 — 2026-05-26)

**Change**: when an `AnalyzedItem` arrives at the writer with `need='full'`
AND `kind in {agent_search, agent_writer}` (refs-family), the walker now
unfolds the WI's **used** references and attaches them in `resolved_refs_md`.
The renderer emits `<refs>...</refs>` after `body_md` for these items —
same shape as the partial-refs path.

**Why**: previously a `need='full'` refs-family item arrived with raw
`content_md` containing inline `[3]` `[7]` citation markers but no
resolution of what those `[n]`s mean. The writer could copy the markers
forward in the new draft but couldn't ground them on actual references.
The fix surfaces every used reference (one row per `[n]` the publisher
marked as cited in this WI's body).

**Mechanics**:
- Walker identifies refs-family WIs with `need='full'`. Calls
  `fetch_item_references(supabase, wi_id, used_only=True)` per WI.
- All such fetches in one turn fan out via `asyncio.gather` so total
  latency is one fetch's worth, not N.
- The `used_only=True` filter on `workspace_item_references.used` is the
  canonical "this ref was cited" signal — no regex parsing of `body_md`.
- Renderer (`agents/writer/prompts.py::_render_item_inner`): the `<refs>`
  block now renders whenever a refs-family item has `resolved_refs_md`,
  regardless of `need`. Meta-family items skip it (no references).

**Path mechanics summary** (replaces the old "no refs for full" rule):

| `need` × `kind`                      | `body_md` source       | refs source                                    | Renderer emits         |
|---|---|---|---|
| `full` + refs-family                 | raw `content_md`       | `fetch_item_references(used_only=True)` (all)  | `body_md` + `<refs>`   |
| `full` + meta-family                 | raw `content_md`       | n/a (meta has no refs)                         | `body_md` only         |
| `partial` + refs-family              | analyzer `distilled`   | `fetch_item_references()` filtered by `refs_needed` | `body_md` + `<refs>` |
| `partial` + meta-family              | analyzer `distilled \| ""` | n/a                                        | `<facts>` + `body_md`  |

**Cost**: one DB roundtrip per full refs-family WI per turn. Typical turn
has 1–2 such items; `asyncio.gather` keeps wall-clock latency at one fetch
(~50–100ms). A few hundred extra tokens per item — well within the
executor's tier_1 budget. The publisher's `used` flag is the right filter
(it's set when the aggregator marks a ref cited, see migration 049 +
`preprocess_references`), so we don't fetch dead refs that were attached
to the WI but never made it into the body.

---

## Item Analyzer Integration (UPDATED 2026-05-25)

This is the **authoritative integration spec** for how the writer-planner
calls the item_analyzer. It supersedes the older sections of this plan
that referenced `fetch_items`. Read this end-to-end before touching the
planner's tools, models, runner, or the writer's XML renderer.

### What was built (recap)

- New package `agents/memory/item_analyzer/` is on disk, tested, exported.
- Two top-level packages: `item_analyzer` (analyze, this caller) and
  `item_analyzer_editor` (edit — v2.1 stub, not used by writer-planner).
- Per-caller prompt subdirs. The writer-planner caller's prompts already
  exist at `agents/memory/item_analyzer/writer/prompts/{refs_kinds,meta_kinds}.py`.
- Tier_2 / deepseek-primary, fixed. Output cap = 32k tokens (distilled
  slices can be lengthy).
- Slot registered: `AGENT_MODELS["item_analyzer"] = ModelPolicy("tier_2", primary="deepseek")`.
- Three superseded plans archived:
  `.claude/plans/item_analyzer.archived.md`,
  `.claude/plans/item_analyzer_request_builder.archived.md`,
  `agents/plans/INITIAL_item_analyzer_family.archived.md`,
  `agents/plans/PROTOCOL_item_analyzer_callers.archived.md`.

### The call surface

```python
class AnalyzerCall(BaseModel):
    query: str               # the planner's question — verbatim
    targeted_wi: list[str]   # 1+ item_ids; runner partitions by kind family
```

That's it. No `mode`, no `caller_id`, no `user_id`/`conversation_id` on
the call. `caller_id` lives on `AnalyzerDeps` (the planner's runner
builds deps with `caller_id="writer_planner"` hardcoded).

### The verdict shape (per-WI return)

Each verdict is one of three states discriminated by `need`:

| `need` | Refs family (`agent_search` / `agent_writer`) | Meta family (`attachment` / `notes`) |
|---|---|---|
| `"full"` | `item_id`, `kind`, `rational` only — caller embeds full `content_md` | `item_id`, `kind`, `rational` only — caller embeds full `content_md` |
| `"partial"` | `distilled: str` (Arabic slice, **may be long**) + `refs_needed: list[int]` + `rational` | `distilled: str \| None` + `extracted_metadata: dict[str,str]` (verbatim facts) + `rational` |
| `"none"` | `item_id`, `kind`, `rational` only — caller drops this WI from the package | same |

`AnalyzeOutput.items: list[WIVerdict]` is ordered to match input
`targeted_wi`. `AnalyzeOutput.overall_rational: str \| None` carries a
cross-WI strategic note (planner-facing, for plan_md framing — never sent
to the writer).

### The new `analyze_items` tool — verbatim implementation

This **replaces** the old `fetch_items` tool in
`agents/writer_planner/tools.py`:

```python
from pydantic_ai import RunContext

from agents.memory.item_analyzer import (
    analyze, AnalyzerCall, build_analyzer_deps, AnalyzeOutput,
)

@agent.tool
async def analyze_items(
    ctx: RunContext[WriterPlannerDeps],
    query: str,
    targeted_wi: list[str],   # list of WI-{seq} aliases, e.g. ["WI-1", "WI-3"]
) -> AnalyzeOutput:
    """Ask the item analyzer to verdict each WI against this query.

    `targeted_wi` is a list of WI-{seq} aliases drawn from the labels
    rendered in <attached_items> / <prior_artifacts>. The tool resolves
    each alias against deps.wi_alias_map BEFORE invoking the analyzer
    (which still receives UUIDs — analyzer is a Layer-4 sibling, no
    change to that boundary). Unknown aliases raise ModelRetry with an
    Arabic error so the LLM can self-correct on the next round.

    Returns one verdict per resolvable WI:
      - need='full'      → unfold the entire content_md into the WriterPackage
      - need='partial'   → use the distilled slice (+ resolve refs_needed via
                           references_service for refs-family WIs,
                           or use extracted_metadata for meta-family WIs)
      - need='none'      → drop the WI; do NOT include in the WriterPackage

    rational / overall_rational are planner-facing — they shape plan_md but
    never reach the writer.
    """
    resolved_ids = [ctx.deps.resolve_wi_alias(a) for a in targeted_wi]
    # ...unknown → ModelRetry(...)
    deps = build_analyzer_deps(
        supabase=ctx.deps.supabase,
        http_client=ctx.deps.http_client,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        caller_id="writer_planner",
    )
    return await analyze(AnalyzerCall(query=query, targeted_wi=resolved_ids), deps)
```

If `WriterPlannerDeps` doesn't already carry `http_client`, add it
(needed by `references_service` for ref unfolding too — see below).

### Updated `AnalyzedItem` model (the planner's per-item record)

The planner translates each non-`none` verdict into one of these for the
WriterPackage. **Field renames vs. the old plan**: `text_md` → `body_md`,
`source` (literal) → derived from `need`. Two new optional fields.

```python
class AnalyzedItem(BaseModel):
    """One workspace_item the planner included in the package."""
    item_id: str
    title: str
    kind: str                                  # original workspace_items.kind
    role: Literal["template", "source", "reference", "prior_draft"]
    need: Literal["full", "partial"]           # 'none' items are not built
    body_md: str                               # full content_md (full) or distilled (partial)
    word_count_before: int                     # from workspace_items.word_count
    word_count_after: int                      # body_md word count

    # Refs-family partial only:
    refs_needed: list[int] = []
    resolved_refs_md: str | None = None        # rendered string of resolved refs

    # Meta-family partial only:
    extracted_metadata: dict[str, str] = Field(default_factory=dict)
```

Removed fields from the old shape: `text_md` (renamed), `source`
(redundant — `need=="full"` ⇒ raw, `need=="partial"` ⇒ distilled).

### The verdict-walk algorithm

After `analyze_items` returns, the planner walks `result.items` and
builds the package. This replaces the old "distill vs passthrough"
batch-gating logic — the analyzer's own prompt decides full/partial/none.

```python
async def build_analyzed_items_from_verdicts(
    verdicts: list[WIVerdict],
    wi_index: dict[str, WorkspaceItemRow],   # pre-loaded WIs (item_id → row)
    deps: WriterPlannerDeps,
) -> list[AnalyzedItem]:
    analyzed: list[AnalyzedItem] = []
    for v in verdicts:
        if v.need == "none":
            continue                          # dropped — never reaches the writer
        wi = wi_index[v.item_id]

        if v.need == "full":
            body_md = wi.content_md
            extras = {}                       # nothing extra for full
        else:  # partial
            if v.kind in ("agent_search", "agent_writer"):
                body_md = v.distilled
                resolved = ""
                if v.refs_needed:
                    # See "references_service quirk" below — we filter post-fetch.
                    all_refs = await fetch_item_references(deps.supabase, v.item_id)
                    keep = {n: r for n, r in all_refs.items() if n in v.refs_needed}
                    resolved = _render_resolved_refs(keep)
                extras = {"refs_needed": v.refs_needed,
                          "resolved_refs_md": resolved or None}
            else:  # attachment / notes
                body_md = v.distilled or ""
                extras = {"extracted_metadata": v.extracted_metadata}

        analyzed.append(AnalyzedItem(
            item_id=v.item_id,
            title=wi.title or "",
            kind=wi.kind,
            role=planner_role_for(v.item_id),  # planner labels this earlier (template/source/...)
            need=v.need,
            body_md=body_md,
            word_count_before=wi.word_count,
            word_count_after=_word_count(body_md),
            **extras,
        ))
    return analyzed
```

`overall_rational` and per-verdict `rational` are NOT copied into
`AnalyzedItem` — they feed `plan_md` for `present_plan_for_approval`,
and only that.

### Updated writer-side XML rendering

In `agents/writer/prompts.py::build_writer_user_message_from_package`
the inner `<source>` / `<template>` / `<reference>` / `<prior_draft>`
blocks change shape. Old format had `source="raw|distilled"`. New format
uses `need` for parity with the planner's record and adds family-specific
sub-blocks for partial verdicts.

```xml
<!-- need=full (refs or meta) -->
<source kind="{kind}" item_id="{item_id}" need="full">
{body_md}
</source>

<!-- need=partial, refs family -->
<source kind="{kind}" item_id="{item_id}" need="partial">
{body_md}
<refs>
{resolved_refs_md}
</refs>
</source>

<!-- need=partial, meta family -->
<source kind="{kind}" item_id="{item_id}" need="partial">
<facts>
{key}: {value}
{key2}: {value2}
</facts>
{body_md if body_md else ""}
</source>
```

The outer structural blocks (`<templates>`, `<sources>`, `<references>`,
`<prior_draft>`, `<user_request>`, `<preferences>`) stay the same — only
the per-item rendering changes.

### `references_service` quirk

`backend/app/services/references_service.py::fetch_item_references(
supabase, wi_id, *, used_only=False) -> list[Reference]` returns all
references for a WI — **it does NOT accept an `n` filter**. The planner
must fetch all refs and filter in Python:

```python
all_refs = await fetch_item_references(supabase, wi_id)
wanted = {r for r in all_refs if r.n in verdict.refs_needed}
```

If `refs_needed` is empty, **skip the call entirely** — `distilled`
alone is the body. Most partials will have small `refs_needed` lists;
this is a cheap post-fetch filter, not worth adding an `n` param to the
service.

### Migration checklist (use this when implementing)

Use this checklist to translate the rest of this plan into actual code,
ignoring the obsolete `fetch_items` references in the older sections.

- [ ] Drop the `fetch_items` tool from `tools.py`. Add `analyze_items`
      verbatim from above.
- [ ] Update `WriterPlannerDeps` to include `http_client` if missing
      (needed downstream).
- [ ] Replace `AnalyzedItem` with the new shape (above). Drop `text_md`,
      `source`. Add `need`, `body_md`, `refs_needed`,
      `resolved_refs_md`, `extracted_metadata`.
- [ ] Add the verdict-walk helper (above) — call it from the planner's
      runner after `analyze_items` returns, before assembling
      `WriterPackage`.
- [ ] Update `build_writer_user_message_from_package` in
      `agents/writer/prompts.py` to render the new `need`-tagged
      `<source>` blocks (above).
- [ ] **Drop the 1000-word distill threshold logic** from the planner's
      decider system prompt and dynamic instructions. The analyzer's
      own prompt handles full-vs-partial decisions — the planner does
      not gate.
- [ ] **Drop the `WRITER_PLANNER_DISTILL_THRESHOLD` env var.** No replacement.
- [ ] Update the planner's system prompt to reference `analyze_items`
      (not `fetch_items`). The mental model is now: "ask the analyzer
      to verdict these WIs against this query" — not "fetch raw vs
      distill batch".
- [ ] **Drop these tests** (obsolete with the new design):
      - `test_gating_policy.py::test_small_batch_passthrough`
      - `test_gating_policy.py::test_large_batch_distilled`
      - `test_gating_policy.py::test_mixed_batch_in_one_call`
      - `test_gating_policy.py::test_threshold_env_override`
- [ ] **Add these tests**:
      - `test_verdict_walk.py::test_full_verdict_embeds_raw_content`
      - `test_verdict_walk.py::test_partial_refs_fetches_and_filters_refs`
      - `test_verdict_walk.py::test_partial_meta_keeps_extracted_metadata`
      - `test_verdict_walk.py::test_none_verdict_drops_item`
      - `test_runner.py::test_overall_rational_feeds_plan_md`
- [ ] Keep these tests as-is (still valid with new design):
      - `test_decider.py::test_triages_out_irrelevant_artifact` — summary triage
        still happens upstream of `analyze_items`; verdict `none` is a second
        signal of irrelevance
      - `test_examine_before_asking.py::test_contract_example_no_clarify` —
        the protocol is unaffected by the analyzer redesign
      - `test_template_search.py::*` — pgvector path unchanged
- [ ] Verify: the planner makes ONE `analyze_items` call covering all
      WIs it wants to consider (refs + meta mixed). The runner internally
      does 1 or 2 LLM calls (one per family present). If a future telemetry
      shows the planner is fan-ing `analyze_items` calls per kind, that's a
      regression in the planner's prompt — fix the prompt, not the analyzer.

### What carries over unchanged from the older sections

These parts of the plan remain valid and don't need rework:

- The **Goal / triage-via-summary** philosophy — planner still triages
  using `summary` + `word_count` before calling the analyzer, to keep
  the analyzer's input set tight.
- The **examine-before-asking protocol** (§ Stage 1) — fully intact.
  Planner inspects message, attachments, summaries before any `ask_user`.
- **Layer placement** — Layer-2 Major for the planner, Layer-3 Task for
  the demoted writer, Layer-4 Memory for the analyzer. Unchanged.
- **Pause/resume** with `ask_user` + `present_plan_for_approval`
  (deferred tools, two pause reasons, single agent_runs row with status='awaiting_user').
  Unchanged.
- **Hard cap at 3 present cycles**. Unchanged.
- **Template search** via pgvector against `system_templates.summary_embedding`.
  Unchanged — separate code path, unaffected by the analyzer redesign.
- **Role assignment** by the planner (template / source / reference /
  prior_draft). The analyzer doesn't return a role — the planner assigns
  one based on user wording + item title + item summary, just as before.
- **Edit routing** logic (fresh / revise / instruct) — the *decisions*
  stay; the *threshold-gating mechanics* (batch ≤ 1000 → raw, otherwise
  distill) go away. The decider simply triages → analyzes → walks
  verdicts → assembles package.
- **Publisher relocation** — writer is demoted to Layer 3, planner's
  runner calls the existing publisher. Unchanged.
- **Migration 053** (`agent_runs_pause_reason` — adds `pause_reason` TEXT column
  to `agent_runs`, default `'clarify'`). Pause state lives on `agent_runs`;
  `awaiting_user` is an enum value of `agent_run_status`, not a separate table.

### Anti-patterns to avoid

- **Don't call `analyze_items` once per kind**. One call, one mixed
  `targeted_wi` list — the analyzer's runner partitions internally.
- **Don't pass `caller_id` as a tool argument**. It belongs in deps; the
  LLM never sees it. The factory routes prompts based on
  `deps.caller_id`, set by the tool wrapper above.
- **Don't add LLM-side decisions about word_count / thresholds in the
  planner prompt**. The analyzer is now the authority on full vs partial.
  The planner only triages out clearly-irrelevant WIs via summary before
  calling the analyzer.
- **Don't try to set `tier_override`**. v2 fixed at tier_2; no override
  parameter exists on `AnalyzerCall` or `AnalyzerDeps`. If verdict
  quality plateaus, that's a v2.1 decision.
- **Don't import the verdict types in the writer-package XML renderer**.
  The renderer only sees `AnalyzedItem` (the post-walk record). Verdict
  types are an analyzer ↔ planner boundary type.
- **Don't pipe `rational` or `overall_rational` into the WriterPackage**.
  They're planner-only. The writer drafts from `body_md` / `resolved_refs_md` /
  `extracted_metadata` — never from the analyzer's reasoning narrative.
- **Don't add a `source='raw|distilled'` attribute back into the writer
  XML.** `need` carries the same information. Some old sections of this
  plan show the old shape — ignore.
- **Don't load raw `content_md` into the planner LLM's context** (e.g. via a
  hypothetical `read_workspace_item` tool). The planner is summary-only by
  design — see § Core invariant. If a summary is too thin to triage, route
  through `analyze_items` and read the verdict's `rational`; the analyzer
  reads on the planner's behalf.
- **Don't call `analyze_items` when the relevant items are already
  unambiguous** (user attached them this turn, or named them explicitly).
  Bypass the analyzer: the runner constructs `AnalyzedItem` records directly
  from triaged WIs with `need="full"` and `body_md = wi.content_md`. See
  § Two skippable phases — the planner's gating policy.

### Build-order delta (for this plan's overall sequence)

The original Build order in this plan starts with "Land the item_analyzer
plan first" — that's **done**. Adjust the remaining steps:

1. ~~Land the item_analyzer plan first.~~ ✅ DONE (`item_analyzer_v2.md` shipped + 32/32 tests).
2. Migration 047 (`pause_reason` column).
3. `WriterPackage` + `AnalyzedItem` (new shape from § above) + writer's
   `from_package` constructor + new XML renderer in
   `writer/prompts.py`. Tests for the new path.
4. `template_search.py` — unchanged.
5. `writer_planner_context.py` — unchanged.
6. `WriterPlannerDeps` (incl. `http_client`) +
   `build_writer_planner_instructions` + the static system prompt
   (examine-before-asking; **NO threshold gating language**) + the 4
   tools (`analyze_items`, `search_templates`, `ask_user`,
   `present_plan_for_approval`).
7. `create_writer_planner_decider()` + `handle_writer_planner_turn()`.
8. Verdict-walk helper (§ above) — feeds the package assembler.
9. Orchestrator wiring + pause-row extension + SSE event.
10. End-to-end tests including the new `test_verdict_walk.py` suite.

---

## Goal

**The planner's one job: give the writer the most useful context
for the task, talking to the user only when something is genuinely missing.**

It does that with three signals:

1. **Summary triage** — every workspace_item carries a persisted `summary`
   (migration 037) and a `word_count` (migration 048). The planner reads
   those — already loaded into its dynamic instructions — and decides per
   item: *is this even relevant to what the writer needs?* Irrelevant items
   are skipped (they never enter the WriterPackage at all).
2. **Item analyzer fetch** — for items that pass triage, the planner calls
   `fetch_items` against the shared `agents/memory/item_analyzer` service.
   Each target carries `query=None` (raw passthrough, for items under the
   word-count threshold) or `query="..."` (distill the item against this
   angle, for items over the threshold). One call, mixed batch.
3. **Router-handed ids** — the router already pre-selects relevant
   `attached_items` for the turn and passes them in `MajorAgentInput`. The
   planner doesn't rediscover those; it accepts the router's pre-selection
   as the candidate set, then triages + fetches inside it.

`writer` today is a one-shot LLM call that receives a free-form
`user_request` plus router-selected `attached_items`. It works, but it has
no triage, no template scaffolding, no per-item distillation, and no chance
for the user to shape strategy before tokens are spent. The planner fixes
all four by inserting a *director* phase: triage → fetch → assemble plan →
present → iterate → hand off WriterPackage to the writer.

## Two skippable phases — the planner's gating policy

### Core invariant — the planner LLM never reads raw `content_md`

The planner's LLM context is **summary-only** for prior WIs. It works from:

- `summary` (workspace_items.summary, migration 037)
- `title`, `kind`, `word_count`

It never sees `content_md`. Its job is **validation, not unfolding**: decide
relevance + role from summary, then either trust the WI (raw bypass) or
delegate content reading to `item_analyzer` (verdict path). Either way,
unfolding happens elsewhere:

- **Bypass path** — the planner's *runner* (Python code, post-LLM) loads
  `content_md` from `workspace_items` and glues it into
  `WriterPackage.analyzed_items[*].body_md`. The LLM emits item_ids + roles
  only; it never sees the bytes.
- **Analyzer path** — `item_analyzer` reads `content_md` on the planner's
  behalf and returns verdicts. The planner reads the verdict's `rational`,
  not the raw content.

This invariant is what keeps the planner's tier_1 context tight even when
conversation scope has 20+ prior WIs. Violating it (e.g. a tool that loads
raw `content_md` into the planner's prompt) defeats the whole purpose of
having both summaries and the analyzer.

### Two jobs, each independently skippable

The planner has **two distinct jobs**, each independently skippable when the
inputs already make it unnecessary. The whole point of the planner is to
*avoid* exhausting itself on prior-WI context and to *avoid* burning a user
round-trip when nothing needs clarifying — so the default posture is **skip
both**, and a phase only fires when summary-triage finds a real need.

| Phase | Tool | Skip when |
|---|---|---|
| **1. Strategy alignment** | `ask_user` / `present_plan_for_approval` | Subtype stated/implied + template supplied or system-findable + drafting parameters present in the turn. |
| **2. Context distillation** | `analyze_items` | The relevant items are unambiguous: user attached them this turn (router-handed `attached_items`) or named specific artifacts in the message and the planner can resolve them from title/summary. |

### When to SKIP `analyze_items` (preferred path)

The analyzer exists to keep the planner from drowning in raw prior-WI content.
If the planner already knows which items matter, the analyzer is dead weight —
the planner just labels roles and hands raw `content_md` to the writer.

Skip when any of these hold:

- **Turn-attached items.** The user uploaded files this turn, or the router
  handed a small `attached_items` set that's all obviously on-topic.
- **Named items.** The user referenced specific artifacts («استخدم نموذج
  العقد», «من البحث السابق», «المسودة السابقة») and the planner can resolve
  each reference from title + summary.
- **Few prior WIs.** Conversation has a handful of prior artifacts and
  summary-triage alone is enough to decide include/skip.

In all these cases the planner constructs `AnalyzedItem` records **directly**
from the triaged WIs:

```python
analyzed.append(AnalyzedItem(
    item_id=wi.item_id,
    title=wi.title or "",
    kind=wi.kind,
    role=planner_role_for(wi.item_id),
    need="full",                      # always 'full' when bypassing analyzer
    body_md=wi.content_md,            # raw passthrough
    word_count_before=wi.word_count,
    word_count_after=wi.word_count,
))
```

No analyzer call. No verdict walk. The writer gets raw items, role-labeled.

### When to INVOKE `analyze_items`

- **Many prior WIs** and only some are relevant — the analyzer's `none`
  verdict is the cheapest way to drop noise without polluting the planner's
  own context window.
- **Large items** where only slices matter — `partial` distill keeps the
  writer's tier_1 context budget for drafting, not for reading filler.
- **Ambiguous reference** where it's unclear which prior artifact the user
  means — analyzer's per-WI `rational` helps the planner reason about what
  to keep.

When invoked, follow the verdict-walk algorithm in § Item Analyzer
Integration above.

### When to SKIP `present_plan_for_approval`

- **Clean turn** (the contract example in this plan): subtype clear, template
  supplied, parameters explicit → emit final `PlannerDecision` directly,
  writer fires.
- **Tone tweak / micro-revision**: instruction is self-evident on an existing
  draft → no plan needed.

### When to INVOKE `present_plan_for_approval`

- Subtype unclear or ambiguous between two valid types.
- Multiple valid strategies (summary vs full draft vs revision-in-place).
- Missing drafting parameters that can't be inferred from attachments or
  conversation history.

### The four shapes of a planner turn

| Strategy clear? | Items clear? | Planner flow |
|---|---|---|
| ✓ | ✓ | Direct → writer. No analyzer, no present, no ask. **Most common path** for attached-this-turn cases. |
| ✓ | ✗ | `analyze_items` only → assemble package → writer. |
| ✗ | ✓ | `present_plan_for_approval` → approved → writer (raw items, no analyzer). |
| ✗ | ✗ | `analyze_items` → `present_plan_for_approval` → approved → writer. |

This gating sits **above** the verdict-walk algorithm in § Item Analyzer
Integration: only enter the verdict walk when `analyze_items` was actually
invoked. When skipped, build `AnalyzedItem`s directly from triaged WIs as
shown above.

### Prompt anchoring

The decider's system prompt encodes this as the explicit decision the planner
must make first, **before** any tool call:

> «أولاً، حدد هل تحتاج إلى تحليل عناصر ورشة العمل السابقة عبر `analyze_items`؟
> إن كانت العناصر التي يحتاجها الكاتب واضحة — مرفقات هذا الدور، أو عناصر سماها
> المستخدم بوضوح — فمرّرها كما هي بدون تحليل. استخدم `analyze_items` فقط
> حين يكون لديك عناصر سابقة كثيرة لا تعرف أيها يهم.»
>
> «ثانياً، هل الخطة واضحة؟ إن كان النوع محدداً والقالب موجوداً والمعطيات
> كاملة، انتقل مباشرة إلى `PlannerDecision` النهائي بدون
> `present_plan_for_approval`.»

---

## Concurrency — parallel tool emission

When both phases fire (the bottom row of the four-shapes matrix), the planner
needs `search_templates` AND `analyze_items` results before it can assemble
the plan. These are independent — `search_templates` is a pgvector DB read,
`analyze_items` is a tier_2 LLM round-trip — so they should fan out in
parallel.

**How it works**: Pydantic AI dispatches tool calls emitted in the same
LLM response concurrently via `asyncio.create_task`. Same-turn emission =
parallel; cross-turn emission = serial (the second call is conditioned on
the first result). Both writer_planner tools are async I/O-bound, so the
overhead is negligible.

**The model must be told to emit them together.** Tier_1 models default to
one-at-a-time tool selection unless the system prompt nudges otherwise. The
planner's static prompt includes:

> «إن احتجت إلى استدعاء `search_templates` و `analyze_items` كليهما في هذا
> الدور، أصدر الاستدعاءَين في نفس الرد لتنفيذهما بالتوازي. لا تنتظر نتيجة
> أحدهما قبل إصدار الآخر — فهما مستقلان.»

**Precedent**: `agents/deep_search_v4/planner/` already emits
`dispatch_case_search` / `dispatch_reg_search` / `dispatch_compliance_search`
in one response and fans out concurrently. Same mechanism, same prompt
pattern.

**Safety**: parallel tool calls share `ctx.deps`. Neither tool mutates
deps — `search_templates` only reads supabase, `analyze_items` only reads
supabase + http_client and builds a fresh `AnalyzerDeps` per call. No locks
needed.

**Telemetry**: `agent.run_stream_events()` emits one `FunctionToolCallEvent`
per tool invocation; Logfire traces show overlapping spans when parallel
emission worked. If we see strictly serial spans on the bottom row, the
prompt nudge needs strengthening — fix the prompt, not the runner.

---

## Stage 1 protocol — examine before asking

A real Saudi-lawyer turn often arrives like this (real example):

> 3 attachments — 2 images (offer + commercial registry) + 1 PDF labeled
> «عقد اتعاب سنوي مسودة» — plus text:
> «سويلي بناء على العرض والطلبات والكرف الثاني منابر وبناء على نموذج العقد
> والمبلغ ٤٠٠٠٠الف، ٢٠ الف الان و٢٠الف بعد ٦ شهور، وتاريخ اليوم ١٤٤٧/١/١٨»

Everything the planner needs is already in front of it:

- **subtype**: عقد (contract) — both stated and implied by «نموذج العقد»
- **template**: the PDF labeled «مسودة» — used as scaffold
- **source data**: the two images — offer + registry → parties, scope
- **parameters**: 40K split 20+20 over 6 months, date 1447/1/18

A planner that opens this turn with «ما نوع الوثيقة؟» is broken. The decider's
first action MUST be **inspection**:

1. Parse the user message for stated subtype, parameters, references to
   specific attachments («العرض», «الكرف الثاني», «نموذج العقد»).
2. Read each attached_item's `summary` (and `title`, `word_count`) to triage
   relevance and infer role (template vs source vs reference). If a summary
   is missing or thin, include that WI in an `analyze_items` call and read
   the verdict's `rational` to learn what's in it — **the planner LLM does
   NOT load raw `content_md` itself; the analyzer reads on its behalf** (see
   § Core invariant above).
3. Identify what's missing **after** the inspection.

**Hard rule — no ask_user when the user has been clear.** If subtype is
stated or unambiguously implied, a template is supplied (either user-attached
or stated by reference), and the parameters needed to draft are in the
message, the planner does NOT call `ask_user`. It goes straight to role
assignment → triage → fetch → present plan. The contract example above is
a clean turn — zero clarification questions, one plan presentation, one
«موافق», then writer fires.

`ask_user` is reserved for cases where, even after inspecting summaries and
optionally fetching raw items, a critical fact for drafting is missing
(e.g. the user said «اكتب العقد» but no party names are anywhere in the
attachments or the conversation).

## Layer placement (Wave 9 hierarchy)

Two orthogonal axes — keep them straight (see `CLAUDE.md` § "Vocabulary"):

- **Layer** — the agent's architectural position in the call graph and its
  privilege rules. Defined in `.claude/plans/wave_9_agent_runs.md` ("Agent
  Hierarchy"). Values: Layer 1 Conductor / Layer 2 Major / Layer 3 Task /
  Layer 4 Memory. Determines who can talk to the user, who can write
  `workspace_items`, what context surface each agent gets.
- **Tier (model class)** — the cost/capability bucket. Defined in
  `agents/utils/agent_models.py:32-45` as `Tier = Literal["tier_1", "tier_2"]`.
  Drives which family/provider chain `get_agent_model` returns.

| Agent | Layer | Model tier | Notes |
|---|---|---|---|
| `writer_planner_decider` (new) | **Layer 2 — Major** | `tier_1` | Takes the Major role for the writing family. Talks to user (`ask_user`, `present_plan_for_approval`). Publishes the final `kind='agent_writing'` workspace_item via the writer's existing publisher. Symmetric to `deep_search_v4 planner_decider`. |
| `writer` | **Layer 3 — Task** (demoted) | `tier_1` (unchanged) | Was Layer 2 in Wave 9 when invoked directly by the router. Now invoked by writer_planner only — becomes a pure transformer: WriterPackage → WriterLLMOutput. No user talk. The runner stops calling its own publisher; the writer_planner runner calls it instead. Symmetric to `aggregator` in deep_search (Layer 3 transformer that emits the artifact body; planner persists). |
| `item_analyzer` (new, separate plan) | **Layer 4 — Memory** | `tier_2` | Shared memory agent — see `.claude/plans/item_analyzer.md`. Called by this planner via a wrapped Pydantic AI tool. No user talk, no workspace_items writes — purely reads content_md and returns it raw or distilled. |
| `search_templates` | n/a (no LLM) | n/a | pgvector cosine over `system_templates.summary_embedding`. Not an agent. |

### Wave 9 hard rules this design satisfies

- **«Only Layer 1 + Layer 2 may communicate with the user.»** ✓
  writer_planner (Layer 2) is the only new user-facing agent; item_analyzer
  (Layer 4) and writer (Layer 3) are both silent.
- **«Only Layer 2 + Layer 4 write to `workspace_items`.»** ✓
  writer_planner (Layer 2) publishes the agent_writing row. writer
  (Layer 3) stops writing — it returns structured output and the planner's
  runner invokes the existing publisher. item_analyzer (Layer 4) reads only.
- **«Layer 3 writes nothing user-visible.»** ✓ — writer's structured
  output goes to the publisher, never directly to chat.
- **«All layers write `agent_runs` when they perform an LLM invocation.»** ✓
  writer_planner, writer, and item_analyzer all record runs.

### Publisher relocation

The existing `agents/writer/publisher.py` stays where it is (it's
code, not a layer), but its call site moves from `writer/runner.py`
to `writer_planner/runner.py`. Sequence:

1. writer_planner_decider emits final `PlannerDecision(package=WriterPackage)`.
2. Planner runner calls `handle_writer_turn(package, deps)` — the writer's
   runner accepts the new WriterPackage shape, performs its single LLM call,
   returns `WriterLLMOutput` **without publishing**.
3. Planner runner calls `publish_writer_result(llm_output, package, deps)` —
   this is the existing publisher, now invoked from one layer up.

That's the only mechanical change in writer to enforce the layer
demotion.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WriterPlanner.decider (tier_1)                                         │
│                                                                         │
│  Deps (rendered into dynamic @agent.instructions):                      │
│   • intent (current user message — parsed for stated subtype/params)    │
│   • recent_messages, case_brief                                         │
│   • prior_artifacts[] — ALL convo workspace_items, each carrying        │
│       (item_id, kind, title, summary, word_count)                       │
│   • attached_items[] — router-selected for THIS turn, same shape        │
│   • detail_level, tone                                                  │
│                                                                         │
│  Tools:                                                                 │
│   ▸ fetch_items(targets=[{item_id, query?}, ...])   [→ item_analyzer]   │
│   ▸ search_templates(subtype, intent)               [pgvector]          │
│   ▸ ask_user(question)                              [DEFERRED]          │
│   ▸ present_plan_for_approval(plan_md)              [DEFERRED]          │
│                                                                         │
│  fetch_items is the SINGLE content path. Per-target query controls      │
│  behavior: query=None → raw content_md returned; query=set → distilled  │
│  against that query. One call covers a mixed batch of both.             │
│                                                                         │
│  Loop: inspect → TRIAGE per item via summary (include/skip/inspect) →   │
│        role-assign included items → gate batches by word_count →        │
│        emit fetch_items (query=None for small / query=set for big) →    │
│        maybe template search → assemble plan → present → user replies   │
│        → … → approved → emit WriterPackage                              │
│                                                                         │
│  Hard cap: 3 present_plan_for_approval cycles per turn.                 │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴─────────────┐
                │ (planner-policy gating)   │
                ▼                           ▼
       batch_word_count ≤ 1000       batch_word_count > 1000
                │                           │
                ▼                           ▼
     fetch_items(query=None)        fetch_items(query="...")
     (passthrough — no LLM)         (LLM-distilled)
                                      both paths → same item_analyzer
                                      (one call, mixed targets allowed)
                              │
                              ▼ WriterPackage
┌─────────────────────────────────────────────────────────────────────────┐
│  writer (tier_1 — existing)                                       │
│  Drafts the final Arabic document grounded in the package.              │
└─────────────────────────────────────────────────────────────────────────┘
```

## Triage via summary — the first filter

Before any `fetch_items` call, the planner asks per item: *is this even
relevant to what the writer needs?* The signal it uses is the persisted
`summary` (column on `workspace_items`, migration 037) plus `title` and
`kind` — all three are already in the planner's dynamic instructions for
every artifact in scope (router-handed `attached_items` + conversation-scope
`prior_artifacts`).

Three triage outcomes per item:

| Outcome | When | Result |
|---|---|---|
| **Skip** | Summary clearly shows the item is unrelated to the drafting task. E.g. user is drafting a contract; a prior `agent_search` artifact about an unrelated criminal case is in convo history. | Item is NOT added to fetch_items targets. It never enters the WriterPackage. The writer never sees it. |
| **Include** | Summary signals the item is relevant (matches subtype, parties, jurisdiction, topic, or the user named it). | Item is added to fetch_items targets. The gating layer (below) decides raw vs distilled based on word_count. |
| **Inspect** | Summary is missing, thin, or ambiguous AND the item might be relevant. | Planner calls `fetch_items` with `query=None` to see raw content, then re-decides include/skip on the next loop iteration. |

Skipping is the most valuable action — it shrinks the WriterPackage to only
the items that matter, which keeps the writer's tier_1 context budget for
the task, not for filtering.

The triage decision happens **inside the decider's reasoning** (no separate
tool). It's reflected in which item_ids appear in the `fetch_items` call:
included = present; skipped = absent; inspect = present with `query=None`
on the first round.

## Distillation gating — planner policy

> ⚠️ **SUPERSEDED** — see § Item Analyzer Integration (UPDATED 2026-05-25) at
> the top of this file. The 1000-word threshold and the
> `WRITER_PLANNER_DISTILL_THRESHOLD` env var were removed in the v2 redesign.
> The analyzer's own prompt now decides full vs partial; the planner does NOT
> gate. The text below is historical context only — do not implement it.

The decider doesn't always call `distill_items`. **The threshold lives in the
planner**, not in the shared item analyzer — different callers will have
different policies (see the item_analyzer plan for the rationale).

**Writer planner threshold (v1): 1000 words per batch.**

A "batch" is a set of items the planner wants the writer to see together.
The planner groups items however it likes (typically by role: all "source"
attachments in one batch, all "research" items in another, the prior_draft
in its own batch).

For each batch:

```
batch_word_count = sum(item.word_count for item in batch)   # from workspace_items.word_count column
if batch_word_count <= 1000:
    # Passthrough: one fetch_items call with query=None per target — item_analyzer
    # returns raw content_md without invoking the LLM. Caller receives source='raw'
    # entries in the bundle.
    targets = [{"item_id": item.item_id, "query": None} for item in batch]
else:
    # Distill: one fetch_items call with a per-target query — item_analyzer batches
    # all queried targets into a single LLM call. Caller receives source='distilled'.
    targets = [{"item_id": item.item_id, "query": planner_query_for(item)} for item in batch]
bundle = await fetch_items(targets=targets)
for fetched in bundle.items:
    analyzed_items.append(AnalyzedItem(..., source=fetched.source, text_md=fetched.text_md))
```

**Mixed batches in one call**: the planner may pass a mix of `query=None` and
`query="..."` targets in one `fetch_items` call. item_analyzer handles both —
raw passthroughs return immediately, queried targets are batched into a single
LLM round-trip. This is the right call when some items in a batch are small
(passthrough) and others are large (distill).

The planner reads `word_count` directly from `workspace_items.word_count`
(migration 048, auto-maintained by trigger). It does NOT load `content_md`
just to count.

**Per-item queries are the norm.** Even when the planner batches items
together, each item carries its own `query` so the distiller can focus
differently per item (e.g. one item → "extract late-payment clauses", another
item → "extract jurisdiction and parties"). The shared analyzer packs all
targets into one LLM call regardless.

**Threshold is configurable** via `WRITER_PLANNER_DISTILL_THRESHOLD` env var
(default 1000). Lets us tune from telemetry without code changes.

## Role assignment

The planner labels each analyzed item with one of four roles **based on its
own inspection** (the shared item_analyzer doesn't know about roles):

| Role | Meaning | Writer behavior |
|---|---|---|
| `template` | Scaffolding to mimic — structure, boilerplate, clauses | Adopt structure; fill in parties/dates/amounts from sources |
| `source` | Raw facts the document is about — parties, terms, contracts being summarized | Quote / cite / fold into prose |
| `reference` | Background that may be cited but isn't the subject | Cite when relevant, otherwise ignore |
| `prior_draft` | The agent_writing being revised | Treat as the starting point; apply revision_targets |

The planner derives the role from user wording + item title + item summary.
Two-pass correction (planner hint → analyzer correction) is NOT in scope here
since the shared analyzer doesn't return a role. If the planner's role
assignment is wrong, the user is the corrective surface during plan approval.

**Special case — user-supplied template**: when the planner labels any item
with `role='template'`, it **skips `search_templates`** entirely. The user's
template wins over the system library.

## Templates — graceful no-results (v1)

`system_templates` table exists per migration 046 but has zero ingested rows.
v1 ships with this state. `search_templates` returns `[]`. The decider's
prompt covers the no-template path: «إن لم توجد قوالب، أنشئ هيكلاً مناسباً
للنوع دون الاعتماد على قالب». Ingestion is a **separate follow-up plan**.

Subtype mapping (English writer subtype → Arabic enum):

```
contract       → عقد
memo           → مذكرة
legal_opinion  → رأي_قانوني
defense_brief  → مذكرة            (closest match — Saudi practice merges)
letter         → إنذار             (or none, depending on intent)
summary        → none              (no template applies)
```

## The planner — phases, tools, loop

### Decider construction

`agents/writer_planner/agent.py::create_writer_planner_decider()`
returns an `Agent[WriterPlannerDeps, [PlannerDecision, DeferredToolRequests]]`.

```python
agent = Agent[WriterPlannerDeps, list[PlannerDecision | DeferredToolRequests]](
    model=get_agent_model("writer_planner_decider"),   # tier_1 slot
    deps_type=WriterPlannerDeps,
    output_type=[PlannerDecision, DeferredToolRequests],  # list syntax — see below
    instructions=WRITER_PLANNER_SYSTEM_PROMPT,
)
```

- **Model resolution** — `get_agent_model("writer_planner_decider")` returns
  a `FallbackModel` over the tier_1 chain (qwen3.6-plus → deepseek-v4-pro).
- **`output_type=[A, B]` (list syntax, NOT `Union[A, B]`)** — each list
  member becomes a separate "output tool" in the underlying API call.
  Cleaner schema for the model, better selection accuracy than a single
  union type. The runner type-narrows on the consumer side
  (`isinstance(result.output, DeferredToolRequests)` → pause path; else
  final `PlannerDecision`).
- **Static `instructions=`** — `WRITER_PLANNER_SYSTEM_PROMPT` carries the
  rules that don't change per turn: examine-before-asking protocol, the
  two-phase gating policy (skip-both-by-default), the core invariant
  (planner LLM never reads raw `content_md`), the parallel-emission nudge.
- **Dynamic `@agent.instructions`** — `build_writer_planner_instructions(ctx)`
  renders per-turn context: parsed intent, recent_messages, the
  `attached_items` set with `(WI-{seq}, kind, title, summary, word_count)`
  per row, and the conversation-scope `prior_artifacts` set in the same
  shape. **Per the core invariant: `content_md` is NEVER rendered here —
  only summary + metadata.** **Per the WI-{seq} alias protocol: the LLM
  surface uses `WI-{seq}` aliases, never raw UUIDs** — items without a
  `wi_seq` (rare — case-only / pre-052) render `WI-?` with a debug log.
  The function may be `async` if a future expansion needs to fetch
  context from external services (e.g. user preferences); v1 is sync,
  reading from already-loaded `deps`.
- **4 tools registered**: `analyze_items`, `search_templates`, `ask_user`,
  `present_plan_for_approval`. The first two are non-deferred; the last
  two raise `CallDeferred` to pause the run.

### Tool docstrings are the model's only spec

Each tool's docstring is the model's **complete specification** for when
and how to call it — Pydantic AI passes the docstring (Google/NumPy/Sphinx
style) to the LLM verbatim alongside the parameter schema. The decider
will not know `analyze_items` is for triage-distillation, or that it
should batch multiple WIs in one call, unless the docstring says so.
Treat tool docstrings as prompt engineering, not documentation. The
`analyze_items` docstring in § Item Analyzer Integration above is the
canonical example — copy that level of specificity for the other three.

### `fetch_items` tool — thin wrapper

```python
# agents/writer_planner/tools.py

@agent.tool
async def fetch_items(
    ctx: RunContext[WriterPlannerDeps],
    targets: list[ItemQuery],
) -> ItemBundle:
    """Fetch content for one or more workspace_items.

    Each target carries an optional `query`:
      - query=None  → return the item's raw content_md verbatim (no LLM)
      - query="..." → distill the item against that query (one tier_2 LLM call
                       covers all queried targets in this batch)

    Use this for ALL content the planner wants the writer to see. Compute
    word_count from prior_artifacts (the column workspace_items.word_count
    is already loaded into your dynamic instructions); pass query=None when
    a batch totals ≤ WRITER_PLANNER_DISTILL_THRESHOLD words (default 1000),
    pass query="..." otherwise. Mixed batches are fine — passthroughs return
    immediately, queried targets get batched into one LLM call.

    Args:
        targets: list of {item_id, query: str | None}.

    Returns:
        ItemBundle (per item: text_md + source='raw'|'distilled' + word_counts
        + telemetry).
    """
    cache_key = _make_cache_key(targets)
    if cache_key in ctx.deps._fetch_cache:
        return ctx.deps._fetch_cache[cache_key]

    from agents.memory.item_analyzer import fetch_items as _fetch, build_item_analyzer_deps
    deps = build_item_analyzer_deps(
        supabase=ctx.deps.supabase,
        http_client=ctx.deps.http_client,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
    )
    bundle = await _fetch(targets, deps)
    ctx.deps._fetch_cache[cache_key] = bundle
    return bundle
```

The cache is keyed by `(sorted(item_ids), query_hashes)` so re-plan rounds
that hit the same batch + queries don't re-pay.

### The loop

1. Decider inspects intent + prior_artifacts (summaries + word_counts) +
   attached_items.
2. Decider **triages** each item via summary (include / skip / inspect) — see
   the triage section above. Skipped items are dropped from consideration.
3. Decider assigns roles to each included item (template/source/reference/prior_draft).
4. Decider groups included items into batches it wants to feed the writer.
5. For each batch the decider decides: inline raw (small) vs distill (large).
   This is **the decider's own reasoning**, not a runner branch — the prompt
   carries the threshold as guidance and the decider chooses the tool args
   accordingly.
6. Decider emits at most one `fetch_items` call covering every INCLUDED item
   it wants the writer to see. Targets in that call carry `query=None` (raw
   passthrough) or `query="..."` (LLM distill) based on the per-batch
   word-count math. Skipped items never appear in the call. `search_templates`
   only fires if no `role='template'` was assigned.
7. Decider reads results, may re-triage (e.g. an inspected item turns out
   irrelevant after seeing raw content — drop it on the next round) or
   dispatch more if a new realization arises.
8. Decider emits `present_plan_for_approval(plan_md="...")` → run pauses
   with `DeferredToolRequests`.
9. Orchestrator persists the agent_runs row in `awaiting_user` state with
   `pause_reason='approve_plan'`, emits an SSE token stream of the plan_md to
   chat, returns control to the user.
10. User replies. Resume: `agent.run(message_history=…,
    deferred_tool_results=DeferredToolResults({tool_call_id: user_reply}))`.
11. Decider reads reply. Either re-triage + re-dispatch + re-present, or
    emit final `PlannerDecision(approved=True, package=WriterPackage(...))`.

### Pause/resume — two deferred tools

Both raise `CallDeferred` like the deepsearch `ask_user`. The orchestrator
distinguishes them by **tool name** when persisting the pause row:

| Tool | `pause_reason` column | UI affordance |
|---|---|---|
| `ask_user` | `'clarify'` | Plain Arabic question rendered in chat |
| `present_plan_for_approval` | `'approve_plan'` | Plan_md rendered in chat; user replies in chat |

The existing `agent_runs` table gains one column: `pause_reason TEXT NOT NULL
DEFAULT 'clarify'`. The pause state already lives on `agent_runs` (status
flips to `'awaiting_user'`, plus `deferred_payload` / `question_text` /
`asked_at` / `expires_at` columns from migration 033) — `pause_reason` just
distinguishes which deferred tool fired. Single row per planner turn —
updated in place on each new pause.

### Iteration cap — hard at 3

`WriterPlannerDeps` tracks `present_count: int`. On the 4th call to
`present_plan_for_approval` the tool implementation bypasses the deferred
raise, auto-approves with the current plan, and the decider must emit a
final `PlannerDecision` next round. Logged + telemetry-tagged so we can
tune later.

## `WriterPackage` — the planner's payload to the writer

> ⚠️ **`AnalyzedItem` shape SUPERSEDED** — the version below uses `text_md` +
> `source='raw\|distilled'`. The v2 redesign renames `text_md` → `body_md`,
> replaces `source` with `need: Literal["full","partial"]`, and adds
> `refs_needed` / `resolved_refs_md` / `extracted_metadata`. See the new
> `AnalyzedItem` definition in § Item Analyzer Integration at the top of this
> file. The `WriterPackage` outer shape (envelope fields) carries over.

```python
class AnalyzedItem(BaseModel):
    """One workspace_item the planner inspected, labeled and ready for the writer."""
    item_id: str
    title: str
    kind: str                                # original workspace_items.kind
    role: Literal["template", "source", "reference", "prior_draft"]
    source: Literal["raw", "distilled"]      # how text_md was produced
    text_md: str                             # either raw content_md or distilled_md
    word_count_before: int                   # original content_md word count (from row)
    word_count_after: int                    # == word_count_before when source='raw'

class WriterPackage(BaseModel):
    intent_ar: str                           # one-paragraph distilled intent
    subtype: WriterSubtype
    edit_mode: Literal["fresh", "revise", "instruct"]
    plan_md: str                             # the user-approved plan
    analyzed_items: list[AnalyzedItem]       # everything the planner included
    system_templates: list[TemplateRef]      # from search_templates (may be empty)
    style: WriterStyle                       # detail_level + tone

    # Convenience views — derived properties, not separate fields:
    # - user_templates(): analyzed_items with role='template'
    # - sources(): analyzed_items with role='source'
    # - prior_draft(): analyzed_items with role='prior_draft' (at most one)
```

The writer's user message renders the package as XML blocks:

```
<plan>...</plan>
<templates>
  <template source="user" item_id="..." title="...">{text_md}</template>
  <template source="system" template_id="..." type="عقد">{full content}</template>
</templates>
<sources>
  <source item_id="..." kind="attachment" source="raw|distilled">
    {text_md}
  </source>
</sources>
<references>
  <reference item_id="..." kind="agent_search" source="raw|distilled">
    {text_md}
  </reference>
</references>
<prior_draft>
  <body item_id="..." source="raw|distilled">{text_md}</body>
</prior_draft>
<user_request>{intent_ar}</user_request>
<preferences detail_level="..." tone="..." />
```

The writer doesn't need to know whether each block was distilled or raw — it
just drafts against `text_md`. The `source` attribute is there for telemetry
/ debugging only.

## Edit routing

> ⚠️ **Dispatch column SUPERSEDED** — the table below references `fetch_items`
> with `query=None` raw passthroughs and the 1000-word batch gating. Under v2:
> one `analyze_items` call covers ALL targets (refs + meta mixed); the
> analyzer's verdict per WI (`full` / `partial` / `none`) replaces the
> "raw vs distilled" branching. The **edit-mode scenarios themselves**
> (fresh / revise / instruct) remain valid — only the dispatch mechanics
> change. See § Item Analyzer Integration at the top of this file.

The decider decides the dispatch set itself based on intent + edit_mode +
batch word_counts:

| Scenario | edit_mode | Typical dispatch |
|---|---|---|
| Fresh draft, user-attached template (500w) + 2 source attachments (300w each = 600w) | `"fresh"` | one `fetch_items` with 3 targets, all `query=None` (each batch ≤ 1000 → raw passthrough), NO `search_templates` |
| Fresh draft, sources but no template, attachments total 2400w | `"fresh"` | one `fetch_items` with `query="..."` per source (over threshold → distill), plus `search_templates(subtype, intent)` |
| Tone tweak on prior draft (1500w) | `"instruct"` | one `fetch_items` for the prior_draft with `query="extract revision targets"` (over threshold → distill), no other dispatches |
| New attachment (200w) dropped, revise existing draft (800w) | `"revise"` | one `fetch_items` with 2 targets, both `query=None` (combined 1000w — at threshold, raw passthrough) |

`skip_distill_for_instruct_edits` is no longer a special path — it falls out
naturally from the gating policy. A simple tone tweak with no new evidence
means the planner doesn't dispatch anything at all and produces a one-line
plan that the user usually approves on first present.

## File manifest

### NEW

```
agents/writer_planner/
  __init__.py
  agent.py                     ← create_writer_planner_decider()
  prompts.py                   ← WRITER_PLANNER_SYSTEM_PROMPT +
                                  build_writer_planner_instructions() +
                                  examine-before-asking protocol +
                                  gating-threshold guidance
  deps.py                      ← WriterPlannerDeps + build_writer_planner_deps()
  models.py                    ← PlannerDecision, WriterPackage, AnalyzedItem,
                                  TemplateRef, WriterStyle
                                  (NO discriminated union — analyzed items
                                  carry source='raw|distilled' + plain text_md)
  runner.py                    ← handle_writer_planner_turn(major_input, supabase) →
                                  internally awaits handle_writer_turn(package, deps)
  tools.py                     ← The 4 @agent.tool definitions; fetch_items
                                  wraps the shared agents/memory/item_analyzer
                                  (item_analyzer is the SINGLE content path —
                                  no read_workspace_item on this planner)
  distill/
    __init__.py
    template_search.py         ← async def search_templates(supabase, subtype, intent)
                                  → list[TemplateRef]  — pgvector only, NO LLM
  tests/
    __init__.py
    test_decider.py            ← Pydantic AI TestModel + FunctionModel coverage
    test_template_search.py    ← pgvector mocked + graceful empty path
    test_runner.py             ← end-to-end with stubbed tools
    test_loop_iteration_cap.py ← exercises the 3-round cap
    test_examine_before_asking.py ← the real contract example must NOT call ask_user
    test_gating_policy.py      ← batches ≤ threshold use read_workspace_item;
                                  > threshold use distill_items

backend/app/services/
  writer_planner_context.py    ← load_writer_planner_context(supabase, user_id,
                                  conversation_id) → ArtifactSummaryView[]
                                  (selects item_id, kind, title, summary, word_count)

shared/db/migrations/
  053_agent_runs_pause_reason.sql    ← ALTER TABLE agent_runs ADD COLUMN
                                        pause_reason TEXT NOT NULL DEFAULT 'clarify'
                                        (047 is already taken by storage_rls_owner_id; 053 is next free)
```

### MODIFIED

```
agents/writer/models.py
  + WriterPackage class (imported from planner.models or re-exported)
  + WriterInput.from_package(package: WriterPackage, ...) helper

agents/writer/prompts.py
  + build_writer_user_message_from_package(package) — XML render described above
  (existing build_writer_user_message stays for legacy callers / unit tests)

agents/writer/runner.py
  + handle_writer_turn() accepts either WriterInput OR WriterPackage; when
    given a package, calls _populate_deps_from_package + the new message builder.
  No behavior change for existing callers.

agents/orchestrator.py
  ~ _run_writer() — replaced body with `return await handle_writer_planner_turn(
    major_input, supabase)`. The planner internally calls the writer.
  ~ pause-handling branch extended to also catch planner pauses from the
    writing family. _record_deferred takes pause_reason kwarg.

agents/utils/agent_models.py
  + add slot: "writer_planner_decider" (tier_1).
  (item_analyzer slot lives in its own plan.)
```

## Orchestrator wiring change

```python
async def _run_writer(
    input: MajorAgentInput,
    subtype: str | None,
    supabase: SupabaseClient,
) -> SpecialistResult:
    from agents.writer.planner import handle_writer_planner_turn
    return await handle_writer_planner_turn(input, subtype, supabase)
```

The pause-handling branch (currently keyed on `ds_outcome.kind == "paused"`
for `deep_search`) extends to also catch planner pauses from the writing
family.

## Testing strategy — Pydantic AI primitives

All decider tests use Pydantic AI's test doubles, never real LLM calls.

| Primitive | Use for |
|---|---|
| `TestModel` | Zero-config double that returns deterministic responses (echoes the schema). Use when you only care that the agent calls the right tools / accepts the right output shape — not what the LLM "decides". |
| `FunctionModel` | Programmable double — supply a function that receives messages and returns a `ModelResponse`. Use to script specific tool-call sequences, raise `CallDeferred` from a tool, or simulate multi-round behavior. |
| `agent.override(model=…, deps=…)` | Context-manager swap for tests. Real `create_writer_planner_decider()` produces the agent; tests override its model + deps inside a `with` block. |
| Manual `ModelRequest`/`ModelResponse` | Construct `message_history` directly (with `UserPromptPart`, `ToolCallPart`, `ToolReturnPart`) to test resume flow. Needed for `DeferredToolResults` roundtrip tests. |
| `agent.override(deps=test_deps)` | Inject mock Supabase + mock http_client + an in-memory `_fetch_cache` so tools execute their real bodies against fakes. |

For deferred-tool tests, the pattern is: (1) `FunctionModel` whose function
emits a `ToolCallPart` for `present_plan_for_approval`; (2) the tool raises
`CallDeferred`; (3) assert the run returns `DeferredToolRequests`; (4)
resume with `agent.run(message_history=result.all_messages(),
deferred_tool_results=DeferredToolResults({tool_call_id: "موافق"}))`; (5)
assert the next run emits a final `PlannerDecision`.

## Test plan

| Test | Covers |
|---|---|
| `test_decider.py::test_decides_dispatch_for_fresh_draft` | Decider with TestModel returns expected dispatch set for a fresh contract intent + 2 attachments + 1 research item |
| `test_decider.py::test_pauses_with_present_plan_for_approval` | FunctionModel raises CallDeferred on the gate tool; runner returns DeferredToolRequests |
| `test_decider.py::test_skips_analyzer_for_turn_attached_items` | Items attached this turn → planner emits NO `analyze_items` call; package built with `need="full"` + raw `body_md` per § Two skippable phases |
| `test_decider.py::test_planner_llm_never_sees_content_md` | Inspect message_history across an end-to-end run; assert no `content_md` string from any WI appears in any model request (planner LLM is summary-only per § Core invariant) |
| `test_decider.py::test_emits_parallel_when_both_phases_fire` | Bottom-row case (strategy unclear + items unclear) → FunctionModel asserts both `analyze_items` and `search_templates` ToolCallParts appear in the SAME ModelResponse |
| `test_decider.py::test_resumes_with_approval` | message_history + DeferredToolResults("موافق") resumes and emits final PlannerDecision |
| `test_decider.py::test_resumes_with_edit_feedback` | User reply rejecting the plan triggers re-dispatch, then re-present |
| `test_decider.py::test_skips_search_templates_when_user_attaches_one` | One attachment assigned role="template" → search_templates is NOT called |
| `test_decider.py::test_triages_out_irrelevant_artifact` | prior_artifacts contains an unrelated criminal-case agent_search artifact; decider triages it as "skip" → that item_id never appears in any fetch_items call and is NOT in the WriterPackage |
| `test_decider.py::test_inspects_thin_summary_then_decides` | Artifact has a near-empty summary; first round fetches it raw (query=None), second round triages include/skip based on raw content |
| `test_gating_policy.py::test_small_batch_passthrough` | 3 items × 300w each → planner emits one fetch_items with 3 targets all query=None → bundle items have source='raw' |
| `test_gating_policy.py::test_large_batch_distilled` | 2 items × 1500w each → planner emits one fetch_items with 2 targets carrying queries → bundle items have source='distilled' |
| `test_gating_policy.py::test_mixed_batch_in_one_call` | Small item (passthrough) + large item (distill) in one fetch_items call → bundle has source='raw' for one, source='distilled' for the other |
| `test_gating_policy.py::test_threshold_env_override` | WRITER_PLANNER_DISTILL_THRESHOLD=500 → smaller batches now carry queries (distill) |
| `test_template_search.py::test_returns_empty_when_no_rows` | v1 graceful path |
| `test_template_search.py::test_pgvector_cosine_with_type_filter` | Mocked pgvector returns top-5 ordered |
| `test_runner.py::test_handle_writer_planner_turn_end_to_end` | Stubbed tools, real PlannerDecision → WriterPackage → handle_writer_turn called with package |
| `test_loop_iteration_cap.py::test_caps_at_3_present_cycles` | 4th call to present_plan_for_approval auto-approves |
| `test_runner.py::test_skip_distill_for_instruct_edit` | "اجعل النبرة أرسم" with prior draft 200w → empty dispatch, writer fires straight |
| `test_examine_before_asking.py::test_contract_example_no_clarify` | The real example from the design discussion (PDF template + 2 image sources + explicit parameters) must NOT trigger ask_user — decider goes straight to dispatch + present |

Existing `writer/tests/test_runner.py` continues to pass — the writer's
input contract (WriterInput) is unchanged, only a new constructor path
(WriterPackage) is added.

## Out of scope (deferred follow-ups)

1. **`system_templates` ingestion** — separate plan: curate 5-10 templates per
   `template_type_enum` value, embed via Alibaba text-embedding-v4, INSERT via
   service_role script. Until then, planner runs with empty template_search
   results.
2. **User-authored templates** — referenced in migration 046's comment as v2 /
   Rayhan.
3. **Workspace-pane plan artifact** — for now plan_md lives in chat only. If
   we want pin/recall, add a `kind='writer_plan'` workspace_items mirror later.
4. **Inline-span surgical edits** ("change only this paragraph") — out of v1.
5. **Per-caller threshold tuning** — start at 1000 for writer_planner; revisit
   after first 20 real runs. Other future callers (deep_search planner,
   router) will set their own thresholds in their own plans.
6. **Cross-batch distillation optimization** — v1 emits at most one
   `distill_items` per turn (the planner groups all over-threshold items into
   one call). If a future case wants multiple parallel batches the loop
   already supports it; just not exercised in v1 tests.

## Dependencies

- Migration 046 (`system_templates`) — already drafted; verify applied state
  with `mcp__supabase__list_migrations` before merging.
- Migration 053 (`agent_runs_pause_reason`) — included in this plan's manifest.
- Migration 048 (`workspace_items.word_count`) — already drafted; required for
  the planner's gating arithmetic. Verify applied.
- **Companion plan**: `.claude/plans/item_analyzer.md` must land first — the
  writer planner's `distill_items_tool` imports from
  `agents/memory/item_analyzer`.
- `agents/utils/agent_models.py` — `writer_planner_decider` slot registered
  (tier_1).

## Telemetry & observability

Every planner run records a Logfire span with structured metadata for
post-hoc analysis. This is non-negotiable in production — the planner is
the most expensive component in the writing path (tier_1 model, possibly
multiple turns), so every run must be traceable.

### Setup (one-time, at app startup)

```python
import logfire
logfire.configure(service_name="luna-backend", environment=settings.environment)
logfire.instrument_pydantic_ai()    # auto-instruments every Agent.run()
```

### Per-run metadata

`handle_writer_planner_turn` attaches metadata to every `agent.run()` call:

```python
result = await agent.run(
    user_prompt,
    deps=deps,
    message_history=history,
    deferred_tool_results=deferred_results,
    usage_limits=UsageLimits(
        request_limit=10,          # max LLM round-trips per turn
        tool_calls_limit=20,       # max tool invocations per turn
        output_tokens_limit=8000,  # tier_1 cap
    ),
    metadata={
        "agent": "writer_planner_decider",
        "layer": 2,
        "tier": "tier_1",
        "user_id": deps.user_id,
        "conversation_id": deps.conversation_id,
        "turn_number": deps.turn_number,
        "present_count": deps.present_count,   # tracks the 3-cycle cap
        "phase_skip": phase_skip_flags(deps),  # which of the two phases were skipped this turn
    },
)
```

### What to record (beyond Pydantic AI's automatic spans)

| Event | When | Why |
|---|---|---|
| `writer_planner.phase_skip.analyzer` | When the bypass path fires (no `analyze_items` call) | Track the gating policy's hit rate — too many = wasted analyzer dep, too few = wasted tier_1 tokens |
| `writer_planner.phase_skip.present` | When the planner emits `PlannerDecision` without ever calling `present_plan_for_approval` | Same — measures the clean-turn rate |
| `writer_planner.present_cycle` | Each call to `present_plan_for_approval`, with `cycle_number` (1/2/3) | Distribution tells us if the 3-cap is the right number |
| `writer_planner.auto_approved` | When the 4th present cycle is auto-approved | Alert signal — means a user got stuck in a loop |
| `writer_planner.parallel_dispatch` | When `analyze_items` + `search_templates` overlap | Verifies the parallel-emission prompt is working |

### UsageLimits & ConcurrencyLimit

Apply `UsageLimits` at every `agent.run()` call (per-turn budget). For
multi-tenant safety, `_run_writer` in the orchestrator should also apply a
`ConcurrencyLimit(max_running=N)` so a flood of concurrent users can't
exhaust tier_1 quotas. Both raise typed exceptions (`UsageLimitExceeded` /
`ConcurrencyLimitExceeded`) that the orchestrator maps to a user-facing
Arabic message: «حدثت ضغوط على الخادم، حاول مرة أخرى بعد قليل.»

### Error handling

| Exception | Where | User-facing handling |
|---|---|---|
| `UnexpectedModelBehavior` | tier_1 model exhausted retries on validation | Log via `capture_run_messages()`; tell user «تعذرت معالجة الطلب، حاول مرة أخرى.» |
| `UsageLimitExceeded` | Per-turn budget breached | Log; tell user «استنزفت معطيات الطلب، اقتصر على نقطة واحدة.» |
| `ModelHTTPError` (fallback exhausted) | Both tier_1 providers down | Log; tell user «المزود مؤقتاً غير متاح، أعد المحاولة لاحقاً.» |
| `CallDeferred` (caught by orchestrator) | Normal pause flow | Not an error — orchestrator updates `agent_runs` row to `status='awaiting_user'` + sets `pause_reason`, emits SSE. |

---

## Build order

1. **Land the item_analyzer plan first** (separate file). Until that ships
   the writer planner's distill tool has nothing to call.
2. Migration 053 (`agent_runs.pause_reason` column) — unblocks orchestrator
   pause-row write. (Plan originally said 047 but that number is taken;
   bumped to 053.)
3. `WriterPackage` + `AnalyzedItem` + `WriterInput.from_package` + new
   `build_writer_user_message_from_package` in `agents/writer/` —
   writer accepts the new shape, behavior unchanged for existing callers.
   Tests for the new path.
4. `template_search.py` — pgvector wrapper + tests including the empty-table
   graceful path.
5. `writer_planner_context.py` — loads ArtifactSummaryView list with word_count.
6. `WriterPlannerDeps` + `build_writer_planner_instructions` + the static
   system prompt (examine-before-asking + gating guidance) + the 5 tools.
7. `create_writer_planner_decider()` + `handle_writer_planner_turn()`.
8. Orchestrator wiring + pause-row extension + SSE event for plan
   presentation.
9. End-to-end loop tests including:
   - the examine-before-asking regression (`test_contract_example_no_clarify`)
   - the gating-policy matrix (`test_gating_policy.py`)
