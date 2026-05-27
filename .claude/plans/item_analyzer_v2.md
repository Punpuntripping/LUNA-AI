# Plan — Item Analyzer v2 (analyze + edit, family fan-out, 3-state verdict)

> **Supersedes** four earlier plans (see § 14). Same problem, smaller surface:
>
> - 2 top-level agent packages — `item_analyzer` (silent Layer-4 librarian) and
>   `item_analyzer_editor` (Layer-4 writer with Layer-2 dep injection).
> - Per-caller prompt sub-dirs. Adding a caller = drop a folder.
> - One call per caller turn. Runner partitions by output-family
>   (refs-kinds vs meta-kinds) and fans out into at most 2 LLM calls.
> - Per-WI verdict is 3-state: `full` / `partial` / `none`. Caller does
>   the unfolding; analyzer never returns full content.
>
> **v2 scope**: analyze in full, writer-planner as the only wired caller.
> Editor is a stub section (§ 8) deepened in v2.1. Router and deep_search
> callers are sketched but not implemented.

---

## 1. Position and ownership

| | |
|---|---|
| Layer | **Layer 4 Memory** — sits alongside `artifact_summarizer` and `ocr_extractor` per `feedback_layer_vs_tier`. |
| Tier | **tier_2** — fixed. Verdicts are short structured outputs; tier_2 (qwen3.5-flash / deepseek-v4-flash) is plenty. Re-evaluate only if verdict quality metrics fall short. |
| Ownership rule | After a `workspace_items` row's initial insert, **only `item_analyzer_editor` mutates `content_md`**. All other agents are read-only on post-release WIs. Enforced via service-layer + CI lint (§ 8). |
| User talk | `item_analyzer` (analyze) — never. `item_analyzer_editor` (edit) — emits a short Arabic acknowledgment via a Layer-2 SSE emitter injected into deps. |

---

## 2. Two-package layout

```
agents/memory/
  item_analyzer/                ← ANALYZE — Layer 4, silent librarian
    __init__.py                 ← re-exports: analyze, AnalyzerCall,
                                  AnalyzerDeps, build_analyzer_deps,
                                  AnalyzeOutput, WIVerdict
    deps.py                     ← AnalyzerDeps + build_analyzer_deps
    models.py                   ← AnalyzerCall, RefsVerdict*, MetaVerdict*,
                                  WIVerdict, RefsAnalyzeOutput,
                                  MetaAnalyzeOutput, AnalyzeOutput,
                                  WorkspaceItemRow, AnalyzerError
    runner.py                   ← analyze() — load WIs, partition,
                                  fan-out, merge
    agent.py                    ← create_refs_analyzer(caller_id) and
                                  create_meta_analyzer(caller_id) —
                                  share _build_analyzer() inner builder
                                  (mirrors artifact_summarizer/agent.py
                                  house pattern)
    prompt_registry.py          ← PROMPT_REGISTRY + USER_MSG_RENDERERS
                                  (the only file you edit to add a caller)
    writer/                     ← writer_planner caller — THIS sprint
      __init__.py
      prompts/
        __init__.py
        refs_kinds.py           ← ANALYZE_REFS_FOR_WRITER_SYSTEM_AR
                                  + render_refs_user_msg(query, wis)
        meta_kinds.py           ← ANALYZE_META_FOR_WRITER_SYSTEM_AR
                                  + render_meta_user_msg(query, wis)
    router/                     ← later sprint (empty)
    deep_search/                ← later sprint (empty)
    tests/
      __init__.py
      test_partition.py         ← family bucketing matches user's
                                  matrix (S+W=1 call, S+attach=2 calls, etc.)
      test_runner.py            ← end-to-end analyze() with TestModel
                                  per family
      test_three_state.py       ← each verdict variant parses + maps to
                                  planner unfold action
      test_prompt_registry.py   ← (caller_id, family) lookup; missing pair → error

  item_analyzer_editor/         ← EDIT — Layer 4, only WI-writer post-publish
    __init__.py                 ← v2.1 stub — exports: edit, EditorCall,
                                  EditorDeps, build_editor_deps, EditOutput
    deps.py                     ← EditorDeps (incl. user_emit injection)
    models.py                   ← EditorCall, EditOutput
    runner.py                   ← edit() — commit version, emit Arabic ack
    agent.py                    ← create_editor(caller_id)
    prompt_registry.py
    router/                     ← only caller for now
      prompts/
        edit.py                 ← EDIT_FOR_ROUTER_SYSTEM_AR
    # full spec in § 8 (stub); implementation in v2.1
```

This mirrors the existing `agents/memory/artifact_summarizer/` house pattern
(`deps.py` / `agent.py` / `runner.py` / `models.py` / `prompts.py`). The only
new convention is **caller sub-dirs** containing the prompt module per family.

---

## 3. The call surface

```python
# agents/memory/item_analyzer/models.py

class AnalyzerCall(BaseModel):
    """Caller-facing request. Two fields, nothing else."""
    query: str               # the planner's question, verbatim — what's THIS WI to me?
    targeted_wi: list[str]   # 1+ item_ids; runner partitions by kind family
```

That's the whole call. No `mode` (mode = which package you import).
No `caller_id` (resolved from deps). No `user_id` / `conversation_id`
(inherited from deps).

```python
# agents/memory/item_analyzer/deps.py

from dataclasses import dataclass
from typing import Any, Literal

CallerId = Literal["router", "writer_planner", "deep_search_planner"]


@dataclass
class AnalyzerDeps:
    """Injected by the caller's runner. The analyzer LLM never sees user_id /
    conversation_id directly — they're used only for RLS-scoped Supabase reads."""
    supabase: Any
    http_client: Any
    user_id: str
    conversation_id: str
    caller_id: CallerId       # routed to PROMPT_REGISTRY at agent-build time
    logger: Any | None = None  # mirrors artifact_summarizer.deps.ArtifactSummaryDeps


def build_analyzer_deps(
    *, supabase, http_client, user_id, conversation_id, caller_id, logger=None,
) -> AnalyzerDeps:
    return AnalyzerDeps(
        supabase=supabase, http_client=http_client,
        user_id=user_id, conversation_id=conversation_id,
        caller_id=caller_id, logger=logger,
    )
```

The caller's runner builds these once per turn with `caller_id="writer_planner"`
(or whichever caller it is) hardcoded. The LLM never sees this — it goes into
prompt selection inside `create_family_analyzer`, not into a tool argument.

---

## 4. The three-state verdict shape

Two families, each with three variants discriminated by `need`:

```python
# agents/memory/item_analyzer/models.py

from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field

# === REFS family — content carries [n] reference tokens ===

class RefsVerdictFull(BaseModel):
    """The entire content_md matters — caller unfolds the whole WI."""
    need: Literal["full"]
    item_id: str
    kind: Literal["agent_search", "agent_writer"]
    rational: str                 # Arabic — answers query for THIS WI

class RefsVerdictPartial(BaseModel):
    """Only part of the WI matters — `distilled` is the relevant slice
    (Arabic), `refs_needed` lists additional [n] tokens to resolve via
    references_service."""
    need: Literal["partial"]
    item_id: str
    kind: Literal["agent_search", "agent_writer"]
    distilled: str                # Arabic, analyzer-written
    refs_needed: list[int]        # may be empty if distilled alone suffices
    rational: str

class RefsVerdictNone(BaseModel):
    """Irrelevant — caller drops this WI."""
    need: Literal["none"]
    item_id: str
    kind: Literal["agent_search", "agent_writer"]
    rational: str                 # «غير ذي صلة لأن…»

RefsVerdict = Annotated[
    Union[RefsVerdictFull, RefsVerdictPartial, RefsVerdictNone],
    Field(discriminator="need"),
]


# === META family — prose/markdown, no [n] refs ===

class MetaVerdictFull(BaseModel):
    need: Literal["full"]
    item_id: str
    kind: Literal["attachment", "notes"]
    rational: str

class MetaVerdictPartial(BaseModel):
    """Both `distilled` and `extracted_metadata` may be populated. Per D1:
    extracted_metadata covers structured facts (parties, dates, amounts);
    distilled covers the prose slice that matters. Use both when both apply."""
    need: Literal["partial"]
    item_id: str
    kind: Literal["attachment", "notes"]
    distilled: str | None = None
    extracted_metadata: dict[str, str] = Field(default_factory=dict)
    rational: str

class MetaVerdictNone(BaseModel):
    need: Literal["none"]
    item_id: str
    kind: Literal["attachment", "notes"]
    rational: str

MetaVerdict = Annotated[
    Union[MetaVerdictFull, MetaVerdictPartial, MetaVerdictNone],
    Field(discriminator="need"),
]


# === Per-family structured outputs the LLM returns ===

class RefsAnalyzeOutput(BaseModel):
    items: list[RefsVerdict]
    overall_rational: str | None = None  # cross-item strategic note, optional

class MetaAnalyzeOutput(BaseModel):
    items: list[MetaVerdict]
    overall_rational: str | None = None


# === Caller-facing merged result ===

WIVerdict = Annotated[
    Union[RefsVerdict, MetaVerdict],
    Field(discriminator="kind"),  # kind value uniquely picks the family branch
]

class AnalyzeOutput(BaseModel):
    """What the caller receives from analyze()."""
    query_echo: str
    items: list[WIVerdict]    # one entry per resolvable targeted_wi, input order
    overall_rational: str | None = None  # merged from both sub-runs (newline-joined)
```

**Note on the outer discriminator**: the merged `WIVerdict` discriminator is
`kind` (not `need`) — each `kind` value (`agent_search`/`agent_writer` vs
`attachment`/`notes`) uniquely identifies the family branch, then the inner
union discriminates on `need`. Pydantic v2 handles two-level discriminators
natively.

---

## 5. The runner — partition + fan-out + merge

```python
# agents/memory/item_analyzer/runner.py

import time
import logging
from typing import Sequence

from shared.observability import get_logfire

from .agent import (
    create_refs_analyzer,
    create_meta_analyzer,
    ANALYZER_LIMITS,
)
from .deps import AnalyzerDeps
from .models import (
    AnalyzerCall,
    AnalyzeOutput,
    WIVerdict,
    WorkspaceItemRow,
    AnalyzerError,
)
from .prompt_registry import render_refs_user_msg, render_meta_user_msg

logger = logging.getLogger(__name__)
_logfire = get_logfire()

REFS_KINDS = {"agent_search", "agent_writer"}
META_KINDS = {"attachment", "notes"}


async def analyze(call: AnalyzerCall, deps: AnalyzerDeps) -> AnalyzeOutput:
    """Layer-4 analyze entrypoint. One call → 0/1/2 LLM calls → merged AnalyzeOutput.

    Best-effort: silently drops out-of-scope item_ids, returns an empty result
    if everything was dropped. Never raises for caller-recoverable conditions.
    """
    t0 = time.perf_counter()
    with _logfire.span(
        "item_analyzer.analyze",
        caller_id=deps.caller_id,
        targeted_count=len(call.targeted_wi),
        query_chars=len(call.query),
    ) as span:
        wis = await _load_workspace_items(
            deps.supabase, call.targeted_wi, user_id=deps.user_id,
        )
        # RLS already scoped the SELECT — anything missing was out-of-scope.
        dropped = [iid for iid in call.targeted_wi if iid not in {w.item_id for w in wis}]
        if dropped:
            logger.warning("item_analyzer: dropped %d out-of-scope ids: %s", len(dropped), dropped)

        refs_wis = [w for w in wis if w.kind in REFS_KINDS]
        meta_wis = [w for w in wis if w.kind in META_KINDS]
        other = [w for w in wis if w.kind not in REFS_KINDS | META_KINDS]
        if other:
            # Defense-in-depth: unsupported kinds shouldn't reach here.
            raise AnalyzerError(
                "أنواع غير مدعومة في الاستدعاء: " + ", ".join(w.kind for w in other)
            )

        span.set_attributes({
            "refs_count": len(refs_wis),
            "meta_count": len(meta_wis),
            "dropped_count": len(dropped),
        })

        verdicts: list[WIVerdict] = []
        overall_chunks: list[str] = []

        if refs_wis:
            verdicts_r, overall_r = await _run_refs_family(call, deps, refs_wis)
            verdicts.extend(verdicts_r)
            if overall_r:
                overall_chunks.append(overall_r)

        if meta_wis:
            verdicts_m, overall_m = await _run_meta_family(call, deps, meta_wis)
            verdicts.extend(verdicts_m)
            if overall_m:
                overall_chunks.append(overall_m)

        # Re-order to match input for caller predictability
        verdicts.sort(key=lambda v: call.targeted_wi.index(v.item_id))

        span.set_attributes({
            "verdict_full_count": sum(1 for v in verdicts if v.need == "full"),
            "verdict_partial_count": sum(1 for v in verdicts if v.need == "partial"),
            "verdict_none_count": sum(1 for v in verdicts if v.need == "none"),
            "duration_s": round(time.perf_counter() - t0, 3),
        })

        return AnalyzeOutput(
            query_echo=call.query,
            items=verdicts,
            overall_rational="\n\n".join(overall_chunks) or None,
        )


async def _run_refs_family(
    call: AnalyzerCall, deps: AnalyzerDeps, wis: Sequence[WorkspaceItemRow],
) -> tuple[list[WIVerdict], str | None]:
    """One LLM call against the refs prompt for the deps.caller_id."""
    agent = create_refs_analyzer(deps.caller_id)
    user_msg = render_refs_user_msg(
        caller_id=deps.caller_id, query=call.query, wis=wis,
    )
    try:
        result = await agent.run(user_msg, usage_limits=ANALYZER_LIMITS)
    except Exception as exc:
        logger.warning("item_analyzer.refs: LLM failed (%s) — returning all 'none'", exc)
        return _none_verdicts_for(wis, kind_family="refs", rational="تعذّر التحليل"), None
    out = result.output
    _record_run(deps, "item_analyzer.refs", call, result, wi_count=len(wis))
    return list(out.items), out.overall_rational


async def _run_meta_family(
    call: AnalyzerCall, deps: AnalyzerDeps, wis: Sequence[WorkspaceItemRow],
) -> tuple[list[WIVerdict], str | None]:
    """One LLM call against the meta prompt for the deps.caller_id."""
    agent = create_meta_analyzer(deps.caller_id)
    user_msg = render_meta_user_msg(
        caller_id=deps.caller_id, query=call.query, wis=wis,
    )
    try:
        result = await agent.run(user_msg, usage_limits=ANALYZER_LIMITS)
    except Exception as exc:
        logger.warning("item_analyzer.meta: LLM failed (%s) — returning all 'none'", exc)
        return _none_verdicts_for(wis, kind_family="meta", rational="تعذّر التحليل"), None
    out = result.output
    _record_run(deps, "item_analyzer.meta", call, result, wi_count=len(wis))
    return list(out.items), out.overall_rational
```

**Key properties:**

| | |
|---|---|
| **LLM call count** | `0` if all targets dropped/missing · `1` if all in one family · `2` if both families present |
| **Cost attribution** | Each family run writes a distinct `agent_runs` row (`subtype="item_analyzer.refs"` vs `"item_analyzer.meta"`) so cost dashboards can split per family |
| **Failure** | Per-family LLM failure degrades that family's WIs to all-`none` verdicts (silently logged). The other family still runs. Caller still gets a valid `AnalyzeOutput` — it just sees those WIs as "irrelevant" |
| **Empty input** | `targeted_wi=[]` → no SELECT, no LLM, `AnalyzeOutput(items=[], overall_rational=None)` |
| **Out-of-scope ids** | Silently dropped + logged (RLS already filtered them). Caller-side responsibility to triage before calling |

---

## 6. The factory + prompt registry

```python
# agents/memory/item_analyzer/agent.py

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .deps import CallerId
from .models import RefsAnalyzeOutput, MetaAnalyzeOutput
from .prompt_registry import (
    refs_prompt_for_caller,
    meta_prompt_for_caller,
)


ANALYZER_LIMITS = UsageLimits(
    output_tokens_limit=32_000,   # distilled slices can be lengthy when the
                                  # source WI is dense (e.g. multi-clause
                                  # contracts, long research artifacts); 32k
                                  # is the tier_2 ceiling we want to use
    request_limit=2,              # 1 retry max
)


def _build_analyzer(instructions: str, output_type) -> Agent:
    """House-pattern inner builder — same shape as artifact_summarizer/agent.py.
    All analyzer instances share this config; only prompt + output type vary."""
    return Agent(
        get_agent_model("item_analyzer"),
        name="item_analyzer",
        output_type=output_type,
        instructions=instructions,
        retries=1,
    )


def create_refs_analyzer(caller_id: CallerId) -> Agent:
    return _build_analyzer(
        instructions=refs_prompt_for_caller(caller_id),
        output_type=RefsAnalyzeOutput,
    )


def create_meta_analyzer(caller_id: CallerId) -> Agent:
    return _build_analyzer(
        instructions=meta_prompt_for_caller(caller_id),
        output_type=MetaAnalyzeOutput,
    )
```

```python
# agents/memory/item_analyzer/prompt_registry.py

from typing import Callable, Sequence

from .deps import CallerId
from .models import WorkspaceItemRow
from .writer.prompts.refs_kinds import (
    ANALYZE_REFS_FOR_WRITER_SYSTEM_AR,
    render_refs_user_msg as _render_refs_writer,
)
from .writer.prompts.meta_kinds import (
    ANALYZE_META_FOR_WRITER_SYSTEM_AR,
    render_meta_user_msg as _render_meta_writer,
)


_REFS_PROMPTS: dict[CallerId, str] = {
    "writer_planner": ANALYZE_REFS_FOR_WRITER_SYSTEM_AR,
    # "router": ANALYZE_REFS_FOR_ROUTER_SYSTEM_AR,        # later
    # "deep_search_planner": ANALYZE_REFS_FOR_DSP_SYSTEM_AR,  # later
}

_META_PROMPTS: dict[CallerId, str] = {
    "writer_planner": ANALYZE_META_FOR_WRITER_SYSTEM_AR,
    # later: router, deep_search_planner
}

_REFS_RENDERERS: dict[CallerId, Callable] = {
    "writer_planner": _render_refs_writer,
}

_META_RENDERERS: dict[CallerId, Callable] = {
    "writer_planner": _render_meta_writer,
}


def refs_prompt_for_caller(caller_id: CallerId) -> str:
    try:
        return _REFS_PROMPTS[caller_id]
    except KeyError:
        raise NotImplementedError(
            f"refs prompt not registered for caller_id={caller_id!r}"
        )


def meta_prompt_for_caller(caller_id: CallerId) -> str:
    try:
        return _META_PROMPTS[caller_id]
    except KeyError:
        raise NotImplementedError(
            f"meta prompt not registered for caller_id={caller_id!r}"
        )


def render_refs_user_msg(
    *, caller_id: CallerId, query: str, wis: Sequence[WorkspaceItemRow],
) -> str:
    return _REFS_RENDERERS[caller_id](query=query, wis=wis)


def render_meta_user_msg(
    *, caller_id: CallerId, query: str, wis: Sequence[WorkspaceItemRow],
) -> str:
    return _META_RENDERERS[caller_id](query=query, wis=wis)
```

**Adding a new caller is one diff**: drop `router/prompts/refs_kinds.py` +
`meta_kinds.py`, then add 4 lines to `prompt_registry.py` (2 prompts +
2 renderers).

---

## 7. Writer-planner caller — end-to-end

The writer-planner already exists per `.claude/plans/writer_planner.md`.
This section specifies how it calls the analyzer.

### 7.1 Replacing the old `fetch_items`

```python
# agents/agent_writer/planner/tools.py — REPLACE the existing fetch_items body

@agent.tool
async def analyze_items(
    ctx: RunContext[WriterPlannerDeps],
    query: str,
    targeted_wi: list[str],
) -> AnalyzeOutput:
    """Ask the item analyzer to verdict each WI against this query.

    Returns one verdict per resolvable WI:
      - need='full'      → unfold the entire content_md into the WriterPackage
      - need='partial'   → use the distilled slice (+ resolve refs_needed via
                            references_service for refs-family WIs,
                            or use extracted_metadata for meta-family WIs)
      - need='none'      → drop the WI; do NOT include in the WriterPackage

    rational / overall_rational are planner-facing — they shape plan_md but
    never reach the writer.
    """
    from agents.memory.item_analyzer import analyze, build_analyzer_deps, AnalyzerCall

    deps = build_analyzer_deps(
        supabase=ctx.deps.supabase,
        http_client=ctx.deps.http_client,
        user_id=ctx.deps.user_id,
        conversation_id=ctx.deps.conversation_id,
        caller_id="writer_planner",
    )
    return await analyze(AnalyzerCall(query=query, targeted_wi=targeted_wi), deps)
```

The old `fetch_items` is removed entirely. The 1000-word threshold gating
moves into the analyzer's prompt as a soft heuristic: "if the whole WI is
small and relevant, return `full`; otherwise prefer `partial` with a
focused `distilled`."

### 7.2 Planner unfold semantics (caller side)

After `analyze_items()` returns, the planner builds the WriterPackage by
walking each verdict:

| `kind` family | `need` | Planner action | Resulting block in WriterPackage |
|---|---|---|---|
| refs | `full` | Read `workspace_items.content_md` directly | `<source kind="agent_search\|agent_writer" item_id="..." source="raw">{content_md}</source>` |
| refs | `partial` | Embed `distilled` as the body; if `refs_needed`, fetch via `references_service.fetch_item_references` filtered to those `n` and embed below | `<source item_id="..." source="distilled">{distilled}\n\n<refs>{resolved}</refs></source>` |
| refs | `none` | Skip entirely | (not in package) |
| meta | `full` | Read `content_md` directly | `<source kind="attachment\|notes" item_id="..." source="raw">{content_md}</source>` |
| meta | `partial` | Embed `distilled` (if present) + `extracted_metadata` as a key/value block | `<source item_id="..." source="distilled"><facts>{kv}</facts>{distilled}</source>` |
| meta | `none` | Skip entirely | (not in package) |

`rational` / `overall_rational` feed the planner's `present_plan_for_approval`
plan_md — they describe WHY each WI matters in human-readable Arabic. They
never enter WriterPackage.

### 7.3 End-to-end call flow

```
writer_planner.runner            item_analyzer.runner                LLM(s)
─────────────────────            ────────────────────                ──────
1. router pre-selected
   attached_items[]; planner
   triages via summary

2. analyze_items tool fires
   query="هذا العقد يحتاج..."
   targeted_wi=[S1, W2, A3]   ──▶ 3. load_workspace_items(...)
                                   refs_bucket=[S1, W2]
                                   meta_bucket=[A3]

                                4. create_refs_analyzer("writer_planner")
                                   render_refs_user_msg(...) ─────▶  refs LLM
                                                                     RefsAnalyzeOutput
                                                                  ◀──
                                   record_run("item_analyzer.refs", ...)

                                5. create_meta_analyzer("writer_planner")
                                   render_meta_user_msg(...) ─────▶  meta LLM
                                                                     MetaAnalyzeOutput
                                                                  ◀──
                                   record_run("item_analyzer.meta", ...)

                                6. merge → AnalyzeOutput
                            ◀───  items=[S1(full), W2(partial,
                                  distilled+refs[3,7]), A3(none)]

7. planner builds WriterPackage:
   - S1 raw content
   - W2 distilled + fetch refs [3,7]
   - A3 dropped

8. present_plan_for_approval(plan_md)
   plan_md built from rationals
   → SSE stream to user → user approves

9. handle_writer_turn(WriterPackage, deps)
```

The analyzer's 2 LLM calls are invisible to the planner. They're observable
only via the 2 `agent_runs` rows and the family-tagged Logfire spans.

---

## 8. Editor agent — `item_analyzer_editor/` (stub for v2.1)

Carried forward conceptually from the prior plan; fully specified in v2.1.
Recap of what survives:

- **Separate package** `agents/memory/item_analyzer_editor/` — different
  output schema, different concurrency story, different invocation
  pattern (1 WI per call, never batched).
- **Single sub-agent per caller** (no family fan-out — edit operates on
  one concrete WI of one kind). v2.1 ships only the `router/` caller.
- **Ownership invariant** — only `commit_item_revision` writes
  `content_md` post-insert, enforced by:
  1. service-layer chokepoint in `backend/app/services/workspace_items.py`
  2. CI grep lint `scripts/lint/forbid_direct_content_md_updates.py`
  3. `edited_by_agent` must start with `"edit."`
- **Versioning** — `workspace_item_versions` table holds before-images;
  atomic snapshot + content update + counter bump in one transaction;
  1-retry on `(item_id, version_number)` conflict.
- **User emission** — `EditorDeps.user_emit` injected by orchestrator on
  the router-edit dispatch path only; emit AFTER DB commit (crash-safe);
  no `user_emit` in any other dispatch path.
- **`no_change=True`** outcome — editor refuses politely, no version
  written, user_emit still fires the polite acknowledgment.

v2.1 will replace this section with the full spec. v2 ships only the
package skeleton (`__init__.py` + a `NotImplementedError`-raising
`edit()`) so the writer-planner sprint doesn't accidentally take a
dependency on it.

---

## 9. Tier and cost tracking

```python
# agents/utils/agent_models.py — ONE-LINE ADDITION

AGENT_MODELS: dict[str, ModelPolicy] = {
    ...
    "item_analyzer": ModelPolicy("tier_2", primary="deepseek"),
    "item_analyzer_editor": ModelPolicy("tier_2", primary="deepseek"),  # v2.1
}
```

Tier_2 / DeepSeek-primary matches `artifact_summarizer` — same tier-4 memory
family, same fast-cheap profile. Verdicts are short structured outputs;
qwen3.5-flash and deepseek-v4-flash both handle this easily.

`agent_runs` rows per analyze() invocation:

| Field | Value |
|---|---|
| `agent_family` | `'memory'` |
| `subtype` | `'item_analyzer.refs'` or `'item_analyzer.meta'` (one row per LLM call) |
| `input_item_ids` | the WI ids in this family's bucket |
| `output_item_id` | `NULL` (analyzer produces no WI) |
| `tokens_in / tokens_out / tokens_reasoning` | from `result.usage()` |
| `per_phase_stats` | `{"caller_id": "...", "family": "refs\|meta", "wi_count": N, "verdict_counts": {"full": ?, "partial": ?, "none": ?}}` |

Cost dashboards can split refs vs meta and per caller via these fields.

---

## 10. Observability

| Span | Attributes |
|---|---|
| `item_analyzer.analyze` | `caller_id`, `targeted_count`, `query_chars`, `refs_count`, `meta_count`, `dropped_count`, `verdict_full_count`, `verdict_partial_count`, `verdict_none_count`, `duration_s` |
| `item_analyzer.refs` | `caller_id`, `wi_count`, `model_used`, `tokens_in`, `tokens_out`, `tokens_reasoning`, `fallback_used`, `duration_s` |
| `item_analyzer.meta` | (same shape as refs) |

The outer `item_analyzer.analyze` span wraps both family runs; cost-per-call
is the sum of the two inner span tokens.

---

## 11. Failure modes

| Failure | Behavior |
|---|---|
| `targeted_wi=[]` | No SELECT, no LLM. `AnalyzeOutput(items=[])`. |
| Some/all ids out of scope | Silently dropped + logged WARNING. Remaining ids processed. |
| WI kind unsupported (shouldn't happen — defense in depth) | `AnalyzerError` raised with Arabic message. |
| Refs LLM fails (after `retries=1` exhaustion) | Refs WIs get all-`none` verdicts with `rational="تعذّر التحليل"`. Meta family still runs normally. |
| Meta LLM fails | Symmetric — meta WIs get all-`none`, refs runs normally. |
| Both LLMs fail | All WIs get `none` verdicts. Caller treats every WI as irrelevant — planner will likely re-present or ask the user. |
| `caller_id` not in PROMPT_REGISTRY | `NotImplementedError` at agent-build time — programmer bug. |

---

## 12. File manifest

### NEW

```
agents/memory/item_analyzer/
  __init__.py
  deps.py
  models.py
  runner.py
  agent.py
  prompt_registry.py
  writer/
    __init__.py
    prompts/
      __init__.py
      refs_kinds.py        ← ANALYZE_REFS_FOR_WRITER_SYSTEM_AR
                              + render_refs_user_msg(query, wis) → str
      meta_kinds.py        ← ANALYZE_META_FOR_WRITER_SYSTEM_AR
                              + render_meta_user_msg(query, wis) → str
  tests/
    __init__.py
    test_partition.py            ← family bucketing matrix
    test_runner.py               ← analyze() E2E with TestModel per family
    test_three_state.py          ← verdict variant parsing + planner mapping
    test_prompt_registry.py      ← (caller, family) lookup; unknown → NotImplementedError
    test_failure_paths.py        ← LLM-fail per family → all-'none' for that family
    test_workspace_loader.py     ← RLS scoping + missing-id drop behavior

agents/memory/item_analyzer_editor/
  __init__.py                    ← v2.1 stub — raises NotImplementedError
                                     for any call
```

### MODIFIED

```
agents/agent_writer/planner/tools.py
  ~ Replace fetch_items() body with analyze_items() that wraps
    agents.memory.item_analyzer.analyze. Old tool name and behavior gone.

agents/agent_writer/planner/runner.py / models.py
  ~ AnalyzedItem (per writer_planner.md) gains a `need` field
    ("full"|"partial"|"none") and renames `text_md` → `body_md` for
    clarity; the planner populates body_md from content_md (full) or
    from verdict.distilled (partial). Drops the source='raw|distilled'
    field — that information is now in `need`.

agents/agent_writer/prompts.py
  ~ build_writer_user_message_from_package — render the new <source> /
    <facts> block shape from § 7.2.

agents/utils/agent_models.py
  + AGENT_MODELS["item_analyzer"] = ModelPolicy("tier_2", primary="deepseek")
  + AGENT_MODELS["item_analyzer_editor"] = ModelPolicy("tier_2", primary="deepseek")

shared/observability.py
  (no change — uses existing get_logfire())
```

### ARCHIVED (renamed `<name>.archived.md`)

```
.claude/plans/item_analyzer.md
.claude/plans/item_analyzer_request_builder.md
agents/plans/INITIAL_item_analyzer_family.md
agents/plans/PROTOCOL_item_analyzer_callers.md
```

Each archived file gets a header line: `> SUPERSEDED by .claude/plans/item_analyzer_v2.md (2026-05-25)`.

---

## 13. Build order

1. **`agents/memory/item_analyzer/models.py`** — every type from § 3 + § 4.
   Discriminated unions, no logic. Unit-test that each verdict variant
   round-trips JSON (`test_three_state.py`).

2. **`agents/memory/item_analyzer/deps.py`** — `AnalyzerDeps` + builder. Trivial.

3. **`agents/memory/item_analyzer/writer/prompts/refs_kinds.py`** —
   `ANALYZE_REFS_FOR_WRITER_SYSTEM_AR` (Arabic system prompt teaching the
   3-state verdict semantics for refs-family WIs) +
   `render_refs_user_msg(query, wis)` (XML/markdown block per WI with
   `item_id`, `kind`, `title`, `word_count`, `content_md`).

4. **`agents/memory/item_analyzer/writer/prompts/meta_kinds.py`** — symmetric
   for meta-family.

5. **`agents/memory/item_analyzer/prompt_registry.py`** — registry dicts +
   resolver functions + render wrappers. Add the lookup-miss
   `NotImplementedError` paths.

6. **`agents/memory/item_analyzer/agent.py`** — `_build_analyzer` inner
   builder + `create_refs_analyzer` / `create_meta_analyzer` + `ANALYZER_LIMITS`.

7. **`agents/memory/item_analyzer/runner.py`** — `analyze()` + the family
   runners + workspace_items loader. Span instrumentation throughout.

8. **`agents/utils/agent_models.py`** — register `item_analyzer` slot.

9. **`agents/memory/item_analyzer/__init__.py`** — public re-exports.

10. **Tests**: `test_partition`, `test_runner`, `test_failure_paths`,
    `test_workspace_loader`, `test_prompt_registry`. All using
    `pydantic_ai.models.test.TestModel` and `FunctionModel`.

11. **`agents/memory/item_analyzer_editor/__init__.py`** — v2.1 stub raising
    `NotImplementedError("item_analyzer_editor not yet implemented — see v2.1")`.

12. **Caller wiring**: `agents/agent_writer/planner/tools.py` — replace
    `fetch_items` with `analyze_items` per § 7.1. Update
    `agents/agent_writer/planner/models.py::AnalyzedItem`,
    `runner.py::_build_writer_package` (translate verdicts → analyzed_items),
    `agents/agent_writer/prompts.py::build_writer_user_message_from_package`
    (new block shape).

13. **Smoke test**: end-to-end via writer-planner — 3 attachments + 1 search,
    verify 2 LLM calls fire, WriterPackage assembled correctly per verdict
    states, plan_md uses rational text.

14. **Archive the 4 prior plans** (rename + header line).

---

## 14. Test plan

| Test | Covers |
|---|---|
| `test_partition.py::test_one_search_one_writer_yields_one_call` | `targeted_wi=[S, W]` → 1 LLM call (refs family), 0 meta calls. |
| `test_partition.py::test_one_search_one_attach_yields_two_calls` | `[S, attach]` → 1 refs + 1 meta call. |
| `test_partition.py::test_one_search_one_writer_one_attach_yields_two_calls` | `[S, W, attach]` → 1 refs (with [S,W]) + 1 meta. |
| `test_partition.py::test_notes_only_yields_one_meta_call` | `[N, N]` → 0 refs + 1 meta. |
| `test_partition.py::test_empty_input_yields_zero_calls` | `[]` → no LLM, empty result. |
| `test_runner.py::test_analyze_refs_writer_planner_e2e` | TestModel returns `RefsAnalyzeOutput` with one of each variant; verdicts mirror in `AnalyzeOutput.items` in input order. |
| `test_runner.py::test_analyze_meta_writer_planner_e2e` | Same for meta family. |
| `test_runner.py::test_mixed_families_merge_preserves_input_order` | `targeted_wi=[S, A, W]` → resulting items keyed back to that order. |
| `test_runner.py::test_overall_rational_concatenated` | Both families return `overall_rational`; merged with `\n\n` separator. |
| `test_runner.py::test_overall_rational_omitted_when_both_none` | Both return None → final `overall_rational=None`. |
| `test_three_state.py::test_full_variant_round_trips` | `RefsVerdictFull` and `MetaVerdictFull` parse from JSON the LLM would emit. |
| `test_three_state.py::test_partial_refs_has_distilled_and_refs_needed` | Validation: `partial` refs requires `distilled` (non-empty string); refs_needed may be empty. |
| `test_three_state.py::test_partial_meta_allows_both_fields` | `MetaVerdictPartial` with both `distilled` and `extracted_metadata` populated parses cleanly. |
| `test_three_state.py::test_none_variant_drops_extra_fields` | LLM accidentally emits `refs_needed` on a `none` variant → discriminator picks `RefsVerdictNone`, extra fields ignored by Pydantic. |
| `test_prompt_registry.py::test_writer_planner_refs_registered` | `refs_prompt_for_caller("writer_planner")` returns the writer-specific prompt. |
| `test_prompt_registry.py::test_unknown_caller_raises` | `refs_prompt_for_caller("router")` → `NotImplementedError`. |
| `test_failure_paths.py::test_refs_llm_fail_degrades_to_none` | Refs LLM raises → all refs WIs become `RefsVerdictNone`; meta runs normally. |
| `test_failure_paths.py::test_meta_llm_fail_degrades_to_none` | Symmetric. |
| `test_failure_paths.py::test_both_llms_fail_returns_all_none` | All WIs `none`; `overall_rational=None`. |
| `test_workspace_loader.py::test_rls_drops_other_user_items` | User A asks for B's item_id → silently dropped, warning logged. |
| `test_workspace_loader.py::test_missing_id_dropped` | Non-existent item_id → dropped, no error. |
| `test_workspace_loader.py::test_returns_only_needed_columns` | SELECT pulls `item_id, kind, title, content_md, word_count` only — confirms no over-fetch. |

`agents/agent_writer/planner/tests/` gets its own follow-up tests for the
verdict→WriterPackage mapping owned by the planner sprint (covered in
`writer_planner.md`'s test plan, not here).

---

## 15. What this supersedes

The following plans are **archived** when this lands (rename to
`<name>.archived.md`, prepend a `> SUPERSEDED by item_analyzer_v2.md (2026-05-25)` header):

| File | Why archived |
|---|---|
| `.claude/plans/item_analyzer.md` | Defined 7 sub-agents (4 analyze + 3 edit) routed via a dispatch table. v2 collapses to 2 packages + family fan-out. |
| `.claude/plans/item_analyzer_request_builder.md` | Defined the dispatcher in front of the 7 sub-agents. With only 2 sub-agent factories per package (refs + meta), the dispatcher is just an `if kind in REFS_KINDS` branch in the runner — no separate module needed. |
| `agents/plans/INITIAL_item_analyzer_family.md` | Pydantic-AI INITIAL.md derived from the 7-sub-agent design. Drift target removed. |
| `agents/plans/PROTOCOL_item_analyzer_callers.md` | Protocol doc for the 7-sub-agent caller interface (4 caller-family output schemas, EXTRAS_TAGS dispatch). Replaced by the single `AnalyzerCall(query, targeted_wi)` interface in § 3 + per-caller prompt dirs in § 2.

### What changed structurally

| Old | New |
|---|---|
| 7 sub-agents (4 analyze + 3 edit) | 2 packages × per-caller prompt dirs (writer-planner first) |
| Dispatch table `SUB_AGENT_REGISTRY[(mode, kind)]` | Family partition in runner: `kind in REFS_KINDS` |
| `EXTRAS_TAGS` per (caller, mode) | `PROMPT_REGISTRY[caller_id]` per family |
| `CALLER_EXTRAS_SCHEMA` Pydantic validation | Caller fills `AnalyzerDeps.caller_id`; the LLM never sees a caller field |
| 4 caller-family output schemas | 1 caller-agnostic output schema per family (refs / meta) |
| 2-state verdict implied (relevant vs not) | 3-state verdict: `full` / `partial` / `none` |
| Per-call same-kind invariant; callers batch per kind | Mixed kinds in one call; runner partitions internally |
| Tier inheritance via `tier_override` param on `get_agent_model` | Fixed at tier_2 (defer override until evidence shows it's needed) |
| Group selectors (`conversation` / `turn` / `parent_artifact`) | Caller passes concrete `targeted_wi: list[str]` — they're closer to the relevance signal anyway |
| `user_id` / `conversation_id` in call surface | Inherited from `AnalyzerDeps` |
| Edit lives in same package, dispatched by mode | Edit moves to separate `item_analyzer_editor` package |

### What stays

- Layer-4 placement (analyze silent; edit talks via Layer-2 dep injection)
- Ownership invariant — `commit_item_revision` is sole writer of `content_md`
- `workspace_item_versions` for edit history (carried into v2.1)
- CI grep lint to enforce the ownership rule
- `references_service` reuse for refs unfolding (caller-side, not analyzer-side)
- `agent_runs` cost tracking, now per family

---

## 16. Dependencies

- `agents/utils/agent_models.py` — add `item_analyzer` slot (one line).
- `backend/app/services/references_service.py` — already exists; used by the
  writer-planner (not by the analyzer) to unfold `refs_needed` after `partial`
  verdicts.
- `shared/db/migrations/048_workspace_items_word_count.sql` — already applied;
  `word_count` is rendered into the user message so the LLM can size its
  verdict ("if this whole WI is small + on-topic, return `full`").
- `agents/memory/item_analyzer_editor/` v2.1 plan — separate doc; nothing in
  v2 depends on it (the stub raises `NotImplementedError`).
- No new migrations for v2. Edit's `workspace_item_versions` migration lands
  with v2.1.

---

## 17. Open follow-ups (out of v2 scope)

1. **Router and deep_search callers** — drop `router/prompts/` and
   `deep_search/prompts/` folders with their refs+meta prompts; add 4 lines
   to `prompt_registry.py`. Per-caller plans own their own prompt design
   conversation.
2. **Tier override** — if writer-planner telemetry shows verdict quality
   plateaus on tier_2 for complex refs-family items, add a `tier_override`
   param on `AnalyzerCall` (or on deps) and pipe through to
   `get_agent_model`.
3. **Streaming verdicts** — out of scope. Verdicts are short structured
   outputs; planner needs the whole verdict before acting. If a future
   UX wants to show the analyzer "thinking", surface via Logfire spans, not
   streamed structured output.
4. **Cross-conversation `targeted_wi`** — v2 RLS scopes loader by `user_id`
   only, so cross-convo WI ids would technically resolve. The writer-planner
   never calls with cross-convo ids today; if a future caller needs it,
   verify behavior + add a test.
5. **Editor v2.1** — full spec for `item_analyzer_editor` covering: single-WI
   edit input, `EditOutput` (new content + Arabic ack + `no_change`),
   `commit_item_revision` service, `workspace_item_versions` migration,
   user_emit injection, ownership lint, retry-on-conflict, no_change path.
6. **Editor caller expansion** — v2.1 wires only router-edit. Adding
   writer-planner-edit or deep_search-edit later = drop a caller folder +
   register prompts.
