> **SUPERSEDED** by `.claude/plans/item_analyzer_v2.md` (2026-05-25).
> Derived from the 7-sub-agent design that v2 collapses to 2 packages
> with family fan-out. See v2 § 15 for the change rationale.

# INITIAL — `item_analyzer` Layer 4 Content-Steward Family

> Generated: 2026-05-24. Source plans this enriches:
> - `.claude/plans/item_analyzer.md` (Layer 4 family + 7 sub-agents + edit semantics)
> - `.claude/plans/item_analyzer_request_builder.md` (deterministic dispatcher)
>
> This INITIAL.md is the **single source of truth** for the downstream
> Pydantic-AI builder pipeline (`pydantic-ai-prompt-engineer`,
> `pydantic-ai-tool-integrator`, `pydantic-ai-dependency-manager`,
> `pydantic-ai-validator`, `luna-wiring`). All five must be able to start
> work from this file alone — they should not need to re-read the two
> source plans.
>
> Note: the model selection is project-wide (`get_agent_model` slot
> `item_analyzer` with `tier_override`). **TBD — user will specify**
> nothing additional; tier is inherited from the caller per call.

---

## 1. Goal & scope

### 1.1 What this agent does

A **single Pydantic AI agent family** (`item_analyzer`) at **Layer 4 Memory**
owns *all post-release mutations and focused reads of
`workspace_items.content_md`*. It exposes two modes — `analyze` (silent,
read-only) and `edit` (mutating, user-visible) — and dispatches into seven
sub-agents, one per `(mode, target_kind)` pair where
`kind ∈ {notes, agent_search, agent_writer, attachment}`. Attachments are
immutable, so only six of the eight pairs need sub-agents
(`edit × attachment` is rejected at dispatch).

### 1.2 Why one family, seven sub-agents

The user's design north star is: **"same execution context, different
specialization based on request origin."** All seven sub-agents share the
same:

- Pydantic AI agent class and lifecycle
- Dependency dataclass (`AnalyzerDeps`)
- Logfire span taxonomy and `agent_runs` row shape
- `FallbackModel` chain (resolved via `get_agent_model("item_analyzer", tier_override=…)`)
- Request-builder dispatch contract and validation rules

They differ *only* in:

| Axis | Per-sub-agent? | Owner |
|------|----------------|-------|
| System prompt (Arabic) | Yes — 7 prompts | `prompts/<sub_agent_id>.py` |
| Output schema (`BaseModel`) | Yes — 7 schemas (shared base classes by mode) | `models.py` |
| User-message renderer | Yes — 7 renderers (with shared helpers) | `user_message_renderers/<sub_agent_id>.py` |
| Target WI kind | Yes — fixed by dispatch | `dispatch.py` registry |
| Caller-side `extras` schema | Yes — keyed `(caller_id, mode)` | `dispatch.py::CALLER_EXTRAS_SCHEMA` |

### 1.3 Hard ownership invariant (NON-NEGOTIABLE)

After a `workspace_items` row's initial INSERT, **only
`item_analyzer.edit` mutates `content_md`**. Every other agent is read-only
on post-release WIs. Enforcement is **service-layer + CI grep lint**, not
DB triggers (rationale in §10).

### 1.4 In scope vs out of scope

**In scope (v1):**
- 7 sub-agents (4 analyze + 3 edit).
- Tier inheritance via `get_agent_model("item_analyzer", tier_override=…)`.
- `workspace_item_versions` table (before-image snapshots) + atomic
  `commit_item_revision` service.
- Layer-2 `user_emit` dep injection for `edit` mode (analyze stays silent).
- Group selectors (`conversation` / `turn` / `parent_artifact`) for analyze only.
- N=3 conversation history window, env-overridable.
- Deterministic dispatch (no LLM in the request builder).
- Per-(caller, mode) `extras` discriminated-union schema.
- Per-sub-agent Logfire span attributes and `agent_runs` rows.
- One-shot idempotency key on `commit_item_revision` (see §11.4 enrichment).

**Out of scope (deferred — explicit list, do not invent additions):**
1. Cross-conversation edits (today scoped by `(user_id, conversation_id)`).
2. Structural edits (`kind`, `title`, `parent_item_id`, `summary`, embeddings).
3. Restore-to-version UX (data structure supports it; no API yet).
4. Edit diffs in SSE (current contract is a plain Arabic summary).
5. Bulk edits (one call, one item).
6. Auto-summarization on edit (artifact_summarizer is NOT re-triggered).
7. `writer_planner` / `deep_search_planner` as edit callers. **Edit is router-exclusive by design** — see §4.3 and the caller protocol §2.4 for the rationale. This is NOT a v1 deferral; planners are generative/analytical agents, not mutators. The `EXTRAS_TAGS` dict has no `(writer_planner, edit)` or `(deep_search_planner, edit)` entry, so such a call cannot be constructed.
8. Per-fallback-provider granularity in `agent_runs` (`fallback_used` stays boolean).

---

## 2. Agent classification

- **Type**: Pydantic AI agent **family** with deterministic Python dispatcher in front.
- **Layer** (architectural position): **Layer 4 Memory** — alongside `artifact_summarizer`, `ocr_extractor`.
- **Tier** (model cost class): **Inherited per call** (`tier_1` from planners, `tier_2` from router).
  See CLAUDE.md "Layer vs Tier" — never conflate the two.
- **Complexity**: **Medium-high**. The runner + builder + 7 prompts + DB versioning + lint is bigger than a single-agent feature, but each sub-agent itself is a small focused unit.
- **Domain**: Saudi legal AI content management.

---

## 3. Architectural overview

```
                       Caller (Layer 1 router | Layer 2 planner)
                                       │
                                       ▼
                       AnalyzerCall (typed surface — §4.1)
                                       │
                                       ▼
            ┌──────────────────────────────────────────────────────┐
            │  request_builder.build_request(call, deps)           │
            │  DETERMINISTIC — no LLM. Steps:                      │
            │    1. validate call shape                            │
            │    2. validate caller `extras` against schema        │
            │    3. resolve targets → list[WorkspaceItemRow]       │
            │    4. enforce same-kind invariant                    │
            │    5. post-resolution edit guards                    │
            │    6. pick (mode, kind) → SubAgentSpec               │
            │    7. load history (N=3, env-overridable)            │
            │    8. render system + user prompt                    │
            │    9. assemble ResolvedRequest                       │
            └──────────────────────────────────────────────────────┘
                                       │
                                       ▼
                ResolvedRequest (§4.2) — fully materialized
                                       │
              ┌────────────────────────┴────────────────────────┐
              │                                                 │
              ▼                                                 ▼
   runner.analyze(call, deps)                         runner.edit(call, deps)
   • silent (deps.user_emit IS None)                  • requires deps.user_emit
   • returns AnalyzeResult                            • commits version via
   • short_circuit → empty result                      commit_item_revision
                                                       • emits Arabic «تم …»
                                                         AFTER commit succeeds
                                                       • returns EditResult
```

The arrows are one-way. The runner never goes back to the builder.

### Layer separation rules

- Layer 4 agents **do not normally talk to the user**. The single
  exception is the `edit` mode's Arabic acknowledgment, made possible by
  Layer 2 *injecting* `user_emit` into deps — Layer 4 stays oblivious
  to SSE plumbing.
- Layer 4 **never escalates** to a higher-layer agent. If an edit can't
  be done as a small in-place change, the editor returns `no_change=True`
  with a polite Arabic refusal. Re-routing to `agent_writer` for a full
  rewrite is the **router's** decision before it ever invokes the family.

---

## 4. The "same ctx, different specialization" contract

This is the design heart. Below is the explicit matrix the
`pydantic-ai-prompt-engineer` and `pydantic-ai-tool-integrator` must
respect.

### 4.1 Shared (identical across all 7 sub-agents)

| Surface | Concrete shape |
|---|---|
| Pydantic AI `Agent(...)` factory style | `_build_item_analyzer_agent(instructions, output_type, model_settings)` private helper, mirroring `agents/memory/artifact_summarizer/agent.py::_build_summarizer_agent`. |
| Dependency dataclass | `AnalyzerDeps` (§7) — same for all sub-agents. |
| Model resolution | `get_agent_model("item_analyzer", tier_override=req.tier)` |
| Retries | `retries=1` (one Pydantic AI retry round on output-validation failure). |
| `UsageLimits` | `request_limit=2` (one ModelRetry + one fallback hop); `output_tokens_limit=20_000`. |
| `model_settings` | `{"extra_body": {"enable_thinking": True}}` (reasoning enabled — matches sibling `artifact_summarizer`). |
| Logfire span name | `item_analyzer.analyze` or `item_analyzer.edit` (mode-level, NOT sub-agent-level, so dashboards roll up cleanly). |
| `agent_runs.agent_family` | `'memory'` |
| `agent_runs.subtype` | `<sub_agent_id>` e.g. `"analyze.notes"`, `"edit.writer"` |

### 4.2 Per-sub-agent specialization

| `(mode, kind)` | `sub_agent_id` | System prompt | Output schema base | User-message renderer | Target WI kind |
|---|---|---|---|---|---|
| `analyze, notes` | `analyze.notes` | `ANALYZE_NOTES_SYSTEM_AR` | `AnalyzeOutputBase` → `NotesAnalyzeOutput` | `analyze_notes.render` | `note` |
| `analyze, agent_search` | `analyze.search` | `ANALYZE_SEARCH_SYSTEM_AR` | `AnalyzeOutputBase` → `SearchAnalyzeOutput` | `analyze_search.render` | `agent_search` |
| `analyze, agent_writer` | `analyze.writer` | `ANALYZE_WRITER_SYSTEM_AR` | `AnalyzeOutputBase` → `WriterAnalyzeOutput` | `analyze_writer.render` | `agent_writing` |
| `analyze, attachment` | `analyze.attachment` | `ANALYZE_ATTACHMENT_SYSTEM_AR` | `AnalyzeOutputBase` → `AttachmentAnalyzeOutput` | `analyze_attachment.render` | `attachment` |
| `edit, notes` | `edit.notes` | `EDIT_NOTES_SYSTEM_AR` | `EditOutputBase` → `NotesEditOutput` | `edit_notes.render` | `note` |
| `edit, agent_search` | `edit.search` | `EDIT_SEARCH_SYSTEM_AR` | `EditOutputBase` → `SearchEditOutput` | `edit_search.render` | `agent_search` |
| `edit, agent_writer` | `edit.writer` | `EDIT_WRITER_SYSTEM_AR` | `EditOutputBase` → `WriterEditOutput` | `edit_writer.render` | `agent_writing` |

Two intentional shared base classes (`AnalyzeOutputBase`, `EditOutputBase`) — see §5.2 — so all editors share `new_content_md` / `edit_summary_ar` / `no_change`, and all analyzers share telemetry fields.

### 4.3 Per-(caller, mode) extras specialization

| Caller | Mode | Extras schema (Pydantic) | How the renderer uses it |
|---|---|---|---|
| `router` | `analyze` | `RouterAnalyzeExtras { focus: str }` | Appended as «التركيز:» line in user message. |
| `router` | `edit` | `RouterEditExtras { edit_kind: Literal["factual","tighten","insert","reframe"] }` | Selects a 1-line modifier in the system prompt; persisted to `workspace_item_versions.edit_kind`. |
| `writer_planner` | `analyze` | `WriterPlannerAnalyzeExtras { role_hint: Literal["template","source","reference","prior_draft"]; query: str }` | `role_hint` tunes prompt (`source` → "extract verbatim"); `query` becomes the focus line. |
| `deep_search_planner` | `analyze` | `DeepSearchPlannerAnalyzeExtras { angle: str; carry_evidence: bool }` | `angle` is the research angle; `carry_evidence=True` tightens output schema toward verbatim quoting. |
| (`writer_planner`, `edit`) | — | **never registered (by design)** | Edit is router-exclusive. Writer planner is a strategy/assembly agent; it produces new drafts, never mutates existing ones. See PROTOCOL §2.4. |
| (`deep_search_planner`, `edit`) | — | **never registered (by design)** | Edit is router-exclusive. Deep-search is additive — every turn produces a new `agent_search` artifact. Mutation of a prior artifact, when needed, routes through the router (`edit.search`). See PROTOCOL §2.4. |

---

## 5. Data model — Pydantic v2

### 5.1 Discriminated-union enrichment for `AnalyzerCall.extras` (NEW)

> **Enrichment over source plans.** The source plans typed `extras` as
> `dict[str, Any]` and validated it inside `build_request` against a
> registry-keyed schema lookup. We strengthen this with a Pydantic v2
> **discriminated union** so a single call object can be statically
> narrowed by IDE + mypy. Adopts the pattern documented in the Pydantic
> v2 "Unions / Discriminated Unions" docs (using `Annotated[Union[...], Field(discriminator=...)]`),
> which the docs explicitly recommend over untagged unions for
> performance and predictability.

```python
# agents/memory/item_analyzer/models.py
from typing import Annotated, Any, Awaitable, Callable, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ----- enumerations -----
CallerId     = Literal["router", "writer_planner", "deep_search_planner"]
AnalyzerMode = Literal["analyze", "edit"]
TargetKind   = Literal["notes", "agent_search", "agent_writer", "attachment"]
GroupScope   = Literal["conversation", "turn", "parent_artifact"]

# ----- targets -----
class SpecificTarget(BaseModel):
    type: Literal["specific"] = "specific"
    item_id: str

class GroupTarget(BaseModel):
    type: Literal["group"] = "group"
    kind: TargetKind
    scope: GroupScope = "conversation"
    parent_artifact_id: str | None = None
    turn_id: str | None = None

    @model_validator(mode="after")
    def _scope_requirements(self):
        if self.scope == "turn" and not self.turn_id:
            raise ValueError("scope='turn' requires turn_id")
        if self.scope == "parent_artifact" and not self.parent_artifact_id:
            raise ValueError("scope='parent_artifact' requires parent_artifact_id")
        return self

Target = Annotated[SpecificTarget | GroupTarget, Field(discriminator="type")]

# ----- discriminated extras (the enrichment) -----
class _ExtrasBase(BaseModel):
    """Each subclass declares its caller_id + mode literals as the discriminators."""
    caller_id: CallerId
    mode: AnalyzerMode

class RouterAnalyzeExtras(_ExtrasBase):
    caller_id: Literal["router"] = "router"
    mode: Literal["analyze"] = "analyze"
    focus: str

class RouterEditExtras(_ExtrasBase):
    caller_id: Literal["router"] = "router"
    mode: Literal["edit"] = "edit"
    edit_kind: Literal["factual", "tighten", "insert", "reframe"]

class WriterPlannerAnalyzeExtras(_ExtrasBase):
    caller_id: Literal["writer_planner"] = "writer_planner"
    mode: Literal["analyze"] = "analyze"
    role_hint: Literal["template", "source", "reference", "prior_draft"]
    query: str

class DeepSearchPlannerAnalyzeExtras(_ExtrasBase):
    caller_id: Literal["deep_search_planner"] = "deep_search_planner"
    mode: Literal["analyze"] = "analyze"
    angle: str
    carry_evidence: bool = False

AnalyzerExtras = Annotated[
    RouterAnalyzeExtras
    | RouterEditExtras
    | WriterPlannerAnalyzeExtras
    | DeepSearchPlannerAnalyzeExtras,
    # Pydantic v2 supports a "callable discriminator" which can key on a
    # composite. We use it here because tagging on a single literal is not
    # expressive enough for (caller_id, mode).
    Field(discriminator=_extras_discriminator),
]

def _extras_discriminator(v: Any) -> str:
    """Compose `caller_id:mode` as the union tag.

    Per Pydantic v2 docs: callable discriminators MUST handle both dict
    and model inputs, and MUST NOT mutate the value. Return the literal
    tag string used in the union schema below.
    """
    if isinstance(v, dict):
        return f"{v.get('caller_id')}:{v.get('mode')}"
    return f"{v.caller_id}:{v.mode}"

# Tag registry — kept next to the union so adding a new (caller, mode)
# is a one-place change.
EXTRAS_TAGS: dict[str, type[BaseModel]] = {
    "router:analyze":             RouterAnalyzeExtras,
    "router:edit":                RouterEditExtras,
    "writer_planner:analyze":     WriterPlannerAnalyzeExtras,
    "deep_search_planner:analyze": DeepSearchPlannerAnalyzeExtras,
}
```

Pydantic-v2 source: per the [Unions docs](https://docs.pydantic.dev/latest/concepts/unions/), discriminated unions are
**more performant and predictable** than untagged unions. The docs
explicitly note callable discriminators must handle both dict and model
inputs — the helper above complies.

### 5.2 Shared output-schema base classes (NEW enrichment)

> **Enrichment over source plans.** Source plans defined seven independent
> output schemas (4 analyze + 3 edit) with no shared base. We introduce
> two thin base classes so future edits to common fields happen in one
> place and the runner can call `out.new_content_md` without
> `isinstance`-narrowing.

```python
class AnalyzeOutputBase(BaseModel):
    """Common analyzer telemetry. Per-kind subclasses add their own
    structured fields (findings, chunks, sections, facts)."""
    coverage: Literal["full", "partial", "none"] = "partial"
    notes_for_caller: str | None = None   # optional Arabic narrative

class EditOutputBase(BaseModel):
    """Common editor surface. Per-kind subclasses MAY add kind-specific
    diagnostic fields but MUST NOT remove or rename these three."""
    new_content_md: str
    edit_summary_ar: str
    no_change: bool = False

    @model_validator(mode="after")
    def _coherent(self):
        # If editor says no_change, new_content_md MUST be empty string.
        # (Runner uses no_change as the branch — guarantees no accidental write.)
        if self.no_change and self.new_content_md.strip():
            raise ValueError(
                "no_change=True with non-empty new_content_md — invalid edit output"
            )
        if not self.no_change and not self.new_content_md.strip():
            raise ValueError(
                "no_change=False requires non-empty new_content_md"
            )
        if not self.edit_summary_ar.strip():
            raise ValueError("edit_summary_ar must not be empty")
        return self
```

Per-sub-agent subclasses:

```python
# Analyze
class NoteFinding(BaseModel):
    excerpt: str            # verbatim from notes
    why_relevant: str       # short Arabic

class NotesAnalyzeOutput(AnalyzeOutputBase):
    findings: list[NoteFinding]

class ChunkRef(BaseModel):
    chunk_id: str
    verbatim: str
    reason: str

class SearchAnalyzeOutput(AnalyzeOutputBase):
    relevant_chunks: list[ChunkRef]
    angle_coverage: str

class SectionRef(BaseModel):
    heading: str
    slice_md: str
    relevance: str

class WriterAnalyzeOutput(AnalyzeOutputBase):
    matched_sections: list[SectionRef]
    summary_ar: str

class ExtractedFact(BaseModel):
    entity: str
    value: str
    verbatim_span: str      # MUST appear in source content_md

class AttachmentAnalyzeOutput(AnalyzeOutputBase):
    facts: list[ExtractedFact]
    narrative: str

# Edit
class NotesEditOutput(EditOutputBase): pass
class SearchEditOutput(EditOutputBase): pass
class WriterEditOutput(EditOutputBase): pass
```

The three editor outputs are deliberately identical today. Keeping them as
separate types preserves the dispatch table's symmetry and lets us add
kind-specific fields later without renaming.

### 5.3 The call & resolved request

```python
class AnalyzerCall(BaseModel):
    caller_id: CallerId
    mode: AnalyzerMode
    targets: list[Target]                 # 1+ targets
    instruction: str                      # the task — most critical input
    tier: Literal["tier_1", "tier_2"]     # inherited from caller
    user_id: str
    conversation_id: str
    extras: AnalyzerExtras                # discriminated union; never raw dict
    # Idempotency key — optional. Edit callers SHOULD pass one so retries
    # are safe. Analyze ignores it. See §11.4.
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _caller_mode_aligned(self):
        # The extras union tag MUST match the call's (caller_id, mode).
        if self.extras.caller_id != self.caller_id or self.extras.mode != self.mode:
            raise ValueError(
                "extras.caller_id/mode must match call.caller_id/mode"
            )
        return self

class WorkspaceItemRow(BaseModel):
    item_id: str
    kind: str                              # raw DB enum (note|agent_search|...)
    title: str | None
    content_md: str
    word_count: int | None
    current_version_number: int
    parent_item_id: str | None
    turn_id: str | None
    created_at: str

class ConversationMessageView(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: str

class ResolvedRequest(BaseModel):
    sub_agent_id: str
    sub_agent_factory: Callable[..., Any]
    rendered_system_prompt: str
    rendered_user_message: str
    output_schema: type[BaseModel]
    resolved_items: list[WorkspaceItemRow]    # RLS-scoped, created_at ASC
    history: list[ConversationMessageView]
    tier: Literal["tier_1", "tier_2"]
    caller_id: CallerId
    mode: AnalyzerMode
    # Telemetry:
    target_count_input: int
    target_count_resolved: int
    group_expanded: bool
    short_circuit: bool

    model_config = ConfigDict(arbitrary_types_allowed=True)
```

### 5.4 RenderContext (NEW enrichment)

> **Enrichment over source plans.** Source plans pass `items`,
> `instruction`, `extras`, `history` as loose kwargs into renderers. We
> wrap them in a single `RenderContext` Pydantic model so:
> - renderer signatures are stable when future fields are added
> - snapshot tests assert one input object, not four
> - per-renderer unit tests parametrize over a single fixture

```python
class RenderContext(BaseModel):
    items: list[WorkspaceItemRow]
    instruction: str
    extras: AnalyzerExtras
    history: list[ConversationMessageView]

# All 7 renderers share the signature:
#   def render(ctx: RenderContext) -> str
```

### 5.5 Results returned to callers

```python
class AnalyzeResult(BaseModel):
    items: Any                            # the sub-agent's output (NotesAnalyzeOutput | …)
    llm_invoked: bool
    sub_agent_id: str | None
    fallback_used: bool = False

class EditResult(BaseModel):
    success: bool
    fallback_used: bool
    no_change: bool
    version_number: int | None            # NEW current_version_number after commit
    edit_summary_ar: str | None
    # NEW enrichment — correlation handles for downstream:
    version_id: str | None = None         # PK of the workspace_item_versions row written
    run_id: str | None = None             # PK of the agent_runs row written
```

`version_id` and `run_id` are added so callers (and tests) can join the
agent_runs row to the version row without re-querying.

### 5.6 Error types

```python
class AnalyzerCallError(Exception):
    """Raised by request_builder for any shape / extras / kind violation.
    Message is in Arabic (Luna rule)."""

class ItemNotFoundError(AnalyzerCallError):
    """Specific target unresolvable; edit mode hard-raises this."""

class OwnershipViolation(Exception):
    """commit_item_revision rejects edited_by_agent that doesn't start
    with 'edit.'. Caught by CI test; never reached in production."""
```

---

## 6. Dispatch contract

### 6.1 Registry

```python
# agents/memory/item_analyzer/dispatch.py
from dataclasses import dataclass

@dataclass(frozen=True)
class SubAgentSpec:
    id: str
    factory: Callable[..., Any]
    schema: type[BaseModel]
    prompt: str
    renderer: Callable[[RenderContext], str]

SUB_AGENT_REGISTRY: dict[tuple[AnalyzerMode, TargetKind], SubAgentSpec] = {
    ("analyze", "notes"):        SubAgentSpec("analyze.notes",      create_notes_analyzer,      NotesAnalyzeOutput,      ANALYZE_NOTES_SYSTEM_AR,      render_analyze_notes),
    ("analyze", "agent_search"): SubAgentSpec("analyze.search",     create_search_analyzer,     SearchAnalyzeOutput,     ANALYZE_SEARCH_SYSTEM_AR,     render_analyze_search),
    ("analyze", "agent_writer"): SubAgentSpec("analyze.writer",     create_writer_analyzer,     WriterAnalyzeOutput,     ANALYZE_WRITER_SYSTEM_AR,     render_analyze_writer),
    ("analyze", "attachment"):   SubAgentSpec("analyze.attachment", create_attachment_analyzer, AttachmentAnalyzeOutput, ANALYZE_ATTACHMENT_SYSTEM_AR, render_analyze_attachment),
    ("edit",    "notes"):        SubAgentSpec("edit.notes",         create_notes_editor,        NotesEditOutput,         EDIT_NOTES_SYSTEM_AR,         render_edit_notes),
    ("edit",    "agent_search"): SubAgentSpec("edit.search",        create_search_editor,       SearchEditOutput,        EDIT_SEARCH_SYSTEM_AR,        render_edit_search),
    ("edit",    "agent_writer"): SubAgentSpec("edit.writer",        create_writer_editor,       WriterEditOutput,        EDIT_WRITER_SYSTEM_AR,        render_edit_writer),
}
```

### 6.2 Validation rules (every rejection path)

| Condition | Result |
|---|---|
| `mode='edit'` and any target is `GroupTarget` | `AnalyzerCallError("التعديل يقبل عنصراً محدداً واحداً فقط")` |
| `mode='edit'` and `len(targets) > 1` | `AnalyzerCallError("التعديل يقبل عنصراً واحداً فقط")` |
| `mode='edit'` and resolved kind is `attachment` | `AnalyzerCallError("لا يمكن تعديل مرفقات المستخدم")` |
| `mode='analyze'` and resolved items mix kinds | `AnalyzerCallError("جميع العناصر يجب أن تشترك في النوع")` |
| `(call.caller_id, call.mode)` not in `EXTRAS_TAGS` | `AnalyzerCallError("هذا المستدعي لا يدعم هذا الوضع")` |
| `extras` payload fails union validation | `AnalyzerCallError("بيانات الاستدعاء غير صالحة: …")` |
| `(mode, kind)` not in `SUB_AGENT_REGISTRY` | `AnalyzerCallError("لا يوجد محلل لهذا الوضع/النوع")` |
| `GroupTarget(scope='turn', turn_id=None)` | Raised by `GroupTarget` validator at parse time. |
| Resolution returns 0 items | NOT an error — `short_circuit=True`, runner returns empty result (analyze) or raises `ItemNotFoundError` (edit). |

### 6.3 Kind-name reconciliation (OPEN QUESTION — see §17)

The DB `workspace_item_kind` enum (migration 026) uses `note` (singular)
and `agent_writing` (NOT `agent_writer`). The source plans use `notes` and
`agent_writer` in the Python `TargetKind` literal. The dispatch contract
MUST translate consistently. Recommended convention:

```python
# Python-side TargetKind values (used in dispatch keys + Pydantic models):
TargetKind = Literal["notes", "agent_search", "agent_writer", "attachment"]

# DB-side workspace_item_kind enum values:
# 'note' | 'agent_search' | 'agent_writing' | 'attachment' | 'convo_context' | 'references'

_KIND_DB_TO_PY = {
    "note": "notes",
    "agent_search": "agent_search",
    "agent_writing": "agent_writer",
    "attachment": "attachment",
}
_KIND_PY_TO_DB = {v: k for k, v in _KIND_DB_TO_PY.items()}
```

The group resolver translates incoming `kind` (Python form) to DB form
before querying; `WorkspaceItemRow.kind` is stored in DB form, then
mapped to Python form for the same-kind invariant check. **This mapping
lives in `dispatch.py::_KIND_DB_TO_PY` and is the only place that
crosses the naming boundary.** Flagged in §17 — confirm with user before
build wave 5.

---

## 7. Dependencies (deps dataclass fields)

```python
# agents/memory/item_analyzer/deps.py
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

@dataclass
class AnalyzerDeps:
    supabase: SupabaseClient            # shared/db/client.py
    http_client: AsyncClient            # shared http for any future tools
    user_id: str
    conversation_id: str
    tier: Literal["tier_1", "tier_2"]
    # Edit-only — None in analyze mode. Layer-2 SSE dep injection.
    user_emit: Callable[[str], Awaitable[None]] | None = None
    # Optional Logfire helper override (tests pass a stub).
    logfire: Any = None
```

> **Pydantic AI dependencies pattern reference**: per the
> [Dependencies docs](https://ai.pydantic.dev/dependencies/), deps are
> carried via `RunContext` and should be a dataclass. We follow that
> exactly. Per the docs, dependencies should be "isolated between
> requests by building a new dependencies object each time" — our
> `build_analyzer_deps(...)` factory creates a fresh instance per call.

### `build_analyzer_deps`

```python
def build_analyzer_deps(
    *,
    supabase: SupabaseClient,
    http_client: AsyncClient,
    user_id: str,
    conversation_id: str,
    tier: Literal["tier_1", "tier_2"],
    user_emit: Callable[[str], Awaitable[None]] | None = None,
) -> AnalyzerDeps: ...
```

### External services

| Service | Purpose |
|---|---|
| Supabase Postgres (`workspace_items`, `workspace_item_versions`, `messages`) | Read targets, snapshot, commit revision, load history. RLS-scoped. |
| Supabase Auth | Not invoked directly; user_id comes pre-validated from the orchestrator. |
| Pydantic AI `FallbackModel` (Alibaba primary, OpenRouter fallback × Qwen/DeepSeek) | LLM calls. Already configured in `agents/utils/agent_models.py`. |
| Pydantic Logfire | Spans + structured attributes. Already wired via `shared/observability.py`. |
| sse-starlette (indirect) | The Layer-2 emitter (`user_emit`) sends events through the existing SSE writer the orchestrator already manages. Layer 4 sees only the callable. |

**No new heavyweight dependencies.** Everything is on the existing Luna stack.

---

## 7A. References — fetch, reflect, reconcile (NEW capability)

> **Why this exists.** `agent_search` WIs are not just Markdown — their
> `content_md` carries `[n]` citation tokens that map 1:1 to rows in the
> `workspace_item_references` table (migration 049). A sub-agent that sees
> only `content_md` can read the prose but cannot reason about *what each
> `[n]` actually points to*. It can't verify a verbatim quote, can't tell
> the user that `[3]` cites a repealed regulation, and an editor that
> reshuffles citations would silently corrupt the `used` column.
>
> This section adds first-class reference handling to the item_analyzer
> family: load on read, surface to the LLM, reflect in the output schema,
> and reconcile on commit.

### 7A.1 The `workspace_item_references` table (live shape, verified 2026-05-25)

| Column | Type | Notes |
|---|---|---|
| `ref_pk` | `uuid` PK | `gen_random_uuid()` |
| `wi_id` | `uuid NOT NULL` | FK → `workspace_items(item_id) ON DELETE CASCADE` |
| `item_id` | `text NOT NULL` | Source-table key (`chunks_v2.chunk_id` for regulations, case ref for cases, `services.service_ref` for compliance). NOT the workspace_items PK. |
| `domain` | `text NOT NULL` | `regulations` \| `compliance` \| `cases` (CHECK) |
| `n` | `integer NOT NULL > 0` | The `[n]` token rendered in `content_md` (CHECK n > 0) |
| `relevance` | `text NOT NULL` | `high` \| `medium` (CHECK) |
| `used` | `boolean NOT NULL DEFAULT false` | TRUE iff `[n]` appears in the final `content_md` |
| `sub_queries` | `integer[] NOT NULL DEFAULT '{}'` | Which sub-query indices produced this ref |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |
| | `UNIQUE (wi_id, n)` | One row per citation slot per WI |

**RLS** (all four commands gated identically): row visible iff the parent
`workspace_items.user_id` matches `auth.uid()`'s user. The analyzer's
service-role client bypasses RLS but the orchestrator passes `user_id` for
defense in depth.

**Indexes**: `(wi_id, used)`, `(item_id)`, `(domain, item_id)`.

**Coverage today** (verified): 47 `agent_search` WIs hold 792 refs total.
**Zero rows for `agent_writer`, `notes`, or `attachment` WIs.** This shapes
the loader policy: ref loading runs on `agent_search` only in v1. If a
future agent persists refs on writer WIs, extend §7A.6.

### 7A.2 The loader — `fetch_item_references` (already exists)

`backend/app/services/references_service.py::fetch_item_references` is the
canonical read path. It:

1. Selects rows from `workspace_item_references` filtered by `wi_id`
   (and `used=true` when `used_only=True`).
2. Groups by domain and **batch-fetches the underlying source rows** via
   the existing URA enrichment helpers (`_enrich_regulations`,
   `_enrich_cases`, plus a local services lookup for compliance).
3. Builds full `Reference` Pydantic objects byte-identical to what the
   deep_search aggregator publishes (title, snippet, relevance, ref_id,
   source_view, etc.).
4. Returns `list[Reference]` ordered by `n` ASC. Missing source rows
   become stub `Reference`s with `regulation_title="[المصدر غير متوفر]"`
   — so the model can flag broken citations without crashing.

The item_analyzer family **reuses this function as-is**. No new fetch
plumbing. We import it through the deps factory so tests can swap in a
fake without monkey-patching:

```python
# agents/memory/item_analyzer/deps.py — extended
@dataclass
class AnalyzerDeps:
    supabase: SupabaseClient
    http_client: AsyncClient
    user_id: str
    conversation_id: str
    tier: Literal["tier_1", "tier_2"]
    user_emit: Callable[[str], Awaitable[None]] | None = None
    logfire: Any = None
    # NEW — reference loader, injectable for tests. Default wires the real
    # references_service.fetch_item_references; tests pass a stub.
    load_references: Callable[[str, bool], Awaitable[list["Reference"]]] = field(
        default_factory=lambda: _default_reference_loader
    )

def build_analyzer_deps(
    *, supabase, http_client, user_id, conversation_id, tier,
    user_emit=None, load_references=None,
) -> AnalyzerDeps:
    if load_references is None:
        load_references = functools.partial(_default_reference_loader, supabase=supabase)
    return AnalyzerDeps(
        supabase=supabase, http_client=http_client,
        user_id=user_id, conversation_id=conversation_id, tier=tier,
        user_emit=user_emit, load_references=load_references,
    )

async def _default_reference_loader(
    wi_id: str, used_only: bool = False, *, supabase
) -> list[Reference]:
    return await fetch_item_references(supabase, wi_id, used_only=used_only)
```

### 7A.3 Two-track ref access — eager (used) + on-demand (unused)

**Design principle.** Used refs are part of the WI's identity — the `[n]`
tokens in `content_md` only make sense if their cited rows are visible.
Unused refs are *latent strength*: rows from the original search the
writer decided not to cite this round. The editor or analyzer should be
able to reach for them when the task warrants, but they don't belong in
every prompt by default. So:

- **Used refs (`used=true`) — eager pre-load.** The request builder
  fetches them for every resolved WI whose kind has refs, renders them
  into the user message, model sees them up front. This is "the WI
  unfolded."
- **Unused refs (`used=false`) — on-demand via tool.** Every sub-agent
  registers a Pydantic AI `@agent.tool` named `get_unused_references`.
  The model calls it when it needs to reason about latent citations
  (typical triggers: analyzer told to widen coverage; editor considering
  whether to *promote* an unused ref to cited).

**The builder's pre-load policy:**

```python
_REF_BEARING_KINDS: set[str] = {"agent_search"}
# v2 hook: add 'agent_writer' here only when agent_writer publishes its own
# ref rows. Today it borrows [n] from its parent agent_search.

# Eager load is ALWAYS used_only=True. Unused refs go through the tool path.
async def _preload_used_refs(items, deps) -> dict[str, list[Reference]]:
    targets = [it.item_id for it in items if it.kind in _REF_BEARING_KINDS]
    if not targets:
        return {}
    results = await asyncio.gather(
        *(deps.load_references(wi_id, used_only=True) for wi_id in targets),
        return_exceptions=True,
    )
    out: dict[str, list[Reference]] = {}
    for wi_id, res in zip(targets, results):
        if isinstance(res, Exception):
            logger.warning("ref preload failed for %s: %s", wi_id, res)
            out[wi_id] = []
        else:
            out[wi_id] = res
    return out
```

The builder ALSO records per-WI **total ref counts** (cheap COUNT query,
one batch) so the renderer can hint "X used, Y unused available" without
loading the unused payload.

```python
async def _ref_totals(items, deps) -> dict[str, int]:
    """Return {wi_id: total ref count}. One batched query."""
    wi_ids = [it.item_id for it in items if it.kind in _REF_BEARING_KINDS]
    if not wi_ids:
        return {}
    rows = await asyncio.to_thread(
        lambda: deps.supabase.table("workspace_item_references")
            .select("wi_id, count", count="exact")
            .in_("wi_id", wi_ids)
            .execute()
    )
    # PostgREST returns one row per wi_id with count via grouping; if not,
    # group in Python.
    return _group_count(rows.data)
```

Failures are isolated — one broken ref load returns `[]` for that WI,
logs a warning, sets Logfire attribute `refs_load_failed_for: list[wi_id]`.
The runner proceeds; the tool path remains available even after a
preload failure (it queries directly).

`RenderContext` carries both tracks:

```python
class RenderContext(BaseModel):
    items: list[WorkspaceItemRow]
    instruction: str
    extras: AnalyzerExtras
    history: list[ConversationMessageView]
    # NEW — wi_id -> list[Reference] (used=true only) in n-ASC order.
    # Empty list for any WI whose kind is not ref-bearing OR whose preload failed.
    references_by_wi_id: dict[str, list["Reference"]] = Field(default_factory=dict)
    # NEW — wi_id -> total ref count (used + unused). Drives the renderer
    # hint and lets the model decide whether to call the tool.
    ref_totals_by_wi_id: dict[str, int] = Field(default_factory=dict)
```

### 7A.4 Renderer block — what the LLM sees by default

Every renderer for a ref-bearing kind emits a `<references>` block per
item, immediately after `<content_md>`. **Only `used=true` refs are
rendered.** The block carries a `total` hint so the model knows there's
more available via tool:

```
<item id="b66f7317-2af8-45a6-ab68-46abebd37018" kind="agent_search">
  <title>...</title>
  <content_md>
    ... المحامي ملزم بـ [1] والمدعى عليه يجب عليه [3] ...
  </content_md>
  <references used="6" total="14" unused_available_via_tool="get_unused_references">
    <ref n="1" domain="regulations" relevance="high">
      <title>نظام المحاماة — المادة 12</title>
      <snippet>... النص الحرفي من chunk ...</snippet>
    </ref>
    <ref n="3" domain="cases" relevance="medium">
      <title>قضية ...</title>
      <snippet>...</snippet>
    </ref>
    <!-- only used refs appear here. broken used-refs are flagged: -->
    <ref n="7" domain="regulations" relevance="high" broken="true">
      <title>[المصدر غير متوفر]</title>
      <snippet/>
    </ref>
  </references>
</item>
```

Shared helper in `user_message_renderers/_common.py`:
`render_used_references_block(refs: list[Reference], total: int) -> str`.

**Token-budget win.** The 47 production WIs average ~17 refs but typically
6–8 are cited. Used-only rendering ≈ halves the inline ref tokens, which
matters most for tier_2 (router-edit). For agent_search WIs with 0 used
refs (rare but possible), the block renders as
`<references used="0" total="N" .../>` — a self-describing empty
container that signals "use the tool to discover what's here."

### 7A.4a Tool — `get_unused_references` (NEW)

Every sub-agent in the family registers this tool. Implementation lives in
`agents/memory/item_analyzer/tools.py`:

```python
# agents/memory/item_analyzer/tools.py
from pydantic import BaseModel
from pydantic_ai import RunContext
from agents.memory.item_analyzer.deps import AnalyzerDeps

class ReferenceLite(BaseModel):
    """Trimmed Reference for the tool return shape — no source_view, no
    full URA payload. The model gets enough to decide whether to cite."""
    n: int
    domain: Literal["regulations", "compliance", "cases"]
    relevance: Literal["high", "medium"]
    title: str
    snippet: str                      # capped at 400 chars
    broken: bool = False              # source row unresolvable
    sub_queries: list[int] = []       # which sub-query produced this ref

async def get_unused_references(
    ctx: RunContext[AnalyzerDeps],
    wi_id: str,
) -> list[ReferenceLite]:
    """Fetch references for `wi_id` that are NOT currently cited
    (workspace_item_references.used = false).

    USE WHEN:
      • The instruction asks you to widen coverage / strengthen citations.
      • You're editing and considering whether to PROMOTE an unused ref
        to cited (legal — see refs_used_after in your output schema).
      • The user asked about a fact the cited refs don't cover but the
        WI's total ref count (see <references total=...>) suggests more
        evidence exists.

    DO NOT call repeatedly for the same wi_id — the result is cached for
    the call's lifetime. One call per WI you care about, max.

    Returns refs ordered by n ASC. Each carries `broken=true` if its
    source row is unresolvable — do not cite broken refs.
    """
    if wi_id not in _wi_id_allowlist(ctx):
        # Defense in depth: only WIs already resolved into this call are
        # legitimate targets. Prevents prompt-injection cross-WI peeking.
        raise ToolError(f"wi_id {wi_id} not in current call scope")
    full = await ctx.deps.load_references(wi_id, used_only=False)
    used_n = {r.n for r in ctx.deps.cached_used_refs_for(wi_id)}
    unused = [r for r in full if r.n not in used_n]
    return [_to_lite(r) for r in unused]

def _to_lite(ref: Reference) -> ReferenceLite:
    return ReferenceLite(
        n=ref.n,
        domain=ref.domain,
        relevance=ref.relevance,
        title=ref.title or ref.regulation_title or "",
        snippet=(ref.snippet or "")[:400],
        broken=(ref.title or ref.regulation_title or "").startswith("[المصدر غير متوفر]"),
        sub_queries=list(getattr(ref, "sub_queries", []) or []),
    )
```

**Why a tool and not auto-render of all refs:**

| Concern | Effect |
|---|---|
| Token cost | Halves typical ref payload; cost saving compounds in conversations with many WIs (writer planner batches). |
| Signal-to-noise | The model isn't drowned in unused refs when its task only concerns cited ones (most edits). |
| Explicit reasoning | A tool call is a visible decision in the trace ("I'm reaching for unused refs because the user asked X"). That's auditable; pre-rendering is not. |
| Scope safety | The tool checks `wi_id` is in the current call's resolved scope. Cross-WI fishing is rejected. |

**Registration pattern.** Per the Pydantic AI [Tools docs](https://ai.pydantic.dev/tools/),
tools attach to the agent at construction time. We register the tool on
**all 7 sub-agents** — for kinds without refs (notes, attachment), the
tool simply returns `[]`. That's cheaper than per-kind tool wiring and
gives the model uniform behavior across kinds. Example wiring inside the
sub-agent factory:

```python
def create_search_analyzer(model: FallbackModel, output_type: type[BaseModel]) -> Agent:
    agent = Agent(model=model, output_type=output_type, deps_type=AnalyzerDeps,
                   system_prompt=ANALYZE_SEARCH_SYSTEM_AR)
    agent.tool(get_unused_references)
    return agent
```

`ctx.deps.cached_used_refs_for(wi_id)` reads the pre-loaded eager set so
the tool can subtract — no duplicate DB hit on the eager path.

### 7A.5 Reflection — what the sub-agent must produce

**`SearchAnalyzeOutput`** gains three reflection fields. The analyzer can
have called `get_unused_references` before producing this output, so
`unused_but_relevant` is meaningful (only populated when the tool was
called and the model judged some unused refs relevant to the angle):

```python
class CitedRefRef(BaseModel):
    """A reference the analyzer judged relevant to the caller's angle."""
    n: int                       # must exist for this wi_id in workspace_item_references
    why_relevant: str            # short Arabic
    verbatim_excerpt: str | None # exact slice from that ref's snippet, when used

class SearchAnalyzeOutput(AnalyzeOutputBase):
    relevant_chunks: list[ChunkRef]
    angle_coverage: str
    # NEW — reflection on the references
    cited_references: list[CitedRefRef]     # refs (used) judged relevant to the angle
    broken_refs: list[int] = []             # refs whose source row was unresolvable
    unused_but_relevant: list[int] = []     # n's the analyzer pulled via the tool
                                            # and judged worth promoting (writer planner
                                            # may surface these in WriterPackage)
```

**`SearchEditOutput`** carries a refs delta the runner uses for
reconciliation (§7A.7). The `refs_new_invented` field from the prior
revision is **removed** — promoting a previously-unused ref to cited
status is now a legitimate edit (the editor calls
`get_unused_references` to discover what's available, then cites the
relevant ones). The "must not invent" rule survives at the runner level:
every `n` in `refs_used_after` must exist as a row in
`workspace_item_references` for this wi_id. The runner enforces this with
one cheap SELECT.

```python
class SearchEditOutput(EditOutputBase):
    # NEW — citation reconciliation
    refs_used_after: list[int] = []     # n's the editor ended up citing (after edit)
                                          # — may include previously-unused n's that
                                          # the editor promoted via the tool
    refs_dropped:    list[int] = []     # n's the editor removed from citations
                                          # — must have been used=true before edit
```

`model_validator` on `SearchEditOutput` (model-side, fast checks only):

1. `refs_used_after` and `refs_dropped` are disjoint.
2. Both lists contain only positive integers, no duplicates.
3. `refs_used_after` is non-empty iff `new_content_md` contains any `[n]`
   token (cheap regex). Empty `refs_used_after` with cited `[n]` text in
   `new_content_md` is an obvious model error — fail before commit.

The **existence + scope check** lives in the runner (§7A.7), not the
model_validator, because the model doesn't have direct access to the
full ref set — only the eagerly-loaded used subset plus whatever it
fetched via tool. The runner has the authoritative SELECT.

**Prompt instruction** (added to every Arabic system prompt for
ref-bearing kinds):

> «المراجع المُستخدمة حالياً معروضة في كتلة `<references>`. لا تخترع
> مرجعاً جديداً ولا تغيّر ترقيمها. إذا احتجت إلى مراجع غير مستخدمة (لتقوية
> الاستشهاد أو لتوسيع التغطية)، استدع أداة `get_unused_references` مرة
> واحدة لكل WI. عند التحرير، يمكنك ترقية مرجع غير مستخدم إلى مستخدم
> بالإشارة إليه `[n]` في النص الجديد وإدراجه في `refs_used_after`. أبلغ
> عن المراجع المنقطعة (broken) ولا تستشهد بها.»

(Polished phrasing owned by `pydantic-ai-prompt-engineer`.)

### 7A.6 Per-sub-agent applicability matrix

The `get_unused_references` tool is registered on **all 7 sub-agents** —
uniform behavior, and for kinds without refs the tool returns `[]`
cheaply. Eager pre-load and the `<references>` rendered block apply only
to kinds that have rows in the table.

| Sub-agent | Eager pre-load (used refs) | Render block | `get_unused_references` tool | Schema fields |
|---|---|---|---|---|
| `analyze.notes` | No | No | Registered (returns []) | unchanged |
| `analyze.search` | **Yes** | **Yes** | **Registered + actively used** | `cited_references`, `broken_refs`, `unused_but_relevant` |
| `analyze.writer` | No (v1) | No (v1) | Registered (returns []) | unchanged. v2 follow-up below. |
| `analyze.attachment` | No | No | Registered (returns []) | unchanged |
| `edit.notes` | No | No | Registered (returns []) | unchanged (notes have no refs) |
| `edit.search` | **Yes** | **Yes** | **Registered + actively used for promotion** | `refs_used_after`, `refs_dropped` |
| `edit.writer` | No (v1) | No (v1) | Registered (returns []) | unchanged. v2 follow-up below. |

**Why register on every sub-agent even when refs are empty?** Three reasons:

1. Uniform tool surface lets `pydantic-ai-prompt-engineer` share prompt
   instructions across all 7 prompts without per-kind branching.
2. The Wave-9 follow-up (parent-resolved refs for writer WIs) is a
   one-line change in the tool body, not a refactor of every factory.
3. A model calling the tool on a kind without refs gets `[]` and learns
   to stop calling — cheaper than throwing a tool-unavailable error.

**v2 follow-up — agent_writer parent-ref resolution** (NOT in v1 scope):
`agent_writer` WIs carry `[n]` tokens that borrow from a parent
`agent_search` artifact via the `parent_item_id` link the request
builder already understands. To let `analyze.writer` / `edit.writer`
reason about citations, the tool body would:

1. Resolve `parent_item_id` for the writer WI.
2. If parent exists and parent's kind is `agent_search`, call
   `load_references(parent_wi_id, used_only=False)`.
3. Subtract whatever `[n]` is already cited in the writer's
   `content_md`; return the rest as unused.

Eager pre-load would mirror: load parent's used refs, render under the
writer WI. Deferred because (a) zero writer WIs in production carry refs
today and (b) it raises a separate question — when the writer edit
promotes a parent's unused ref, does the parent's `used` column flip
too? That's a cross-WI mutation that deserves a dedicated decision, not
a quiet shipped feature.

### 7A.7 Reconciliation on edit commit

When `edit.search` returns `success=True, no_change=False`, the runner
performs an **atomic four-step commit** (extending §11):

1. **Re-extract** cited `[n]` from `new_content_md` via
   `agents/deep_search_v4/postvalidator.extract_cited_numbers` (one
   regex pass). Cross-check: `set(extracted) == set(refs_used_after)`.
   Mismatch → `EditValidationError`, no commit, no version, apology
   «تعذر التعديل بسبب تعارض في المراجع».
2. **Existence check**: one SELECT against
   `workspace_item_references` for this `wi_id` returns the full set of
   valid `n` values. Validate:
   - Every `n` in `refs_used_after` is in that set (no inventing — the
     ref must exist; promoting from unused to used is fine).
   - Every `n` in `refs_dropped` is in that set AND was previously
     `used=true` (you can't drop what isn't cited).
   - Any violation → `EditValidationError`, no commit, no version,
     apology «تعذر التعديل: مرجع غير معروف» (invented) or
     «تعذر التعديل: محاولة حذف مرجع غير مستشهد» (drop-of-unused).
3. **Snapshot** the prior `content_md` into `workspace_item_versions`
   (existing) and **update** `workspace_items.content_md` + bump
   `current_version_number` (existing).
4. **Reconcile** the `used` column on `workspace_item_references` for
   this `wi_id`:
   - Rows in `refs_used_after` → `used=true` (promotes any
     previously-unused refs)
   - Rows in `refs_dropped` → `used=false`
   - Rows in neither list → left untouched (the editor's delta is
     authoritative for what it changed, not for the full state).

Steps 2–4 run inside the same Supabase transaction (or RPC). Failure of
any step rolls back the entire commit. Concurrency: covered by the
existing `(item_id, version_number)` unique constraint + 1-retry policy.

**The promotion path** — the new capability this enables:

```
Before edit:  content_md cites [1] [3]. Refs in table: 1 (used), 2 (unused),
              3 (used), 4 (unused), 5 (unused).
Editor calls get_unused_references(wi_id) → sees 2, 4, 5.
Editor decides ref 4 strengthens the angle.
Editor returns new_content_md with [1] [3] [4] and refs_used_after=[1,3,4].
Runner: extract_cited_numbers = {1,3,4} ✓ matches refs_used_after.
Runner: existence check — 1, 3, 4 all in table ✓.
Runner: snapshot + content update + UPDATE used=true WHERE n IN (1,3,4)
        AND used=false WHERE n IN (refs_dropped).
After edit:  content_md cites [1] [3] [4]. Row n=4 now used=true.
```

The eager-renderer / tool split makes this safe: the editor only has
access to refs that already exist for this wi_id, so it can't fabricate
new evidence — only redistribute what the original search produced.

### 7A.8 `commit_item_revision` extension

Signature gains one optional parameter:

```python
async def commit_item_revision(
    supabase: SupabaseClient,
    *,
    item_id: str,
    new_content_md: str,
    edited_by_agent: str,
    edit_caller_id: str,
    edit_instruction: str,
    edit_summary_ar: str,
    edit_kind: str | None,
    fallback_used: bool,
    user_id: str,
    idempotency_key: str | None = None,
    # NEW — citation reconciliation (None for non-ref-bearing kinds)
    refs_used_after: list[int] | None = None,
    refs_dropped:    list[int] | None = None,
) -> tuple[int, str, str]:    # (version_number, new_content_md, version_id)
```

When both `refs_used_after` and `refs_dropped` are None (notes / writer in
v1), the function skips the third statement. The CI lint script's
allowlist is unchanged — `commit_item_revision` remains the only writer of
both `workspace_items.content_md` and `workspace_item_references.used`
post-publish.

### 7A.9 Failure modes (additions to §13)

| Failure | Behavior |
|---|---|
| Eager preload fails for one WI | That WI renders `<references used="0" total="N" .../>`; Logfire `refs_load_failed_for=[wi_id]`. Analyze proceeds. Edit also proceeds — the tool path still works (queries directly), so the editor can self-recover via `get_unused_references`. |
| Eager preload fails for all WIs | Span attribute `refs_preload_all_failed=true`, both modes proceed via tool path only. |
| `get_unused_references` called with a `wi_id` not in the call's resolved scope | Tool raises `ToolError` (scope guard, defense in depth). Pydantic AI feeds the error back to the model, which generally adjusts and retries with a valid wi_id. |
| `get_unused_references` DB call fails | Tool returns `[]` and logs (Logfire attribute `tool_refs_fetch_failed=true`). The model proceeds without unused refs — typically returns `no_change=True` if it was specifically reaching for them. |
| Editor's `refs_used_after` doesn't match `extract_cited_numbers(new_content_md)` | Runner step 1 raises `EditValidationError` → no commit, no version, emit «تعذر التعديل بسبب تعارض في المراجع». |
| Editor cites an `n` not in `workspace_item_references` for this wi_id | Runner step 2 raises `EditValidationError` → no commit, no version, emit «تعذر التعديل: مرجع غير معروف. أعد الصياغة بدون اختراع مراجع.» |
| Editor's `refs_dropped` contains an `n` that was never `used=true` | Runner step 2 raises `EditValidationError` → no commit, no version, emit «تعذر التعديل: محاولة حذف مرجع غير مستشهد». |
| Reconciliation UPDATE on `workspace_item_references.used` fails mid-transaction | Whole transaction rolls back. Version row + content update both reverted. Runner returns `EditResult(success=False)`. |
| Source row for a ref is gone (regulation re-chunked, case deleted) | Loader (eager OR tool path) emits a stub `Reference` flagged `broken=true`. The model reports it in `broken_refs` (analyze) or refuses to promote it (edit). |
| Model calls `get_unused_references` repeatedly for the same wi_id | Pydantic AI dedups identical tool calls by default; if it doesn't, the runner caches per-call. Either way, the underlying loader runs at most once per (wi_id, used_only) tuple per call. |

### 7A.10 Observability (additions to §12)

New span attributes on `item_analyzer.build_request`:

| Attribute | Type | Meaning |
|---|---|---|
| `refs_preloaded_used_total` | int | Sum of `len(used refs)` eagerly loaded across all WIs |
| `refs_total_by_wi` | dict[wi_id, int] | Full ref count per WI (from the COUNT query) — drives the model's tool-call decision |
| `refs_load_failed_for` | list[str] | wi_ids whose eager preload failed |
| `refs_broken_count` | int | Eager-loaded refs whose source row was unresolvable |

New child span `item_analyzer.tool.get_unused_references` (Pydantic AI
auto-instruments tool spans per the [Logfire integration docs](https://ai.pydantic.dev/logfire/)):

| Attribute | Type | Meaning |
|---|---|---|
| `wi_id` | str | Target WI |
| `unused_returned_count` | int | Number of unused refs returned |
| `tool_refs_fetch_failed` | bool | Tool errored (returned []) |
| `scope_rejected` | bool | wi_id outside the call's resolved scope |

New span attributes on `item_analyzer.edit`:

| Attribute | Type | Meaning |
|---|---|---|
| `refs_used_after_count` | int | `len(refs_used_after)` |
| `refs_dropped_count` | int | `len(refs_dropped)` |
| `refs_promoted_count` | int | n's in `refs_used_after` that were previously `used=false` — the headline metric for the new capability |
| `refs_reconciled` | bool | True iff step 4 of commit succeeded |
| `refs_validation_failed` | bool | True iff editor mismatched, invented, or drop-of-unused |
| `tool_get_unused_calls` | int | How many times the editor called the tool this run |

New `agent_runs.per_phase_stats` keys: `refs_preloaded_used`,
`refs_total`, `refs_used_after`, `refs_dropped`, `refs_promoted`,
`tool_get_unused_calls`.

### 7A.11 Tests (additions to §16)

| Test | Covers |
|---|---|
| `test_references_preload.py::test_eager_loads_used_only` | analyze.search on a WI with 5 used + 9 unused → `RenderContext.references_by_wi_id` has 5 entries (used only); `ref_totals_by_wi_id == {wi_id: 14}`; rendered block has `used="5" total="14"`. |
| `test_references_preload.py::test_preload_failure_isolates_per_wi` | Stub loader raises for 1 of 3 WIs → other 2 preload, failed wi_id renders `used="0" total="N"` with tool still available; span lists the failed wi_id. |
| `test_references_preload.py::test_notes_kind_skips_preload` | analyze.notes → `references_by_wi_id == {}` and `ref_totals_by_wi_id == {}`; loader callable never invoked for the preload path. |
| `test_get_unused_references_tool.py::test_returns_only_unused` | WI with refs 1-5 used, 6-10 unused → tool returns 5 refs with n ∈ {6,7,8,9,10}. |
| `test_get_unused_references_tool.py::test_returns_empty_for_kinds_without_refs` | analyze.notes invokes tool → returns `[]`, no DB error. |
| `test_get_unused_references_tool.py::test_scope_rejects_foreign_wi_id` | Tool called with a wi_id outside the call's resolved scope → `ToolError` raised; span `scope_rejected=true`. |
| `test_get_unused_references_tool.py::test_broken_refs_carry_flag` | Source row missing for n=7 → returned `ReferenceLite(n=7, broken=True)`. |
| `test_references_reflection.py::test_search_analyze_returns_cited_references` | FunctionModel asserts: `cited_references[].n` exists in `workspace_item_references` for wi_id; `verbatim_excerpt` (when set) is substring of the ref's snippet. |
| `test_references_reflection.py::test_search_analyze_flags_broken_ref` | Loader returns a stub for n=4 → analyzer's output `broken_refs` contains 4. |
| `test_references_reflection.py::test_analyzer_calls_tool_when_widening` | Instruction asks "وسّع التغطية" → FunctionModel records a tool call; `unused_but_relevant` is non-empty. |
| `test_edit_search_refs.py::test_edit_drops_used_flips_to_false` | edit.search returns `refs_dropped=[3]` → after commit, row `(wi_id, n=3).used == false`. |
| `test_edit_search_refs.py::test_edit_promotes_unused_ref_succeeds` | Editor calls tool, picks unused n=4, returns `new_content_md` containing `[1] [3] [4]` and `refs_used_after=[1,3,4]` → row `(wi_id, n=4).used` flips `false → true`; new version row written; emit fires. |
| `test_edit_search_refs.py::test_edit_cites_nonexistent_n_rejected` | Editor returns `refs_used_after=[99]` and `new_content_md` cites `[99]` → runner step 2 fails existence check → no commit, apology «مرجع غير معروف» emitted. |
| `test_edit_search_refs.py::test_edit_drops_already_unused_ref_rejected` | Editor returns `refs_dropped=[2]` but n=2 was already `used=false` → runner step 2 raises, no commit, apology «محاولة حذف مرجع غير مستشهد» emitted. |
| `test_edit_search_refs.py::test_extracted_n_mismatch_rejected` | `new_content_md` cites `[5]` but `refs_used_after=[1,2,3]` → runner step 1 raises → no commit. |
| `test_edit_search_refs.py::test_reconciliation_atomic_with_version` | Forced UPDATE failure on `workspace_item_references` → version row absent + content unchanged + `EditResult.success=False`. |
| `test_commit_item_revision_refs.py::test_refs_arg_none_skips_step_4` | Call with `refs_used_after=None, refs_dropped=None` → no UPDATE on refs table; only snapshot + content update run. |
| `test_commit_item_revision_refs.py::test_promotion_telemetry` | Commit with one promoted ref → `agent_runs.per_phase_stats.refs_promoted == 1`. |

### 7A.12 Build-order placement

Insert between current build-order steps 3 and 4:

3. `commit_item_revision` (extended signature with `refs_used_after`,
   `refs_dropped`) + tests + CI lint.
   **3b. NEW**: extend `AnalyzerDeps` with `load_references` +
   `cached_used_refs_for`; wire `_default_reference_loader` to
   `fetch_item_references`; unit-test the default and stub paths.
   **3c. NEW**: extend `RenderContext` with `references_by_wi_id` +
   `ref_totals_by_wi_id`; extend `request_builder` with the eager
   used-only preload + COUNT-by-wi_id queries; observability attributes
   added.
   **3d. NEW**: implement `agents/memory/item_analyzer/tools.py::get_unused_references`
   + `ReferenceLite`; register on every sub-agent factory; unit-test the
   tool path including the scope guard and the kinds-without-refs case.

The reflection schema fields (§7A.5) land alongside the per-sub-agent
output schemas in step 5; reconciliation logic (§7A.7), including the
runner's four-step commit and the existence-check SELECT, lands with
`runner.py::edit` in step 10.

---

## 8. Tier inheritance — `get_agent_model` extension

The current `agents/utils/agent_models.py::get_agent_model` accepts an
`override: ModelPolicy | str | None`. It already rejects a `ModelPolicy`
whose `.tier` differs from the slot's declared tier (lines 144–149).

For tier-inheritance, we need a **different semantic**: "I want this
slot's policy, but force the tier to X." We extend `get_agent_model`
with a `tier_override` keyword parameter:

```python
def get_agent_model(
    slot: str,
    override: ModelPolicy | str | None = None,
    tier_override: Literal["tier_1", "tier_2"] | None = None,
) -> FallbackModel:
    """When tier_override is provided, build using the slot's
    (provider, primary family) intent but at the specified tier.
    `override` and `tier_override` are mutually exclusive."""
```

Slot registration in `AGENT_MODELS`:

```python
AGENT_MODELS["item_analyzer"] = ModelPolicy("tier_1")  # default to tier_1
```

The cost-tracking helpers (`tier_of_subagent`, `_SUBAGENT_TIER`) get
extended:

```python
_SUBAGENT_TIER.update({
    "analyze.notes":      "tier_1",   # NOTE: actual tier is dynamic per call;
    "analyze.search":     "tier_1",   #       this map is the DEFAULT used only
    "analyze.writer":     "tier_1",   #       when per_phase_stats lacks per_tier.
    "analyze.attachment": "tier_1",
    "edit.notes":         "tier_1",
    "edit.search":        "tier_1",
    "edit.writer":        "tier_1",
})
```

The runner ALWAYS records `per_phase_stats["per_tier"]` so `estimate_run_cost`
prefers the breakdown over the default map. The map exists only as a
defensive fallback.

> **Pydantic AI FallbackModel reference**: per the [FallbackModel docs](https://ai.pydantic.dev/api/models/fallback/),
> `FallbackModel` advances on `ModelAPIError` (which includes `ModelHTTPError`).
> **Structured-output validation failures do NOT trigger fallback** —
> they go through Pydantic AI's `ModelRetry` path (`retries=1` in our
> agent). This means our `fallback_used` flag captures only HTTP errors,
> not malformed JSON. Document this in `agent_runs.per_phase_stats` so
> dashboards interpret correctly.

---

## 9. Edit-mode user-emission protocol (ordering guarantees)

The crash-safety rule is: **DB version commit MUST land before any
user-visible emission.** This mirrors the codebase's other invariant
("user message saved BEFORE AI call"), flipped for edits.

### 9.1 Strict ordering

```
1.  request_builder.build_request   (no DB write, no emit)
2.  sub_agent.run(...)              (no DB write, no emit — LLM call only)
3.  IF output.no_change:
        a. deps.user_emit(out.edit_summary_ar)   # polite refusal
        b. _record_run(..., no_change=True, version_number=None)
        c. return EditResult(success=True, no_change=True)
    ELSE:
        a. commit_item_revision(...)             # atomic snapshot+update+counter
        b. deps.user_emit(out.edit_summary_ar)   # AFTER commit succeeds
        c. _record_run(..., no_change=False, version_number=N)
        d. return EditResult(success=True, no_change=False, version_number=N, version_id=…, run_id=…)
4.  IF commit step raises (unique-constraint conflict):
        retry ONCE with the next current_version_number
        IF second commit fails: deps.user_emit("تعذر التعديل بسبب تعارض متزامن")
            return EditResult(success=False)
5.  IF LLM hard-fails (post-FallbackModel chain):
        deps.user_emit("تعذر إجراء التعديل حالياً، حاول مرة أخرى.")
        return EditResult(success=False, fallback_used=True)
6.  IF user_emit raises AFTER step 3.b succeeded:
        Log + Logfire span attribute "emit_failed=true".
        Return EditResult(success=True, …) — the DB write persists; the
        chat acknowledgment is best-effort.
```

### 9.2 Concurrency note (NEW enrichment — addressing user's question)

> **User's question, paraphrased:** "What if the SECOND edit's `user_emit`
> has already streamed «تم تعديل …» text before the conflict is detected?"

Answer with the strict ordering above: `user_emit` for the success path
is **only called after `commit_item_revision` returns**. The
`(item_id, version_number)` unique violation is raised *by*
`commit_item_revision`, so the second edit's emit can never have fired
yet. The retry path picks the next version number and emits exactly
once. If retry also fails, the apology («تعذر التعديل بسبب تعارض
متزامن») fires, never a false success.

The only ordering risk is in **step 6** — emit failing after commit
succeeds. That's the existing graceful-degradation: the DB row is
canonical, the chat message is missed, Logfire span attribute
`emit_failed=true` flags it for telemetry, and frontend reconciliation
on next workspace_items refresh shows the new content.

### 9.3 Concurrent edits at the SSE layer

The orchestrator serializes edit calls within a single conversation by
default (one user turn → one edit). The risk surface for `(item_id,
version_number)` conflicts is:
- two different conversations editing the same shared WI (cross-conv —
  deferred per §1.4 #1, so not reachable in v1)
- the user double-clicks "edit" before the first commit lands

For the second case, the orchestrator MUST issue a single
`AnalyzerCall` per UI action. If a retry is sent with the same
`idempotency_key` (see §11.4), `commit_item_revision` detects the
duplicate and returns the prior version_id without re-LLM'ing.

### 9.4 sse-starlette emission

The Layer-2 `user_emit` closure wraps the orchestrator's existing
SSE queue. Per the [sse-starlette docs](https://github.com/sysid/sse-starlette),
`EventSourceResponse` consumes its async generator **sequentially** and
events are protected by an internal `_send_lock`, so call order into
`user_emit` directly maps to wire order. Layer 4 does not need to think
about backpressure — that's the orchestrator's `asyncio.Queue` job, and
already proven by the streaming work in Waves 6–7.

---

## 10. Versioning — `workspace_item_versions`

### 10.1 Migration 049 (NEW)

```sql
-- shared/db/migrations/049_workspace_item_versions.sql

CREATE TABLE IF NOT EXISTS public.workspace_item_versions (
    version_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id               UUID NOT NULL
                            REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    version_number        INT  NOT NULL,                  -- monotonic per item, starts at 1
    content_md            TEXT NOT NULL,                  -- snapshot BEFORE the edit
    word_count_before     INT  NOT NULL,
    edited_by_agent       TEXT NOT NULL,                  -- "edit.notes" | "edit.search" | "edit.writer"
    edit_caller_id        TEXT NOT NULL,
    edit_instruction      TEXT NOT NULL,
    edit_summary_ar       TEXT NOT NULL,
    edit_kind             TEXT,                            -- factual|tighten|insert|reframe|NULL
    fallback_used         BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key       TEXT,                            -- §11.4
    run_id                UUID REFERENCES public.agent_runs(run_id),
    user_id               UUID NOT NULL,                  -- denormalized for RLS
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uniq_item_version UNIQUE (item_id, version_number),
    CONSTRAINT uniq_item_idempotency UNIQUE (item_id, idempotency_key)
        DEFERRABLE INITIALLY IMMEDIATE   -- only meaningful when key is non-NULL
);

CREATE INDEX IF NOT EXISTS ix_workspace_item_versions_item
    ON public.workspace_item_versions(item_id, version_number DESC);

ALTER TABLE public.workspace_item_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_item_versions_owner_select ON public.workspace_item_versions;
CREATE POLICY workspace_item_versions_owner_select ON public.workspace_item_versions
    FOR SELECT USING (user_id = get_current_user_id());
-- NO INSERT/UPDATE/DELETE policy — service role only.

-- Extend workspace_items with the live version counter.
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS current_version_number INT NOT NULL DEFAULT 1;
```

Note: Postgres `UNIQUE` over a column that allows NULL only enforces
uniqueness for non-NULL values, which is exactly what we want for
`idempotency_key` (analyze calls and old edit callers pass NULL).

### 10.2 Semantics

- **Initial insert** (any producing agent) → `workspace_items.content_md`
  set, `current_version_number = 1`. **No** `workspace_item_versions` row.
- **First edit** → snapshot v1 content into `workspace_item_versions` with
  `version_number=1`, UPDATE `workspace_items.content_md` to the new
  content, bump `current_version_number = 2`.
- **Nth edit** → snapshot (N-1)th live content into
  `workspace_item_versions` with `version_number = N - 1`, UPDATE, bump
  to N.

Reconstruct full history: `SELECT * FROM workspace_item_versions WHERE
item_id = ? ORDER BY version_number ASC`, then append live content as the
final (`current_version_number`) entry.

### 10.3 Migration 050 (group-resolver columns)

Owned by the request-builder source plan but called out here:

```sql
-- shared/db/migrations/050_workspace_items_turn_parent.sql
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS turn_id UUID NULL,
    ADD COLUMN IF NOT EXISTS parent_item_id UUID NULL
        REFERENCES public.workspace_items(item_id);

CREATE INDEX IF NOT EXISTS ix_workspace_items_turn
    ON public.workspace_items(turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_workspace_items_parent
    ON public.workspace_items(parent_item_id) WHERE parent_item_id IS NOT NULL;
```

Verify with `mcp__supabase__list_migrations` before drafting — may
already exist.

---

## 11. Enforcement — `commit_item_revision` + lint

### 11.1 Service function signature

```python
# backend/app/services/workspace_items.py

async def commit_item_revision(
    supabase: SupabaseClient,
    *,
    item_id: str,
    new_content_md: str,
    edited_by_agent: str,           # MUST start with "edit." — defense in depth
    edit_caller_id: str,
    edit_instruction: str,
    edit_summary_ar: str,
    edit_kind: str | None,
    fallback_used: bool,
    user_id: str,
    idempotency_key: str | None = None,
    run_id: str | None = None,
) -> CommitResult:
    """Atomic: snapshot prior content → workspace_item_versions, update
    workspace_items.content_md, bump current_version_number. word_count is
    recomputed by the existing migration-048 trigger.

    Returns CommitResult(version_number, version_id, content_md, was_duplicate).
    Raises ItemNotFoundError or RLSViolationError on scope failures.
    Raises OwnershipViolation if edited_by_agent doesn't start with 'edit.'."""
```

`CommitResult.was_duplicate=True` means an existing row with the same
`(item_id, idempotency_key)` was found — no new write was performed; the
returned `version_number` / `version_id` are from the prior commit.

### 11.2 Atomicity strategy

Single Supabase RPC implemented as a server-side PL/pgSQL function:

```sql
CREATE OR REPLACE FUNCTION public.commit_item_revision_rpc(
    p_item_id          UUID,
    p_new_content_md   TEXT,
    p_edited_by_agent  TEXT,
    p_edit_caller_id   TEXT,
    p_edit_instruction TEXT,
    p_edit_summary_ar  TEXT,
    p_edit_kind        TEXT,
    p_fallback_used    BOOLEAN,
    p_user_id          UUID,
    p_idempotency_key  TEXT,
    p_run_id           UUID
) RETURNS TABLE (version_number INT, version_id UUID, was_duplicate BOOLEAN)
SECURITY DEFINER
LANGUAGE plpgsql AS $$ ... $$;
```

Service-role only. RLS is bypassed via SECURITY DEFINER but the function
explicitly verifies `workspace_items.user_id = p_user_id` and re-checks
the existence of the item before snapshotting.

### 11.3 The three enforcement layers

1. **`commit_item_revision` is the only writer of `workspace_items.content_md`
   post-insert.** Producing agents (`agent_search`, `agent_writer`, `ocr_extractor`)
   still own initial INSERTs — those become version-1 baselines.
2. **`commit_item_revision` is imported only from
   `agents/memory/item_analyzer/`.** CI grep
   (`scripts/lint/forbid_direct_content_md_updates.py`) flags any other
   importer.
3. **No direct `UPDATE workspace_items SET content_md` SQL or
   `.update({"content_md": ...})` exists outside
   `commit_item_revision`'s implementation.** Same CI grep covers this,
   with an allowlist:
   - `backend/app/services/workspace_items.py` (the implementation)
   - `shared/db/migrations/**/*.sql` (data backfills)

### 11.4 Idempotency (NEW enrichment)

> **Enrichment over source plans.** The original plan handled
> `(item_id, version_number)` conflicts by retrying once. That solves
> *concurrent* edits but NOT *retried* edits on transport failure (user
> double-click; orchestrator retry loop). We add an **optional**
> `idempotency_key` so retries are safe.

- Edit callers SHOULD pass a UUID `idempotency_key` per UI action
  (orchestrator generates one per user turn).
- `commit_item_revision` checks `(item_id, idempotency_key)` BEFORE
  attempting the snapshot+update. If a row exists, return its
  `(version_number, version_id, was_duplicate=True)` — no DB mutation,
  no new agent_runs row.
- Runner treats `was_duplicate=True` like a successful commit but
  records a `dup_idempotency=true` Logfire attribute and SKIPS re-emit
  (since the prior call already emitted). The runner returns
  `EditResult(success=True, no_change=False, version_number=…)` so the
  user-facing contract is unchanged.

For backward compat, `idempotency_key=None` retains the original
behavior (snapshot + retry-once on conflict).

---

## 12. Observability — Logfire taxonomy

### 12.1 Span tree per request

```
item_analyzer.{mode}               (top-level — opened by runner)
├── item_analyzer.build_request    (deterministic dispatch)
├── pydantic_ai.run                (auto-instrumented if logfire is configured)
│   └── llm.completion             (per model attempt; FallbackModel adds more)
└── item_analyzer.commit_revision  (edit only)
```

### 12.2 Attribute taxonomy

| Span | Attribute | Type | Required | Notes |
|---|---|---|---|---|
| `item_analyzer.{mode}` | `caller_id` | str | yes | router/writer_planner/deep_search_planner |
| `item_analyzer.{mode}` | `mode` | str | yes | analyze/edit |
| `item_analyzer.{mode}` | `sub_agent_id` | str | yes | analyze.notes / edit.writer / … |
| `item_analyzer.{mode}` | `tier` | str | yes | tier_1/tier_2 |
| `item_analyzer.{mode}` | `target_count_input` | int | yes | from call |
| `item_analyzer.{mode}` | `target_count_resolved` | int | yes | post-resolution |
| `item_analyzer.{mode}` | `group_expanded` | bool | yes | true if any GroupTarget |
| `item_analyzer.{mode}` | `short_circuit` | bool | yes | resolved=0 path |
| `item_analyzer.{mode}` | `fallback_used` | bool | yes | secondary model answered |
| `item_analyzer.{mode}` | `outcome` | str | yes | ok/empty_resolved/llm_failed/conflict/no_change/dup_idempotency |
| `item_analyzer.edit` | `version_number` | int | conditional | set on commit success |
| `item_analyzer.edit` | `version_id` | str | conditional | PK of workspace_item_versions row |
| `item_analyzer.edit` | `no_change` | bool | yes | editor refused |
| `item_analyzer.edit` | `emit_failed` | bool | conditional | set true when post-commit emit raised |
| `item_analyzer.edit` | `dup_idempotency` | bool | conditional | true when key matched prior |
| `item_analyzer.build_request` | (same caller_id/mode + target counts) | | yes | |
| `item_analyzer.commit_revision` | `item_id`, `prev_version`, `new_version`, `idempotency_key_present` | | yes | |

> **Logfire reference**: per the [Pydantic AI logfire docs](https://ai.pydantic.dev/logfire/),
> Pydantic AI auto-instruments agent runs when Logfire is installed and
> configured. We don't need to manually create `pydantic_ai.run` or
> `llm.completion` spans — they appear automatically as children of
> our `item_analyzer.{mode}` parent.

### 12.3 Correlation IDs

`agent_runs.trace_id` + `agent_runs.span_id` already exist
(migration 029, columns 60–61). The runner reads the current Logfire
span at completion and writes both. This means:

- Pivot from `agent_runs.run_id` → Logfire trace.
- Pivot from `workspace_item_versions.run_id` (new column, §10.1) →
  `agent_runs.run_id` → Logfire trace.
- Pivot from `workspace_item_versions.version_id` ↔ user-visible
  «تم تعديل …» message via `EditResult.version_id` returned from runner.

This three-way join makes the audit story tight: every user-visible
edit message has a backing `agent_runs` row, a `workspace_item_versions`
snapshot, and a Logfire trace.

### 12.4 `agent_runs` row attributes

| Column | Value source |
|---|---|
| `agent_family` | `'memory'` |
| `subtype` | `sub_agent_id` (e.g. `"edit.notes"`) |
| `status` | `'ok'` / `'error'` / `'timeout'` |
| `input_summary` | First 200 chars of `call.instruction` |
| `output_item_id` | `item_id` for edit; `NULL` for analyze |
| `tokens_in/out` | From Pydantic AI `result.usage()` |
| `model_used` | `_model_label_from_result(result)` — matches `artifact_summarizer` pattern |
| `per_phase_stats` | `{ "caller_id": …, "tier": …, "group_expanded": …, "target_count_resolved": …, "version_number": …, "fallback_used": …, "no_change": …, "per_tier": {…} }` |
| `error` | Stack trace + message when status != ok |
| `trace_id`, `span_id` | From active Logfire span |

Short-circuit calls (`short_circuit=True`) write NO `agent_runs` row —
no LLM was invoked.

---

## 13. Failure modes (complete table)

| # | Failure | Detected by | Recovery | Tested by |
|---|---|---|---|---|
| 1 | Request builder rejects call shape | `_validate_call_shape` | Raise `AnalyzerCallError` (Arabic) | `test_validation.py::test_edit_rejects_group_target`, … |
| 2 | `extras` payload invalid | Pydantic union validation | Raise `AnalyzerCallError("بيانات الاستدعاء غير صالحة")` | `test_caller_extras.py` |
| 3 | Group resolution returns 0 items | `resolve_targets` | analyze → empty result; edit → `ItemNotFoundError` | `test_request_builder_e2e.py::test_empty_resolution_short_circuits` |
| 4 | Specific target not found / out-of-scope | RLS | analyze drops + logs; edit raises `ItemNotFoundError` | `test_group_resolver.py::test_rls_drops_other_user_items` |
| 5 | Mixed kinds resolved | `build_request` step 4 | Raise `AnalyzerCallError` | `test_validation.py::test_analyze_rejects_mixed_kinds` |
| 6 | Primary LLM HTTP error | `FallbackModel` chain | Fallback advances; `fallback_used=True` if secondary succeeded | `test_fallback_truncation_analyze.py` |
| 7 | Both providers HTTP-fail in analyze | runner | Return `AnalyzeResult(items=truncated_raw, fallback_used=True)` | `test_fallback_truncation_analyze.py::test_double_fail_returns_truncated_views` |
| 8 | Both providers HTTP-fail in edit | runner | NO version row; NO content update; emit «تعذر إجراء التعديل…»; `EditResult(success=False, fallback_used=True)` | `test_both_models_fail_edit.py` |
| 9 | Output schema validation fails | Pydantic AI `retries=1` | One ModelRetry round; if it still fails, `UnexpectedModelBehavior` → caught → falls through to row 7/8 | `test_runner_edit.py::test_invalid_output_after_retry` |
| 10 | Editor returns `no_change=True` | runner branch | No version row; `user_emit(out.edit_summary_ar)` still fires polite refusal; `EditResult(success=True, no_change=True)` | `test_no_change_path.py::test_no_change_skips_version_write_but_emits` |
| 11 | `(item_id, version_number)` unique violation | `commit_item_revision` | Retry once with next current_version_number; second failure emits «تعذر التعديل بسبب تعارض متزامن» | `test_version_conflict_retry.py::test_retry_succeeds` |
| 12 | `user_emit` raises after commit | runner step 6 | Log + Logfire `emit_failed=true`; `EditResult.success=True` returned (DB is canonical) | `test_runner_edit.py::test_emit_failure_does_not_undo_commit` |
| 13 | `commit_item_revision` called with `edited_by_agent` not starting with `"edit."` | service | Raise `OwnershipViolation` | `test_ownership_guard.py::test_commit_rejects_non_edit_agent_id` |
| 14 | CI grep flags direct `content_md` write outside allowlist | `scripts/lint/forbid_direct_content_md_updates.py` | CI fails | `test_ownership_guard.py::test_lint_flags_direct_content_md_update` |
| 15 | Idempotency-key replay (same key, same item) | `commit_item_revision` | Return prior version_id/version_number; `was_duplicate=True`; runner skips emit; Logfire `dup_idempotency=true` | `test_idempotency.py::test_duplicate_key_returns_prior_version` |
| 16 | edit + attachment kind | `build_request` step 5 (post-resolution edit guard) | Raise `AnalyzerCallError("لا يمكن تعديل مرفقات المستخدم")` | `test_validation.py::test_edit_attachment_rejected_post_resolution` |
| 17 | edit + GroupTarget | `_validate_call_shape` | Raise `AnalyzerCallError` | `test_validation.py::test_edit_rejects_group_target` |
| 18 | edit + len(targets)>1 | `_validate_call_shape` | Raise `AnalyzerCallError` | `test_validation.py::test_edit_rejects_multiple_specific_targets` |

---

## 14. Sub-agent factories — common builder

> **Mirrors `artifact_summarizer._build_summarizer_agent` exactly.** Same
> hard-constraint principle: identical model / model_settings / retries /
> usage_limits across all seven sub-agents. The only knobs are
> `instructions` and `output_type`.

```python
# agents/memory/item_analyzer/_common.py
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from agents.utils.agent_models import get_agent_model

ITEM_ANALYZER_LIMITS = UsageLimits(
    output_tokens_limit=20_000,
    request_limit=2,        # one ModelRetry + one fallback hop
)

def _build_item_analyzer_agent(
    *,
    instructions: str,
    output_type: type[BaseModel],
    tier: Literal["tier_1", "tier_2"],
    name: str,              # e.g. "item_analyzer.edit.notes"
) -> Agent[None, BaseModel]:
    model = get_agent_model("item_analyzer", tier_override=tier)
    return Agent(
        model,
        name=name,
        output_type=output_type,
        instructions=instructions,
        model_settings={"extra_body": {"enable_thinking": True}},
        retries=1,
    )
```

The 7 factories (`create_notes_analyzer`, …, `create_writer_editor`)
are 3-line shims that fill in the right `instructions`, `output_type`,
and `name`. They live in `analyzers/` and `editors/` per the source
plan's manifest.

### 14.1 Dynamic system-prompt note (NEW enrichment)

> Per the [Pydantic AI Agents docs](https://ai.pydantic.dev/agent/) and
> the `@agent.system_prompt(dynamic=True)` decorator, system prompts CAN
> be computed at run-time per `RunContext`. We deliberately do NOT use
> dynamic system prompts here. Our prompts are baked into the rendered
> `instructions=...` at agent-construction time (one factory call per
> request) because:
> 1. The request builder has already pre-composed everything per call.
> 2. Caching opportunities (Anthropic prompt caching, OpenAI prefix
>    caching) are maximized when `instructions` is identical for every
>    call to the same `(mode, kind)` pair — which it is.
> 3. Per-call variability lives in the **user message**, not the system
>    prompt. This is the standard Pydantic AI factory-per-request
>    pattern documented in the docs.

### 14.2 Message-history shape

> Per the [Pydantic AI messages docs](https://ai.pydantic.dev/message-history/),
> `Agent.run(..., message_history=[...])` accepts a list of
> `ModelMessage` objects. The request builder loads `N=3` rows from
> `messages` (Luna's own conversation table) and the runner converts
> them via `_history_to_pai(history)`:

```python
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

def _history_to_pai(history: list[ConversationMessageView]) -> list[ModelMessage]:
    out: list[ModelMessage] = []
    for msg in history:
        if msg.role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
        else:
            out.append(ModelResponse(parts=[TextPart(content=msg.content)]))
    return out
```

Critical per the docs: **tool calls and returns must remain paired** if
you slice. Our history only contains `user` and `assistant` plain-text
messages (no tool calls — these are conversation messages, not prior
agent runs), so we are immune to that hazard.

---

## 15. Tools / external integrations

The sub-agents themselves expose **no Pydantic AI tools**. They are
pure structured-output agents: the LLM sees the rendered prompt and
emits a JSON object matching the per-sub-agent output schema.

External integrations live at the runner / service level:

| Integration | Surface | Purpose |
|---|---|---|
| Supabase Postgres | `deps.supabase` | Read `workspace_items` / `messages`; call `commit_item_revision_rpc` |
| Pydantic AI FallbackModel | Via `get_agent_model("item_analyzer", tier_override=…)` | LLM execution |
| Pydantic Logfire | Via `shared/observability.get_logfire()` | Span instrumentation |
| Layer-2 SSE writer | Via `deps.user_emit` callable | Stream Arabic «تم …» messages |
| `agents/utils/agent_models.cost_usd` | Indirect | Cost calc folded into `agent_runs.cost_usd` (migration 036) |

No new tools, no new external HTTP services, no new dependencies in
`pyproject.toml` / `requirements.txt`.

---

## 16. Test plan

Organized by test pyramid level. All tests live under
`agents/memory/item_analyzer/tests/` unless noted otherwise.

### 16.1 Unit — TestModel (fast, deterministic)

> Per the [Pydantic AI testing docs](https://ai.pydantic.dev/testing/),
> `TestModel` auto-generates structured output that satisfies the schema.
> Use `agent.override(model=TestModel())` context-manager. Fast.

| File | Test | Asserts |
|---|---|---|
| `test_models.py` | `test_edit_output_base_validators_reject_inconsistent` | `no_change=True` + non-empty `new_content_md` → ValidationError |
| `test_models.py` | `test_analyzer_call_caller_mode_aligned_validator` | `caller_id='router', mode='edit'` + `RouterAnalyzeExtras` → ValidationError |
| `test_models.py` | `test_extras_discriminator_dict_input` | dict input routed correctly; missing tag raises |
| `test_dispatch_table.py` | `test_every_pair_routes_to_spec` | All 7 `(mode, kind)` keys present; no extras |
| `test_dispatch_table.py` | `test_no_attachment_editor_registered` | `("edit","attachment")` NOT in registry |
| `test_validation.py` | (rows 1, 16-18 from §13) | every rejection path |
| `test_caller_extras.py` | `test_router_edit_kind_validated` | `edit_kind='banana'` rejected |
| `test_caller_extras.py` | `test_writer_planner_edit_mode_rejected` | `(writer_planner, edit)` → `AnalyzerCallError` |
| `test_user_message_renderers.py` | `test_each_renderer_snapshot` | 7 renderers × canonical `RenderContext` → snapshot match |

### 16.2 Unit — FunctionModel (verbatim preservation)

> Per the docs, `FunctionModel` lets you write custom Python that
> generates the model response. Use it whenever a TestModel auto-gen
> would obscure intent — esp. verbatim assertions.

| File | Test | Asserts |
|---|---|---|
| `test_runner_analyze.py` | `test_analyze_attachment_preserves_facts_verbatim` | `FunctionModel` emits `AttachmentAnalyzeOutput`; runner returns it; each `ExtractedFact.verbatim_span` MUST appear unchanged in the input `content_md` |
| `test_runner_analyze.py` | `test_analyze_search_preserves_chunk_ids` | every `ChunkRef.chunk_id` returned by analyzer matches one in the source WI |
| `test_runner_edit.py` | `test_edit_writer_preserves_factual_anchors` | configurable test: `FunctionModel` returns edited content; assert party names from prompt appear in `new_content_md` |
| `test_runner_edit.py` | `test_invalid_output_after_retry` | `FunctionModel` returns malformed JSON twice → falls through to `AnalyzeResult.fallback_used=True` / `EditResult.success=False` |

### 16.3 Integration — DB + runner + emit

Uses a Supabase test schema (testcontainer or transactional fixture).

| File | Test | Asserts |
|---|---|---|
| `test_runner_edit.py` | `test_edit_writer_commits_version_and_updates_content` | `workspace_item_versions` row at `version_number=1` (before-image); `workspace_items.content_md` updated; `current_version_number=2`; `EditResult.version_id` matches inserted row |
| `test_runner_edit.py` | `test_edit_emits_arabic_summary_after_commit` | DB row visible BEFORE `user_emit` is called (use `Mock` with side-effect that queries DB) |
| `test_runner_edit.py` | `test_emit_failure_does_not_undo_commit` | `user_emit` raises; commit still visible; `EditResult.success=True`; Logfire span carries `emit_failed=true` |
| `test_no_change_path.py` | `test_no_change_skips_version_write_but_emits` | `no_change=True` → 0 new rows in `workspace_item_versions`; `user_emit` invoked once; `EditResult(success=True, no_change=True)` |
| `test_analyze_silent.py` | `test_analyze_never_calls_user_emit` | `deps.user_emit = Mock()`; analyze runs; mock never called; assert that deps.user_emit MUST be None per the runner contract (assertion error if not) |
| `test_edit_revision_chain.py` | `test_three_edits_produce_three_versions` | 3 sequential edits → versions table has `version_number=1,2,3`; `workspace_items.current_version_number=4` |
| `test_version_conflict_retry.py` | `test_retry_succeeds` | mock `commit_item_revision` to raise `UniqueViolationError` once, then succeed; runner returns `EditResult(success=True)` |
| `test_version_conflict_retry.py` | `test_double_conflict_emits_apology` | mock to raise twice; runner emits «تعذر التعديل بسبب تعارض متزامن»; `EditResult(success=False)` |
| `test_idempotency.py` | `test_duplicate_key_returns_prior_version` | call edit twice with same `idempotency_key`; second returns same `version_id`; only ONE `workspace_item_versions` row exists; second `user_emit` NOT invoked |
| `test_both_models_fail_edit.py` | `test_no_version_written_on_double_fail` | both providers raise `ModelHTTPError`; 0 new version rows; `content_md` unchanged; apology emitted; `EditResult(success=False, fallback_used=True)` |
| `test_fallback_truncation_analyze.py` | `test_double_fail_returns_truncated_views` | analyze double-fail → `AnalyzeResult(fallback_used=True)` with per-item truncated raw content |
| `test_commit_item_revision.py` | `test_atomic_snapshot_update_counter` | snapshot + content update + counter bump all visible in same TX; partial-failure scenarios verified |

### 16.4 Tier inheritance

| File | Test | Asserts |
|---|---|---|
| `test_tier_inheritance.py` | `test_writer_planner_call_uses_tier_1_chain` | `AnalyzerCall(tier='tier_1')` → `get_agent_model('item_analyzer', tier_override='tier_1')` invoked; first model in chain is `qwen3.6-plus` |
| `test_tier_inheritance.py` | `test_router_call_uses_tier_2_chain` | `AnalyzerCall(tier='tier_2')` → first model is `qwen3.5-flash` |
| `test_tier_inheritance.py` | `test_get_agent_model_tier_override_mutex_with_override` | passing both `override` and `tier_override` → ValueError |

### 16.5 Lint / ownership

| File | Test | Asserts |
|---|---|---|
| `test_ownership_guard.py` | `test_commit_rejects_non_edit_agent_id` | `commit_item_revision(edited_by_agent="rogue")` → `OwnershipViolation` |
| `test_ownership_guard.py` | `test_lint_flags_direct_content_md_update` | synthetic file with `.update({"content_md": …})` outside allowlist → grep script exits non-zero |
| `test_ownership_guard.py` | `test_lint_allowlists_service_module` | same write inside `backend/app/services/workspace_items.py` → grep script exits 0 |

### 16.6 End-to-end request builder matrix

| File | Test | Asserts |
|---|---|---|
| `test_request_builder_e2e.py` | `test_analyze_search_group_end_to_end` | writer_planner `Group(kind='agent_search', scope='conversation')` → `ResolvedRequest(sub_agent_id='analyze.search', resolved_items=[3 rows], group_expanded=True, short_circuit=False)` |
| `test_request_builder_e2e.py` | `test_edit_writer_specific_end_to_end` | router edit on specific writer WI → `sub_agent_id='edit.writer'`, single resolved item, `edit_kind` in rendered prompt |
| `test_request_builder_e2e.py` | `test_empty_resolution_short_circuits` | all target item_ids missing → `short_circuit=True, resolved_items=[]` |
| `test_request_builder_e2e.py` | `test_kind_translation_db_to_py` | DB row with `kind='agent_writing'` resolves to `TargetKind='agent_writer'` dispatch path |

---

## 17. Open questions for the user

Tried to resolve all from the source plans. These are the **few** I could
not resolve unambiguously — please confirm before build wave 4.

1. **Kind-name translation `note` vs `notes` and `agent_writing` vs
   `agent_writer`.** §6.3 proposes a single `_KIND_DB_TO_PY` mapping in
   `dispatch.py`. Confirm this is the right boundary (vs renaming the
   DB enum, which would cascade into a lot of existing code).

2. **Idempotency key origin.** §11.4 adds an optional `idempotency_key`.
   The orchestrator should generate a UUID per user turn (or per
   "edit" button click). Confirm the orchestrator is the right owner of
   this key, vs. the router passing through a UI-supplied key.

3. **Per-call cost-tracking accuracy when `tier_override` flips tier.**
   The `_SUBAGENT_TIER` map (§8) declares all 7 sub-agents `tier_1` by
   default. Per-call telemetry uses the *actual* tier (from
   `per_phase_stats.per_tier`). Confirm dashboards consume `per_tier`,
   not the default map — otherwise cost rollups under-count router
   calls (which run tier_2).

4. **Migration 049 PK on the unique constraint with NULL `idempotency_key`.**
   Postgres `UNIQUE(item_id, idempotency_key)` only constrains rows
   where both columns are non-NULL. For idempotency callers who pass
   NULL, the constraint is effectively no-op (which is what we want).
   Confirm this matches DB conventions in Luna (vs adding a partial
   unique index `WHERE idempotency_key IS NOT NULL`).

5. **Writer_planner's `fetch_items → analyze` migration.** Source plan
   §"Migration from the old `fetch_items` API" defers the actual edit
   to the writer_planner plan's follow-up list. Confirm the build
   order in §18 below keeps this dependency as a final cross-plan
   step (NOT blocking the item_analyzer family ship).

---

## 18. Build order

The Pydantic AI builder pipeline consumes this INITIAL.md in waves. Each
wave is one "@pydantic-ai-*" agent invocation; waves are sequential.

1. **Wave 1 — request builder skeleton** (this plan's `request_builder.md` source)
   - Migration 050 (`workspace_items.turn_id` + `parent_item_id`) — verify via `mcp__supabase__list_migrations` first.
   - `models.py` (call types, target union, extras discriminated union, RenderContext, AnalyzeResult/EditResult, errors).
   - `dispatch.py` (SUB_AGENT_REGISTRY with `NotImplementedError` factories, EXTRAS_TAGS, `_validate_call_shape`, `_KIND_DB_TO_PY`).
   - `group_resolver.py`, `history.py`.
   - `user_message_renderers/_common.py` + 7 per-sub-agent renderers + snapshot tests.
   - `request_builder.py::build_request`.
   - All builder-level tests (`test_dispatch_table.py`, `test_validation.py`, `test_group_resolver.py`, `test_history_loader.py`, `test_caller_extras.py`, `test_user_message_renderers.py`, `test_request_builder_e2e.py`).

2. **Wave 2 — DB + service layer**
   - Migration 049 (`workspace_item_versions` + `workspace_items.current_version_number` + idempotency unique constraint + `run_id` FK).
   - PL/pgSQL `commit_item_revision_rpc`.
   - `backend/app/services/workspace_items.py::commit_item_revision` + `test_commit_item_revision.py`.
   - `scripts/lint/forbid_direct_content_md_updates.py` + hook into `.github/workflows/ci.yml`.

3. **Wave 3 — model slot + deps**
   - Extend `agents/utils/agent_models.py`: add `"item_analyzer": ModelPolicy("tier_1")`; add `tier_override` keyword to `get_agent_model`; extend `_SUBAGENT_TIER` defensive map; `test_tier_inheritance.py`.
   - `deps.py` (`AnalyzerDeps` dataclass + `build_analyzer_deps` factory).

4. **Wave 4 — prompts + sub-agent factories** (invokes `pydantic-ai-prompt-engineer`)
   - 7 Arabic system prompts under `prompts/`.
   - `_common.py::_build_item_analyzer_agent` private builder.
   - 4 analyzer factories + 3 editor factories — replace `dispatch.py` `NotImplementedError` stubs.

5. **Wave 5 — runner** (invokes `pydantic-ai-dependency-manager`)
   - `runner.py::analyze` (silent path + truncated-raw fallback).
   - `runner.py::edit` (no-change + version-conflict retry + double-fail apology + emit-after-commit + idempotency).
   - `_record_run`, `_commit_with_retry`, `_truncated_raw_fallback` helpers.

6. **Wave 6 — full test pyramid** (invokes `pydantic-ai-validator`)
   - All §16 tests not yet written.
   - Integration tests against a Supabase test schema.

7. **Wave 7 — orchestrator wiring** (invokes `luna-wiring`)
   - Stamp `turn_id` + `parent_item_id` on every WI INSERT.
   - Router-edit dispatch path constructs `AnalyzerDeps(user_emit=create_layer2_emitter(...))`; all other dispatch paths pass `user_emit=None`.
   - Generate per-turn `idempotency_key` on the orchestrator side.

8. **Wave 8 (cross-plan, separate sprint) — migrate `fetch_items`**
   - `agents/agent_writer/planner/tools.py::fetch_items` → `item_analyzer.analyze` shim per source plan's migration table. Owned by the writer_planner plan's build list.

---

## 19. Success criteria

- [ ] All 7 sub-agents respond to `analyze` / `edit` calls with valid schema-matched output, end-to-end through the runner.
- [ ] `workspace_item_versions` table receives a before-image row on every successful edit; `current_version_number` increments by 1.
- [ ] `commit_item_revision` is the sole writer of `workspace_items.content_md` post-insert (verified by CI grep + integration test).
- [ ] Router-driven edits stream Arabic «تم …» messages via the existing SSE pipeline; the user sees them in chat exactly as assistant messages today.
- [ ] Tier inheritance verified: writer_planner / deep_search_planner calls use tier_1 chain; router calls use tier_2 chain.
- [ ] Idempotency: repeated edit calls with the same key produce one version row and one emission.
- [ ] Three concurrency tests pass: (a) two concurrent edits, second retries and succeeds; (b) idempotency replay returns prior version; (c) `user_emit` failure after commit logs `emit_failed=true` and returns success.
- [ ] Every analyze-mode call with `deps.user_emit != None` raises an assertion (defense in depth) — analyzers never leak text to users.
- [ ] Every edit-mode call with `deps.user_emit is None` raises an assertion.
- [ ] All §16 tests pass.
- [ ] Logfire dashboards display the three-way join `EditResult.version_id ↔ workspace_item_versions.run_id ↔ agent_runs.trace_id`.

---

## 20. Assumptions made

- The Luna `messages` table schema and RLS policy (from Wave 4) supports the `load_history` query as written. If not, builder must add a service-role read path.
- The Layer-2 SSE emitter helper (`create_layer2_emitter`) already exists in the orchestrator and emits plain message events that the frontend renders as assistant chat. (Source plan asserts this.)
- Supabase has a function `get_current_user_id()` (verified in `017_rls_fix_users_authuid.sql`) for RLS predicates on migration 049.
- All Pydantic AI invocations use the same `request_limit=2` ceiling. Reasoning-heavy edit cases stay well under `output_tokens_limit=20_000`; if telemetry shows breaches, bump the limit before adding streaming.
- The orchestrator generates `idempotency_key` per user turn and passes it through the router. If absent, runners behave as the source plans specify (retry-once on conflict, no replay protection).
- Saudi-legal-Arabic prompts will preserve party names, dates, and amounts verbatim — enforced by FunctionModel-based tests (§16.2), not by tooling.
- The `note` / `notes` and `agent_writing` / `agent_writer` mapping uses a single `_KIND_DB_TO_PY` translator (§6.3); confirmed in §17 open questions.

---

**End of INITIAL_item_analyzer_family.md** —
generated by `pydantic-ai-planner`, 2026-05-24.
