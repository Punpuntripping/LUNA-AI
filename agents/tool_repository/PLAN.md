# Markdown Section Editor Tool — Design Plan

A Pydantic AI tool that **deletes one section** of a markdown file and **adds another** — reliably, even when the LLM caller is imprecise about whitespace or duplicate headings.

> Scope: section-level edits on markdown (`.md`) files. Not a general-purpose code editor.

---

## 1. The Core Problem

A section-editing tool must answer two questions reliably:

1. **Which section do we delete?** (targeting)
2. **Where exactly does the new section go?** (anchoring)

Naive implementations fail because:

- Headings repeat (`## Notes` appears 3 times)
- Whitespace drifts (`## Old Notes ` vs `## Old Notes`)
- `#` appears inside fenced code blocks and gets mistaken for a heading
- Custom anchor syntax (`## Old Notes {#old-notes}`) breaks naive equality
- HTML blocks contain headings the parser ignores

---

## 2. Targeting Strategy Options

Ordered weakest → strongest, with what each is good for.

### Option A — First-match line scan
```python
for i, line in enumerate(lines):
    if line.strip() == heading.strip():
        return i
```
- **Reliability:** Low
- **Pros:** trivial code, no dependencies
- **Cons:** silent wrong-section edits when heading repeats; fooled by `#` in code fences
- **Verdict:** rejected. This is what my v1 draft did and it's the failure mode every research source warns about.

### Option B — Exact match + uniqueness enforcement (Claude Code model)
```python
matches = [i for i, l in enumerate(lines) if l.strip() == heading.strip()]
if len(matches) != 1:
    raise AmbiguousMatch(f"{len(matches)} matches for '{heading}'")
```
- **Reliability:** Medium-high
- **Pros:** simple, fails loudly, forces caller to provide a unique identifier
- **Cons:** still fooled by code fences; whitespace-fragile
- **Verdict:** good fallback. This is exactly what Claude Code's `Edit` tool does.

### Option C — Fuzzy match ladder (Aider model)
1. Try exact match
2. Try whitespace-normalized match
3. Try regex with flexible whitespace between words
- **Reliability:** High in practice
- **Pros:** tolerates "almost correct" LLM output (very common)
- **Cons:** more complex error messages; can mask real bugs
- **Verdict:** worth adopting as a fallback **after** uniqueness check fails on exact match.

### Option D — Markdown AST + heading path (RECOMMENDED)
Parse with `markdown-it-py` → walk the token stream → identify `heading_open` tokens with their level and inline content → compute section span from heading to next same-or-higher level heading.
- **Reliability:** High
- **Pros:** ignores `#` inside code fences automatically; understands heading levels; survives whitespace; can target by heading path (`Guide > Setup > Windows`)
- **Cons:** requires `markdown-it-py` dependency; needs token→source-offset mapping
- **Verdict:** **primary strategy.**

### Option E — Stable slug anchors
Use `markdown-it-py` slug rules (e.g. `## Old Notes` → `old-notes`) as the unique key. Caller passes `slug="old-notes"` instead of heading text.
- **Reliability:** Highest
- **Pros:** survives any whitespace/punctuation change; round-trippable; matches GitHub/MkDocs anchor convention
- **Cons:** caller must know the slug; needs collision handling for duplicate slugs
- **Verdict:** **expose as optional `target_slug` parameter alongside heading text.**

---

## 3. Recommended Architecture

```
target = slug | heading_path | heading_text  (caller picks; tool validates uniqueness)
        ↓
markdown-it-py AST
        ↓
locate heading_open token + section span (until next ≤-level heading)
        ↓
splice source by character offset (not line index)
        ↓
write back, return diff summary
```

### Tool signature
```python
class EditMarkdownSection(BaseModel):
    file_path: str
    # Targeting — exactly one must be provided:
    target_slug: str | None = None              # strongest: "old-notes"
    target_path: list[str] | None = None        # strong: ["Guide", "Setup", "Windows"]
    target_heading: str | None = None           # weakest: "## Old Notes"
    # New section:
    new_heading: str
    new_body: str
    # Placement of new section:
    insert_after_slug: str | None = None        # if None → append at end
    # Safety:
    dry_run: bool = False                       # return diff without writing
```

### Return value
Always return a structured result, never silently succeed/fail:
```python
class EditResult(BaseModel):
    success: bool
    deleted_span: tuple[int, int] | None        # char offsets
    inserted_at: int | None
    diff: str                                   # unified diff
    error: str | None                           # populated when not unique / not found
```

---

## 4. Best Practices (drawn from research)

### From Claude Code's `Edit` tool
- **Fail loudly on ambiguity.** Never silently pick the first of N matches.
- **Force the caller to provide a unique target.** Either uniqueness must be inherent (slug) or the caller must add disambiguating context.
- **Read-before-edit invariant.** Tool should refuse to edit a file it hasn't read in this session (or hash-check the file).

### From Aider
- **Fault-tolerant matching ladder.** Real LLMs produce whitespace-drifted output ~30% of the time. Have a fallback ladder; don't punish near-misses.
- **Edit format dominates accuracy.** GPT-4 Turbo: 20% on SEARCH/REPLACE → 61% on unified diffs. Translation for us: prefer a format the model finds easy to emit correctly — short slugs beat long heading strings.
- **Helpful error messages.** When a match fails, return *what the tool actually saw* (closest match + Levenshtein distance), not just "not found".

### From AST-based code agents (CODESTRUCT)
- **Operate on named units, not text spans.** Pass@1 up 1.2–5.0%, tokens down 12–38%.
- **Avoid false positives from strings/comments.** AST awareness is the only reliable way; in markdown that means parsing fences correctly.

### From markdown-it ecosystem
- `markdown-it-anchor` skips headings inside HTML blocks — document this limitation.
- Slug generation needs a deterministic, collision-handled rule (GitHub-style: lowercase, replace non-alphanumeric with `-`, append `-1`, `-2` on collision).
- TOC generation is a useful free byproduct — expose `list_sections()` as a sibling tool so the LLM can introspect before editing.

### General LLM-tool ergonomics
- **Dry-run mode.** Let the agent preview the diff before committing.
- **Idempotency hint.** If the new section already exists with identical content, return success without writing.
- **Atomic write.** Write to a temp file, then rename. Prevents corruption on crash.
- **Return a diff, not just "ok".** The calling agent can show the user what changed without re-reading the file.

---

## 5. Failure Modes & Mitigations

| Failure | Mitigation |
|---|---|
| Heading appears twice | Refuse; surface both locations in error |
| Heading inside code fence | AST parser ignores; line-scan fallback skips fences |
| Heading inside HTML block | Document limitation; opt-in `--include-html` flag later |
| Trailing whitespace / anchor suffix | Whitespace-normalized fallback; slug-based targeting bypasses |
| File changed since last read | Compare mtime or hash; refuse stale edits |
| New section heading collides with existing | Refuse with clear error; suggest `replace_section` instead |
| Concurrent writer | Atomic rename + advisory lock |
| Cross-platform line endings (CRLF/LF) | Detect on read, preserve on write |

---

## 6. Out of Scope (for v1)

- Editing tables / lists / code blocks below section granularity
- Cross-file edits (e.g. moving a section between files)
- Frontmatter editing (YAML/TOML)
- Live preview / round-trip rendering
- Conflict resolution across concurrent edits

These belong in v2 as separate tools.

---

## 7. Implementation — `edit_supabase_md.py` (BUILT)

The tool is implemented in `agents/tool_repository/edit_supabase_md.py`. After
working through the options, the **primary** strategy is **anchored exact-string
replacement with a uniqueness check**, NOT AST/slug — because the agent quotes
text it can already see (an LLM strength) instead of computing structure, and it
works on any text at any granularity, not just well-formed markdown sections.

Core shape (see the file for the full version):

```python
def locate(content: str, old_text: str) -> Match:
    # 1. exact substring — must be unique (count == 1)
    # 2. whitespace-normalized regex — must be unique
    # 3. raise MatchError with the closest line (difflib) to guide a retry

@agent.tool
async def edit_supabase_md(ctx, item_id, old_text, new_text, dry_run=False) -> str:
    content, version = _fetch(supabase, item_id)          # read + lock token
    new_content, match = apply_edit(content, old_text, new_text)
    if not _write(supabase, item_id, new_content, version):  # UPDATE ... WHERE version=
        raise ModelRetry("changed since read — re-read and retry")
```

Storage target: `workspace_items.content_md` (Arabic markdown). Concurrency:
optimistic lock on `updated_at` because `agent_writing` artifacts are co-edited
by the user in the UI.

---

## 8. Decision Summary

**Primary strategy (BUILT):** anchored exact-string replacement + mandatory
uniqueness check — the LLM passes a verbatim `old_text` it copied from the
artifact, the tool refuses if it isn't unique.
**Fallback ladder:** exact match → whitespace-normalized regex → fail with the
closest near-match (difflib) surfaced as a `ModelRetry` hint.
**Concurrency:** optimistic lock (`updated_at` guard); lost-update → `ModelRetry`.
**Error policy:** fail loudly via `ModelRetry` so the model self-corrects.
**Output:** confirmation string + unified diff.

**AST/slug targeting** (Options D/E) is kept as a *future, optional* second tool
(`replace_section`) for the specific "swap this whole section" ergonomic case —
not the primary path.

This combines the **uniqueness discipline** of Claude Code's Edit tool with the
**fault tolerance** of Aider's match ladder, applied to Luna's Supabase-backed
Arabic artifacts.

---

## 10. `add_user_template` tool (BUILT)

A second reusable tool in this repository, implemented in
`agents/tool_repository/add_user_template.py`. Unrelated to section editing —
it lets an agent **save a markdown template to the current user's personal
library** (the "قوالبي" library, backed by the `user_templates` table from
migration 055).

**Purpose.** When the user EXPLICITLY asks to save something as a template
(«احفظ هذا كقالب»), the agent calls this tool to persist a reusable markdown
template scoped to the current user. It is NOT for proactive saving — drafting
a document is not the same as saving a template.

**Signature.**
```python
@agent.tool
async def add_user_template(ctx, title: str, content_md: str) -> str
```
Returns an Arabic confirmation string including the new `template_id`.

**Implementation.** A single insert into `user_templates` via the sync
service-role Supabase client on `ctx.deps`:
```python
ctx.deps.supabase.table("user_templates").insert({
    "user_id": ctx.deps.user_id,   # ownership — never supplied by the model
    "title": title,
    "content_md": content_md,
    "created_by": "agent",         # provenance — pinned, distinguishes from user-authored rows
}).execute()
```
Empty result or any DB exception → `ModelRetry` with an actionable Arabic hint
(e.g. empty title → ask for a title and retry). Provenance is always
`created_by='agent'` so agent-saved templates are auditable.

**Deps contract.** The agent's deps must structurally satisfy
`HasUserContext` (Protocol with `.supabase` + `.user_id: str`). The
service-role client BYPASSES RLS, so the `user_id` scoping is the only thing
keeping the row owned by the correct user — the model never supplies it.

**Registered on.**
- `writer_planner` (`agents/writer_planner/agent.py`) — `WriterPlannerDeps`
  exposes both `.supabase` and `.user_id`. The `register_add_user_template(agent)`
  call sits right after `register_tools(agent)` in
  `create_writer_planner_decider`.
- **NOT registered on `writer`** — `WriterDeps`
  (`agents/writer/deps.py`) exposes `.supabase` but has **no `user_id`**
  field, and the writer agent is a single-shot LLM call with no tools by
  design. Registering there would raise `AttributeError` on
  `ctx.deps.user_id` at run time, so it was deliberately skipped. To enable it
  later, add a `user_id: str` field to `WriterDeps` first.

---

## 11. `unfold_workspace_item` tool (BUILT)

A third reusable tool, implemented in
`agents/tool_repository/unfold_workspace_item.py`. It **replaces the old
`read_workspace_item`** tool (router) and adds the same read capability to the
**deep_search planner decider** and the **writer_planner** — the three
WI-consuming agents.

**The problem it fixes.** `read_workspace_item` returned only `content_md`. A
deep_search artifact's body cites its sources as `[n]` markers, but the names
behind those numbers (the actual regulations / rulings / services) lived only
in `workspace_item_references` + the source tables — invisible to the agent
reading the item. So when a user pointed at a *specific named regulation* that
appeared inside a prior search (e.g. «نظام اشتراطات المطاعم»), the router/planner
couldn't connect the name to the cited source and kept re-running generic
searches (diagnosed in convo `6b0c5915`).

**What it returns.** The item's `content_md`, followed by a used-only,
`[n]`-keyed manifest whose numbers match the `[n]` markers in the body:

```
[1] {regulation clean_title} — {chunk title}     (regulations)
[2] [{case_number}] {case summary}                (cases)
[3] {service_name_ar}                             (compliance)
```

so the agent can map any `[n]` in the body to the exact named source.

**Design (per the user's spec).**
- **Deterministic, no LLM** — distinct from the `item_analyzer` (the LLM
  full/partial/none distiller). This is a plain read+manifest primitive.
- **Used-only** — only references with `workspace_item_references.used = true`
  appear; unused ones are omitted entirely. This also bounds the size.
- **`n`-keyed, not deduped** — one line per cited `n` (the citation index),
  ordered ascending across all domains.
- **Lean resolver** — reads `workspace_item_references` (used rows), then
  batched per-domain title/summary joins. It does NOT call the heavy
  `references_service.fetch_item_references` (which also builds `source_view`,
  snippets, cross-refs — overkill for a title manifest). Mirrors how the
  item_analyzer's callers unfold refs, but title-only.

**Data sources (live-verified columns).**
- regulations: `workspace_item_references.item_id` → `chunks_v2.id`
  (`reg:<uuid>` ref_id fallback for legacy NULL item_id) → `chunks_v2.title`
  + `chunks_v2.regulation_id` → `regulations_v2.clean_title` (fallback `title`).
- cases: `item_id` → `cases.id` → `cases.case_number` + `cases.summary`.
- compliance: `item_id` → `services.id` → `services.service_name_ar`.

Unresolvable sources degrade to a `(مصدر غير متوفر)` stub line so an `[n]` is
never silently dropped. A missing / out-of-scope item returns `""` — the same
silent-skip contract as the old tool.

**Pure surface (unit-testable without a DB):** `render_unfold_md(content_md,
lines)`, `SourceLine`, `resolve_used_sources(supabase, wi_id)`,
`unfold_item(supabase, item_id, user_id)`, `_resolve_wi_alias(...)`.

**Registration.**
```python
from agents.tool_repository.unfold_workspace_item import register_unfold_workspace_item
register_unfold_workspace_item(agent)   # deps: .supabase + .user_id + .wi_alias_map
```
Registered on `router_agent` (replacing `read_workspace_item`),
`create_planner_decider` (re-introduces a read tool, richer), and
`create_writer_planner_decider` (alongside `analyze_items`). All three deps
classes (`RouterDeps`, `PlannerDeps`, `WriterPlannerDeps`) structurally satisfy
`HasWorkspaceContext`. The planner runner's `_count_workspace_reads` counts the
new tool name (and the legacy one, for historical events).

**Deps contract.** `HasWorkspaceContext` (Protocol): `.supabase` +
`.user_id: str` + `.wi_alias_map: dict[int, str]`. The service-role client
bypasses RLS, so `.eq("user_id", user_id)` on the item fetch is load-bearing
scope enforcement.

---

## 12. Sources

- Claude Code `Edit` tool reference — https://code.claude.com/docs/en/tools-reference
- Claude Code Edit silent-fail bug — https://github.com/anthropics/claude-code/issues/52241
- Aider edit formats — https://aider.chat/docs/more/edit-formats.html
- Aider unified diffs (3× improvement over SEARCH/REPLACE) — https://aider.chat/docs/unified-diffs.html
- Aider Search/Replace logic deep dive — https://deepwiki.com/Aider-AI/aider/3.2-prompt-engineering-and-templates
- Sumit Gouthaman: hidden sophistication behind LLM file editing — https://sumitgouthaman.com/posts/file-editing-for-llms/
- Fabian Hertwig: Code Surgery — https://fabianhertwig.com/blog/coding-assistants-file-edits/
- CODESTRUCT paper (AST action spaces) — https://arxiv.org/pdf/2604.05407
- markdown-it-anchor — https://www.npmjs.com/package/markdown-it-anchor
- markdown-it-py architecture — https://markdown-it-py.readthedocs.io/en/latest/architecture.html
