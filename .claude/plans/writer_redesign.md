# Plan — Writer (Executor) Redesign

> **TL;DR.** The writer executor at `agents/writer/` currently concatenates the
> entire `WriterPackage` (plan + templates + sources + references + prior_draft
> + preferences) into a giant XML user message via
> `build_writer_user_message_from_package`. This redesign moves all package
> content to `WriterDeps` and surfaces it through `@agent.instructions`
> dynamic-prompt callables (the Pydantic AI deps/ctx mechanism). The user
> message shrinks to a one-paragraph distilled intent (`package.intent_ar`)
> plus a short directive. The structure mirrors the patterns already
> established by `agents/memory/item_analyzer/` (per-caller prompt registry,
> pure-function runner, deps-only orchestration data, LLM sees nothing it
> doesn't need). The contract with the planner — the `WriterPackage` shape —
> does NOT change; only the executor's internal rendering changes.

---

## Goal

Make the writing executor a thin, write-only agent that:

1. Receives a **fully-resolved** `WriterPackage` from the planner.
2. Exposes every content slice of that package to the LLM via **deps / ctx**,
   not by stuffing it into the user message.
3. Sends the LLM a **minimal user message** — the distilled intent plus a
   one-line directive («اكتب المسوّدة الكاملة وفق ما سبق»).
4. Makes **zero routing decisions** — no item selection, no distill choice, no
   template fetch, no reference walking, no subtype guessing.
5. Returns a `WriterLLMOutput`. Publication remains the planner runner's job
   (the existing legacy `_handle_legacy_turn` path keeps its inline publish).

The end state is an executor that looks structurally like `item_analyzer`:
**a runner + a deps factory + an agent factory + dynamic-instruction
callables that read deps**. Nothing else.

---

## Current state analysis

### Files in scope

| File | Lines | What it does today |
|---|---|---|
| `agents/writer/runner.py` | 294 | `handle_writer_turn` dispatches by `isinstance` on `WriterInput \| WriterPackage`. Package path calls `_populate_deps_from_package` + `build_writer_user_message_from_package` + `_run_writer` and returns `WriterLLMOutput` (no publish). Legacy path also publishes. |
| `agents/writer/prompts.py` | 428 | Holds `WRITER_PROMPTS` (six subtype-keyed Arabic system prompts), `build_writer_user_message` (legacy), and `build_writer_user_message_from_package` (the XML monster — ~190 LOC of rendering). |
| `agents/writer/agent.py` | 98 | `create_writer_agent(deps, subtype, model_name)` → builds `Agent[WriterDeps, WriterLLMOutput]` with static `instructions=get_writer_prompt(subtype)` plus one `@agent.system_prompt` callable `inject_workspace_context` that renders `attached_items + describe_query + revising_item_id + detail_level + tone` via `format_writer_context`. |
| `agents/writer/deps.py` | 151 | `WriterDeps` dataclass: `supabase`, `http_client`, `model_registry`, `logger`, `primary_model`, `fallback_model`, `temperature`, lock TTL fields, `emit_sse`, plus per-turn ctx fields `describe_query`, `task_label`, `attached_items`, `revising_item_id`, `detail_level`, `tone`. Does **not** hold the `WriterPackage`. |
| `agents/writer/models.py` | 420 | `WriterSubtype` (Literal), `WriterSection`, `WriterLLMOutput`, `WorkspaceContextBlock`, `WriterInput` (legacy flat shape), `WriterOutput`, plus the planner-contract types: `AnalyzedItem`, `TemplateRef`, `WriterStyle`, `WriterPackage` (with `user_templates() / sources() / references() / prior_draft()` views). |
| `agents/writer/context.py` | 121 | `format_writer_context` — the pure-function renderer used by `inject_workspace_context`. Today only renders `attached_items + describe_query + style prefs` — i.e. the legacy ctx, not the package. |
| `agents/writer/publisher.py` | 266 | `publish_writer_result` — DB insert + lock + SSE. Used by legacy path and by the writer_planner runner. **Stays unchanged.** |

### What the LLM sees today (package path)

| Channel | Contents |
|---|---|
| Static system `instructions` | `WRITER_PROMPTS[subtype]` — the role + subtype body + JSON output contract (Arabic). Same for every turn of that subtype. |
| Dynamic `@agent.system_prompt` (`inject_workspace_context`) | Arabic block with `### الطلب` (describe_query), `### العناصر المرفقة للسياق` listing the **router-selected `attached_items`** (NOT the planner's `analyzed_items`!), and `### تفضيلات الأسلوب`. This block is **mostly redundant** in the package path because the user message already carries the same data in a structured form. |
| User message | The full `build_writer_user_message_from_package` XML: `<plan>` + `<templates>` (user + system) + `<sources>` + `<references>` + `<prior_draft>` + `<user_request>` (= intent_ar) + `<preferences>` + the directive. ~ 5–80 KB depending on `need='full'` vs `'partial'`. |

### The double-rendering problem

`_populate_deps_from_package` explicitly comments:

> «`deps.attached_items` is intentionally NOT populated from
> `package.analyzed_items` — the new XML rendering already renders them.
> Populating both would double-render and confuse the model.»

So today the package path has a **dead `inject_workspace_context` block**: it
still renders `attached_items` from deps (left as whatever the runner set, or
empty), but the actual package content lives in the user message. The
executor effectively bypasses its own dynamic-prompt infrastructure.

This redesign **fixes** that by making `inject_workspace_context` (or its
successor) the **primary** channel for package content, and reducing the user
message to one paragraph.

---

## Item_analyzer pattern study

Studied: `agents/memory/item_analyzer/{__init__,runner,deps,agent,prompt_registry,models}.py`
+ `writer/prompts/{refs_kinds,meta_kinds}.py` + `.claude/plans/item_analyzer_v2.md`.

The patterns that should transfer to the writer executor:

### 1. Pure-function `analyze(call, deps) -> output` runner

```python
async def analyze(call: AnalyzerCall, deps: AnalyzerDeps) -> AnalyzeOutput:
    ...
```

No side effects, no publish, no DB writes beyond the cost-tracking row. The
runner is a thin orchestration layer: load → partition → fan out to LLM →
merge. **The writer's runner should look the same after this redesign** — no
more isinstance branching, no more "if package then build XML else build other
XML."

### 2. `caller_id` on deps, never on the call

```python
@dataclass
class AnalyzerDeps:
    supabase: Any
    http_client: Any
    user_id: str
    conversation_id: str
    caller_id: CallerId      # Literal["router", "writer_planner", ...]
    logger: Any | None = None
```

The LLM never sees `caller_id`. The runner uses it at agent-build time to
pick the right system prompt via `prompt_registry`. The writer executor's
equivalent is **subtype** (which already lives on the package, not on deps,
because the LLM needs to be told what kind of document to draft — but the
infrastructure pattern of "deps drives prompt selection" is the same).

### 3. Per-caller prompt sub-package

```
agents/memory/item_analyzer/
    writer/
        prompts/
            refs_kinds.py       # ANALYZE_REFS_FOR_WRITER_SYSTEM_AR + render_refs_user_msg
            meta_kinds.py       # ANALYZE_META_FOR_WRITER_SYSTEM_AR + render_meta_user_msg
    prompt_registry.py          # dict[CallerId, str] + render_*_user_msg dispatchers
```

The analyzer has ONE caller today (writer_planner) and is already split by
caller. The writer executor has ONE caller today (writer_planner — the
legacy `WriterInput` path is the only other entry point and is on its way
out). So the writer does NOT need per-caller prompt dirs — it has one
caller, and the **subtype** dimension is the real fan-out axis, which is
already cleanly handled by `WRITER_PROMPTS[subtype]`. **No prompt-registry
file is needed.**

### 4. Single source of truth `_build_*` for agent config

```python
def _build_analyzer(instructions: str, output_type: Any) -> Agent[None, Any]:
    return Agent(
        get_agent_model("item_analyzer"),
        output_type=output_type,
        instructions=instructions,
        retries=1,
    )
```

Public factories (`create_refs_analyzer`, `create_meta_analyzer`) funnel
through this. The writer already has `create_writer_agent` — but the
instruction-stacking will get richer in this redesign, and the factory
should stay the single seam where instructions get wired.

### 5. User message rendered by a pure function

`prompt_registry.render_refs_user_msg(*, caller_id, query, wis) -> str` —
no DB access, no I/O. The same shape (a pure render fn) should hold for the
writer's user-message builder (which after this redesign renders only the
intent + directive, but the function-shape rule still applies).

### 6. LLM-blind orchestration data

`AnalyzerDeps.user_id` and `AnalyzerDeps.conversation_id` are NEVER passed to
the LLM. They drive Supabase RLS scoping inside `_load_workspace_items` and
populate the cost-tracking `agent_runs` row. The writer should follow the
same rule: anything on `WriterDeps` that doesn't appear inside a
`@agent.instructions` callable's return string is invisible to the model.

### 7. No tools when none are needed

`item_analyzer` exposes zero tools. The agent gets one shot at the input and
returns a structured verdict. The writer is the same shape — one shot, one
structured output. **No tools.** This redesign reaffirms that.

---

## New design

### Deps shape

**Recommendation: carry the full `WriterPackage` on `WriterDeps`** as a single
optional field, plus keep the existing per-turn metadata fields.

```python
@dataclass
class WriterDeps:
    # --- I/O surface (unchanged) ------------------------------------------
    supabase: Any = None
    http_client: Any = None
    model_registry: Any = None
    logger: logging.Logger = field(...)

    # --- Tier / fallback labeling (unchanged) -----------------------------
    primary_model: str = PRIMARY_MODEL_DEFAULT
    fallback_model: str = FALLBACK_MODEL_DEFAULT
    temperature: float = TEMPERATURE_DEFAULT
    lock_ttl_seconds: int = LOCK_TTL_SECONDS_DEFAULT
    lock_heartbeat_seconds: int = LOCK_HEARTBEAT_SECONDS_DEFAULT
    emit_sse: Optional[Callable[[dict], None]] = None

    # --- Per-turn metadata the LLM sees via dynamic instructions ----------
    describe_query: str = ""
    task_label: str = ""
    attached_items: list[WorkspaceItemSnapshot] = field(default_factory=list)
    revising_item_id: Optional[str] = None
    detail_level: str = "standard"
    tone: str = "neutral"

    # --- NEW: the package the planner handed us ---------------------------
    # When set, the dynamic-instruction callables read from here. When None,
    # the agent falls back to the legacy attached_items path (or the legacy
    # path is dead — see § Legacy path handling).
    package: Optional["WriterPackage"] = None

    _events: list[dict] = field(default_factory=list)
```

**Trade-off considered.** The alternative is to "flatten" the package into a
render-ready struct (e.g. pre-rendered XML strings on deps). Rejected
because:

- It duplicates logic that already lives on `WriterPackage` (`user_templates()`,
  `sources()`, `references()`, `prior_draft()`).
- It makes deps harder to construct in tests — every test would have to
  manually pre-render the XML strings.
- It loses the property that **the planner contract is `WriterPackage`** —
  if deps carries the same object, the seam between planner and executor is
  one assignment.

The package-carrying design is also closer to the analyzer pattern, where
deps carries small inputs (`user_id`, `conversation_id`, `caller_id`) and the
runner loads the heavy data inside. Here the planner has already loaded
everything; deps just carries the result by reference.

### Dynamic instructions

**Recommendation: TWO `@agent.instructions` callables, both reading from
`ctx.deps.package`. Use `@agent.instructions` not `@agent.system_prompt`** —
per Pydantic AI's intent, `instructions` is the modern decorator and is
re-evaluated on every run (which matters for resume / retry cases).

```python
@agent.instructions
async def package_content_block(ctx: RunContext[WriterDeps]) -> str:
    """Renders the package content (plan + templates + sources + references +
    prior_draft + preferences) as an Arabic system block."""
    pkg = ctx.deps.package
    if pkg is None:
        return ""    # legacy path or unit test with no package
    return render_package_for_system_prompt(pkg)

@agent.instructions
async def workspace_envelope_block(ctx: RunContext[WriterDeps]) -> str:
    """Renders the per-turn envelope (describe_query, task_label,
    revision target). Used to give the executor a human-frame on top of the
    structured package content."""
    return format_writer_envelope(
        describe_query=ctx.deps.describe_query,
        task_label=ctx.deps.task_label,
        revising_item_id=ctx.deps.revising_item_id,
        detail_level=ctx.deps.detail_level,
        tone=ctx.deps.tone,
    )
```

**Why split into two.**

- `package_content_block` is the **bulk** (XML-shaped, potentially tens of KB)
  and only fires when `deps.package` is set. Pure render of structured data.
- `workspace_envelope_block` is the **frame** — small, always renders, gives
  the model the human-readable "this is what the user actually asked for" view
  on top of the structured content. Equivalent to today's
  `format_writer_context`, minus `attached_items` (the package's
  `analyzed_items` supersedes the router's `attached_items` in the package
  path).

**Why not one big callable.** Independent testability. The package-rendering
function (the meat) can be unit-tested without touching the envelope. The
envelope (small, mostly string formatting) can be regression-tested on its
own.

**Why not three / four (one per block).** Pydantic AI concatenates
instruction-callable outputs in the order they're registered. Splitting into
`<plan>` / `<templates>` / `<sources>` / `<references>` / `<prior_draft>` /
`<preferences>` would multiply callable count without adding clarity. The
**inner** rendering function `render_package_for_system_prompt` is naturally
split into one private helper per block (same shape as today's
`build_writer_user_message_from_package`), but they're called from a single
`@agent.instructions` shell.

**Rendering format.** Keep the existing XML shape from
`build_writer_user_message_from_package` verbatim — same tags, same attrs,
same `<facts>` / `<refs>` family-specific sub-blocks. The ONLY differences:

1. The block lives in the **system prompt** instead of the user message.
2. Wrap the whole thing in a top-level `<package>...</package>` tag so the
   model can mentally separate "package content (system)" from "user request
   (user message)" at a glance.
3. Move `<user_request>` and the `«اكتب ...»` directive OUT of the rendered
   block — they belong to the user message now.

### User message

```python
def build_writer_user_message_minimal(package: WriterPackage) -> str:
    return (
        f"<user_request>\n{_esc(package.intent_ar.strip())}\n</user_request>\n"
        f"\n"
        f"اكتب المسوّدة الكاملة وفق ما ورد في <package> أعلاه."
    )
```

That's the entire user message. Nothing else. Length: a few hundred bytes at
most. The directive references `<package>` (the system-prompt-rendered block)
so the model knows where to look.

For the legacy path (see § Legacy path handling) the user message keeps its
existing shape — only the package path shrinks.

### Tools (or no tools)

**No tools.** Justification:

- The executor receives a **fully resolved** package: every `body_md` is
  literal text, every `resolved_refs_md` is pre-rendered, every system
  template is already loaded. There is nothing to fetch.
- The agent is one-shot — no clarification loop, no plan revision, no
  search. The planner handled all of that.
- Adding tools would invite the LLM to make routing decisions, which the
  redesign explicitly forbids (it's write-only).
- `item_analyzer` exposes zero tools for the same reasons.

If a future feature requires conditional content (e.g. "fetch a section of a
WI on demand"), the right place for that is the **planner**, not the
executor. The planner already has tools (`analyze_items`, `search_templates`)
and can re-resolve the package before re-running the executor.

### Output type

**Keep `WriterLLMOutput` exactly as it is.** The fields (`title_ar`,
`sections`, `citations_used`, `confidence`, `notes_ar`, `chat_summary`,
`key_findings`) are downstream contract — the publisher and the chat
summary surface both depend on them.

**On the user's «write an initial plan» phrasing.** Re-reading the verbatim
brief:

> "Inspire from the item_analyzer and **write an initial plan**. The
> executor's job is simple — write based on instruction, ctx, template,
> and query, plan — all provided by the planner."

The «write an initial plan» refers to **me writing this redesign doc** — the
clause that follows ("The executor's job is simple…") describes what the
executor does, and "plan" appears in that list as one of the **inputs the
planner provides** (= `package.plan_md`, which already exists). It is NOT
asking for a new `draft_plan: str` field on `WriterLLMOutput`. The executor
should not be a "planning" agent in any new sense — it consumes the
planner's plan.

Decision: **no changes to `WriterLLMOutput`.**

### System prompts

**Keep `WRITER_PROMPTS` (the six subtype-keyed Arabic prompts) as the static
`instructions` argument** to `create_writer_agent`. They:

- Encode the **subtype contract** (contract vs memo vs letter vs …), which
  is orthogonal to package content.
- Carry the **JSON output schema teaching** (the `_OUTPUT_CONTRACT_AR`
  trailer), which is invariant per subtype.
- Already work — no Wave-9 issue has surfaced against them.

**Minor edit needed.** The shared role `_SHARED_ROLE_AR` references the
old layout ("«…الفقرة الأولى من الرسالة» / «قسم <research>» / «قسم
<workspace_context>»"). After this redesign the LLM sees:

- The user request inside `<user_request>` in the **user message**.
- All other content inside `<package>` in the **system prompt**.

The `_SHARED_ROLE_AR` paragraph #2 «بحث قانوني مرفق (إن وُجد) في قسم
<research>» and paragraph #3 «ملاحظات وملفات سياق المستخدم في قسم
<workspace_context>» need to be rewritten to point at the new layout
(`<package>` → `<plan>`, `<templates>`, `<sources>`, `<references>`,
`<prior_draft>`). One-paragraph rewrite, no structural change.

**Per-caller prompt subdirs (à la item_analyzer): rejected.** The writer
has one caller (the planner) and one fan-out axis (subtype) that's already
clean. Splitting by caller would add ceremony without clarity.

### Runner changes

The new `handle_writer_turn` shrinks dramatically. Sketch:

```python
async def handle_writer_turn(
    input: WriterInput | WriterPackage,
    deps: WriterDeps,
) -> WriterOutput | WriterLLMOutput:
    if isinstance(input, WriterPackage):
        return await _handle_package_turn(input, deps)
    return await _handle_legacy_turn(input, deps)


async def _handle_package_turn(
    package: WriterPackage, deps: WriterDeps,
) -> WriterLLMOutput:
    # 1. Stash the package on deps so dynamic instructions can read it.
    deps.package = package

    # 2. Populate the envelope fields from the package (subset of today —
    #    no longer touches attached_items or sets revising_item_id from
    #    package.prior_draft() because that flows via deps.package now).
    if not deps.describe_query:
        deps.describe_query = package.intent_ar or ""
    if deps.detail_level == "standard":
        deps.detail_level = package.style.detail_level
    if deps.tone == "neutral":
        deps.tone = package.style.tone

    # 3. Build the minimal user message.
    user_message = build_writer_user_message_minimal(package)

    # 4. Run LLM with fallback (unchanged helper).
    llm_output, model_used = await _run_writer(
        package.subtype, user_message, deps,
    )
    deps.primary_model = model_used  # type: ignore[assignment]
    return llm_output
```

**What gets removed from `_populate_deps_from_package`:**

- The «`deps.attached_items` intentionally NOT populated» comment +
  surrounding logic — `attached_items` is now stale-from-router context and
  the package supersedes it in the system prompt.
- The `revising_item_id` derivation from `package.prior_draft()` — this is
  still useful for the **publisher** (the legacy path uses it for soft-
  delete), but in the package path the planner runner already passes
  `revising_item_id` when it builds `exec_deps` (see
  `agents/writer_planner/runner.py:442`), so the executor doesn't need to
  re-derive it. Keep it ONLY as a defensive fallback.

**What stays:** `describe_query`, `detail_level`, `tone` defaulting from
package. These shape the envelope block.

### Legacy path handling

The `WriterInput` legacy path goes through `_handle_legacy_turn` and uses
`build_writer_user_message`. Three options:

| Option | Cost | Risk | Verdict |
|---|---|---|---|
| **A. Leave legacy as-is** — keep `build_writer_user_message`, keep current `inject_workspace_context` for the legacy ctx. | Zero implementation work. Legacy callers (legacy tests, ad-hoc smoke runs) keep working. | Two code paths to maintain. `attached_items` rendering happens in **system prompt** for legacy, but **package content** is in system prompt for package — symmetrical-but-different. | **RECOMMENDED.** |
| **B. Migrate legacy to deps + dynamic instructions** — kill `build_writer_user_message`, render `research_items` + `workspace_context` from deps. | Medium work. Touches `_handle_legacy_turn`, `format_writer_context` (extend to render `research_items`), and the legacy tests. | The legacy path is on its way out (planner is the modern entry). Investing in it is wasted motion. | Rejected. |
| **C. Deprecate legacy entirely** — drop `_handle_legacy_turn`, remove `WriterInput` as a valid `handle_writer_turn` input, force all callers to build a `WriterPackage`. | Large blast radius — `from_package` adapter would still need to live for the publisher, but the runner stops dispatching. Tests that drive the legacy shape directly (test_runner.py) break. | The legacy path is still wired into `agent_writer` / direct-router paths in some wave plans. Removing it before those are confirmed dead is premature. | Rejected for this redesign. Revisit after Wave 9 cleanup. |

**Recommendation: Option A.** The legacy path stays as-is. The new
`@agent.instructions` callables guard on `deps.package is None` and return
empty strings; the existing `inject_workspace_context` keeps firing for the
legacy ctx (or we rename it to `workspace_envelope_block` and have it skip
the attached_items section when `deps.package` is set — slightly cleaner,
mentioned in Open Questions).

---

## File manifest

| File | Status | Change |
|---|---|---|
| `agents/writer/deps.py` | MODIFIED | Add `package: Optional[WriterPackage] = None` field. Add `package=None` kwarg to `build_writer_deps`. |
| `agents/writer/agent.py` | MODIFIED | Replace single `@agent.system_prompt inject_workspace_context` with two `@agent.instructions`: `package_content_block` (renders `deps.package` via new helper) + `workspace_envelope_block` (renames `inject_workspace_context`, drops attached_items rendering when `deps.package` is set). |
| `agents/writer/context.py` | MODIFIED | Add `format_writer_envelope` (the legacy `format_writer_context` minus the attached_items section, used by the envelope-block callable). Keep `format_writer_context` for the legacy path. |
| `agents/writer/prompts.py` | MODIFIED | (a) Add `render_package_for_system_prompt(package) -> str` — the new pure renderer (extracted from `build_writer_user_message_from_package` with `<user_request>` + directive stripped out, wrapped in `<package>...</package>`). (b) Add `build_writer_user_message_minimal(package) -> str` — the new 3-line user message. (c) `build_writer_user_message_from_package` becomes a thin alias kept ONLY for backward-compat with any external import; deprecated. (d) Update `_SHARED_ROLE_AR` paragraphs 2-3 to reference the new `<package>` layout. |
| `agents/writer/runner.py` | MODIFIED | `_handle_package_turn`: stash `deps.package = package`, shrink `_populate_deps_from_package` (drop `attached_items` non-handling, drop `revising_item_id` derivation as primary path), call `build_writer_user_message_minimal` instead of `build_writer_user_message_from_package`. Legacy path unchanged. |
| `agents/writer/tests/test_runner.py` | MODIFIED | Add tests for the new minimal user message (`build_writer_user_message_minimal`). Add test asserting no `<source>` / `<template>` / `<reference>` tags appear in the user message on the package path. Existing legacy tests stay green. |
| `agents/writer/tests/test_package_rendering.py` | NEW | Unit tests for `render_package_for_system_prompt`: empty package, full-only refs, partial refs with `<refs>`, partial meta with `<facts>`, mixed templates + prior_draft. Verifies the system-prompt-side rendering matches the previous user-message-side rendering one-for-one (regression net). |
| `agents/writer/tests/test_agent_instructions.py` | NEW | Tests that `package_content_block(ctx)` returns the rendered package when `ctx.deps.package` is set, and an empty string when it's None. Tests that `workspace_envelope_block(ctx)` returns the envelope and skips attached_items when `ctx.deps.package is not None`. |
| `agents/writer/publisher.py` | UNCHANGED | — |
| `agents/writer/models.py` | UNCHANGED | `WriterPackage`, `AnalyzedItem`, `WriterLLMOutput`, etc. all stay the same. The planner contract is preserved. |
| `agents/writer/lock.py` | UNCHANGED | — |
| `agents/writer/__init__.py` | MODIFIED (minor) | Export `build_writer_user_message_minimal` alongside the existing exports. Optionally re-export `render_package_for_system_prompt` if any external code wants to inspect the rendering. |
| `agents/writer_planner/runner.py` | UNCHANGED | The planner runner's call site (`handle_writer_turn(package, exec_deps)` followed by `publish_writer_result(...)`) stays exactly the same. The redesign is transparent to the planner. |

---

## Build order

1. **`models.py` — no change**, but re-read the `WriterPackage` view methods
   (`sources()`, `references()`, `prior_draft()`, `user_templates()`) to
   confirm the new renderer can rely on them. Sanity check.
2. **`deps.py`**: add `package: Optional[WriterPackage] = None`. Update
   `build_writer_deps` signature. Run existing unit tests to confirm no
   regression.
3. **`prompts.py`**:
   a. Extract `render_package_for_system_prompt(package)` from
      `build_writer_user_message_from_package`, stripping the
      `<user_request>` + directive trailer. Wrap the body in
      `<package>...</package>`.
   b. Add `build_writer_user_message_minimal(package)`.
   c. Update `_SHARED_ROLE_AR` paragraphs 2–3.
   d. Keep `build_writer_user_message_from_package` as a deprecated alias
      that just calls the new minimal builder + concatenates the package
      content (so any straggler caller still gets the merged shape).
4. **`context.py`**: extract `format_writer_envelope` from
   `format_writer_context` (drop the attached_items section).
5. **`agent.py`**: register two `@agent.instructions` callables in
   `create_writer_agent`. Wire them to the new helpers.
6. **`runner.py`**: tighten `_handle_package_turn` per § Runner changes.
   Confirm the legacy path is untouched.
7. **Tests**:
   a. Add `tests/test_package_rendering.py`.
   b. Add `tests/test_agent_instructions.py`.
   c. Update `tests/test_runner.py` with the "no package XML in user message"
      assertion.
   d. Run the existing writer + writer_planner test suites to ensure no
      regression on either side.
8. **Sanity smoke**: Run one end-to-end writer_planner turn locally (or via
   `test-search` skill if it covers the writer path) to confirm the system
   prompt grows by the package size and the user message stays small.
9. **Docs**: brief note in `CLAUDE.md` § "Known Issues" or wave 9 progress
   that the executor now reads ctx-driven content (one line).

---

## Tests

| Test file | Test case | Purpose |
|---|---|---|
| `test_runner.py` | `test_package_path_user_message_is_minimal` (NEW) | Assert that on a package turn the user message string contains `intent_ar` and the directive but **no `<source>`, `<template>`, `<reference>`, `<prior_draft>`, or `<plan>` substrings**. |
| `test_runner.py` | `test_package_path_passes_package_via_deps` (NEW) | Monkey-patch `create_writer_agent` to capture the deps at run time; assert `captured_deps.package is the_input_package`. |
| `test_runner.py` | `test_legacy_path_unchanged` (existing, kept green) | Confirms `_handle_legacy_turn` still produces the legacy XML + publishes. |
| `test_package_rendering.py` (NEW) | `test_render_empty_package` | Empty `analyzed_items`, empty `system_templates`, no `prior_draft` → result still wraps in `<package>` but inner blocks are absent. |
| `test_package_rendering.py` (NEW) | `test_render_full_refs_only` | One `agent_search` item with `need='full'` → `<sources><source kind="agent_search" item_id="..." need="full">body</source></sources>`. |
| `test_package_rendering.py` (NEW) | `test_render_partial_refs_with_refs_block` | One `agent_search` with `need='partial'` + `resolved_refs_md='[1] …'` → `<source ...><body>… <refs>…</refs></source>`. |
| `test_package_rendering.py` (NEW) | `test_render_partial_meta_with_facts_block` | One `attachment` with `need='partial'` + `extracted_metadata={'parties':'…'}` → `<source ...><facts>parties: …</facts>body</source>`. |
| `test_package_rendering.py` (NEW) | `test_render_mixed_templates_prior_draft` | User template + system template + prior_draft + sources → all blocks present in correct order: `<plan>` → `<templates>` → `<sources>` → `<references>` → `<prior_draft>` → `<preferences>`. |
| `test_package_rendering.py` (NEW) | `test_render_parity_with_legacy_builder` | Same package fed to `render_package_for_system_prompt` + minimal user message; concatenation of the two contains every byte the legacy `build_writer_user_message_from_package` produced (minus formatting whitespace). Regression net so we don't lose any block accidentally. |
| `test_agent_instructions.py` (NEW) | `test_package_content_block_renders_when_package_set` | Build a `WriterDeps` with `package=<minimal package>`, build a `RunContext` shim, call the callable; assert it contains `<package>` and the rendered inner blocks. |
| `test_agent_instructions.py` (NEW) | `test_package_content_block_empty_when_no_package` | Same but `deps.package=None`; callable returns `""`. |
| `test_agent_instructions.py` (NEW) | `test_envelope_block_skips_attached_items_when_package_set` | `deps.package=<...>` + `deps.attached_items=[...]`; envelope output does NOT contain `### العناصر المرفقة للسياق`. |
| `test_agent_instructions.py` (NEW) | `test_envelope_block_includes_attached_items_in_legacy_mode` | `deps.package=None` + `deps.attached_items=[...]`; envelope output DOES contain the attached_items block. |
| `test_publisher.py` | (existing, untouched) | Publisher is unchanged. |
| `test_lock.py` | (existing, untouched) | Lock is unchanged. |
| `agents/writer_planner/tests/*` | All existing tests | Should pass without modification — the planner contract is unchanged. |

---

## Anti-patterns to avoid

- **Do NOT pass the package both via deps AND via the user message.** Pick
  one channel (deps). Double-rendering wastes tokens, kills caching, and
  encourages the model to second-guess which copy is authoritative.
- **Do NOT add tools to the executor.** It is write-only. Any "fetch more
  content" need belongs to the planner, which can re-resolve the package
  and re-invoke the executor.
- **Do NOT let the executor decide which items to include.** The planner's
  `analyzed_items` is the final list. The executor never filters, never
  re-ranks, never drops.
- **Do NOT let the executor decide subtype.** The planner sets
  `package.subtype`; the executor reads it to pick the system prompt and
  passes it through unchanged.
- **Do NOT route fetched refs through the executor.** The planner already
  called `references_service.fetch_item_references` and rendered the result
  into `analyzed_item.resolved_refs_md`. The executor just embeds that
  string.
- **Do NOT re-derive `revising_item_id` from `package.prior_draft()` in the
  package path's primary flow.** The planner runner sets it explicitly when
  it builds `exec_deps`. Keep a defensive fallback at most, and document it.
- **Do NOT touch `WriterPackage`, `AnalyzedItem`, `WriterLLMOutput`, or the
  `kind` enum.** Those are cross-agent contracts. This redesign is local to
  the executor's internal rendering.
- **Do NOT add `caller_id` to `WriterDeps`.** The writer has one caller and
  no prompt fan-out by caller. Adding it would mirror the analyzer's shape
  without earning the cost.
- **Do NOT confuse the WI-{seq} protocol with the executor.** Per
  `.claude/plans/agent_communication_protocol.md`: *"It does NOT… Touch
  `agents/writer/` — the executor side speaks UUIDs end-to-end."* The
  executor receives `AnalyzedItem.item_id` as resolved UUIDs and renders
  them verbatim in `item_id="..."` attributes. Aliases never reach the
  executor.

---

## Open questions

1. **Should `format_writer_context` stay or be replaced?** Option: rename it
   to `format_writer_envelope_legacy`, have `format_writer_envelope` be the
   new package-aware version (skips attached_items when `package is not
   None`), and let `agent.py` call only the latter. The legacy callable then
   has one job (legacy ctx) and the new callable has one job (package
   envelope). Recommend: yes, do this cleanup in step 4 of the build order.
   But it's a naming detail — defer the final name to the implementer.

2. **Prompt-cache implications of moving content into the system prompt.**
   Pydantic AI / the underlying provider (Alibaba qwen, OpenRouter) caches
   system prompts across calls when the prompt is stable. Moving content
   into the system prompt is a **win for cache hit rate within a single
   conversation's retries** (the planner may re-run the executor on a
   model failure → fallback chain). It's a **wash for first-call cost**
   (system tokens cost the same as user tokens). Worth a brief telemetry
   pass after rollout to confirm. No implementation impact.

3. **Top-level wrapper tag.** Plan recommends wrapping the rendered package
   in `<package>...</package>` so the model can mentally separate the
   system block from the user message. Open question: should the wrapper
   include a one-line preamble («فيما يلي حقيبة الكتابة المُحضَّرة بواسطة
   المخطّط:») to frame what follows? Recommend: yes, one Arabic preamble
   line — costs ~30 tokens, improves grounding. Final wording: implementer's
   call.

4. **What to do with `deps.attached_items` in the package path.** Today the
   runner deliberately leaves it empty so the legacy `inject_workspace_context`
   doesn't double-render. After this redesign, the new envelope-block
   callable already guards on `deps.package is not None`, so
   `attached_items` could safely be populated for telemetry / debugging
   without leaking into the prompt. Recommendation: keep the existing
   "don't populate" behavior — the planner's `attached_items` (carried on
   `WriterPlannerDeps`) is router-scoped context that doesn't add signal
   beyond what's already in `analyzed_items`.

5. **Should we delete `build_writer_user_message_from_package` entirely?**
   The redesign deprecates it but keeps it as a thin alias for any external
   import. Scan the repo for callers — if zero, delete cleanly in a follow-
   up commit. Don't gate the redesign on that scan.

6. **Subtype-specific package rendering.** Out of scope for this redesign,
   but worth noting: a contract draft probably wants templates rendered
   FIRST (template-as-scaffold), while a summary probably wants sources
   first. Today the order is fixed (templates → sources → references →
   prior_draft). If a subtype-specific re-ordering proves useful, the
   per-subtype prompt body can mention "look at <templates> first" without
   changing the rendering order. Defer.

---

## Status

**Plan only — implementation pending user approval.** No code in
`agents/writer/` or its tests is touched by this document. The next step is
the user's review; on approval, implement in the build-order sequence above.
