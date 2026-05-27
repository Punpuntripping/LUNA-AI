# Agent Communication Protocol — Passing Workspace Items & References

**Scope.** How Luna's LLM-driven agents (router, planner, writer, aggregator) refer to workspace items and individual references when passing structured output to each other and to the orchestrator. The protocol's job is to keep **UUIDs off the LLM I/O surface** without inventing a new short-token system.

**Status.** Migration 052 (added `workspace_items.wi_seq`) lands the durable foundation. The router and planner prompt/output validators that consume it are the immediate next step.

---

## TL;DR

| What is being identified | Handle the LLM emits | Backing column | Scope of stability |
|---|---|---|---|
| A workspace item | `WI-{seq}` (e.g. `WI-3`) | `workspace_items.wi_seq` | Per conversation, stable across turns |
| One reference inside a WI | `n` (e.g. `[3]`) | `workspace_item_references.n` | Per WI, immutable |
| One reference across WIs | `(WI-{seq}, n)` tuple | composite | Per conversation |

UUIDs (`workspace_items.item_id`, `workspace_item_references.item_id`) stay on the **orchestrator** side of the wall. Validators on every LLM-emitted output and every tool call resolve aliases → UUIDs before any DB read or downstream agent invocation.

---

## Why this exists

Before migration 052, the router and planner both:

1. **Injected raw UUIDs into the prompt** — e.g. router emitted `- item_id={36-char-uuid} | kind=agent_search | title=…` for each WI; planner emitted `<prior_search item_id="{36-char-uuid}">…</prior_search>`.
2. **Required the LLM to copy those UUIDs back** in structured output — `DispatchAgent.attached_item_ids: list[UUID]`, `DispatchAgent.target_item_id: UUID`, `PlannerResponse.referenced_item_id: UUID`, plus every `read_workspace_item(item_id)` tool call.
3. **Defended in prose** — the planner system prompt literally says «ولا تُجرّب item_id لا تعرفه» ("don't try an item_id you don't know"). A prose rule is the brittle version of a structural guarantee.

Models hallucinate UUIDs. They transpose hex digits, drop dashes, and occasionally invent plausible-looking ones from scratch. The fix is structural: hand the model a short integer alias and resolve it on the way back.

---

## The two identifiers

### `WI-{seq}` — workspace item alias

- **Where it comes from:** `workspace_items.wi_seq` (migration 052). Auto-assigned by a `BEFORE INSERT` trigger that picks `MAX(wi_seq) + 1` within the row's `conversation_id` (under a `pg_advisory_xact_lock` keyed on conversation_id to make concurrent inserts safe).
- **Render format:** uppercase `WI-` prefix followed by the integer — `WI-1`, `WI-2`, … `WI-12`. The prefix is mandatory in both directions so it can't be confused with a ref `n` or any other small integer in the prompt.
- **Stability:** stable for the lifetime of the conversation. A WI keeps its seq across turns, edits, soft-deletes (soft-deleted items hold their seq slot). Backfilled rows were numbered per-conversation in `created_at` order at migration time.
- **Scope:** conversation-local. `WI-3` in conversation A is a different item than `WI-3` in conversation B. The orchestrator never lets the LLM see WIs from another conversation, so cross-conversation collisions are unreachable.
- **Uniqueness:** `UNIQUE(conversation_id, wi_seq)` enforced by partial index.

### `n` — reference citation number

- **Where it comes from:** `workspace_item_references.n`. Computed at publish time by `agents/deep_search_v4/aggregator/preprocessor.py::preprocess_references`. Walks `ura.high_results` then `ura.medium_results` and assigns `n = 1, 2, 3, …` in iteration order.
- **Render format:** square-bracketed inline citation: `[3]`, `[3,7]`. The aggregator validator (`_CITATION_RE = re.compile(r"\[(\d+(?:\s*[,،]\s*\d+)*)\]")`) treats both ASCII and Arabic commas as separators.
- **Stability:** immutable once the WI is published. `UNIQUE(wi_id, n)` at the DB layer; the publisher writes the full set in one batch.
- **Scope:** local to one WI. `n=3` in `WI-7` is a different reference than `n=3` in `WI-8`.

---

## What each agent sees and emits

### Router (Layer 1)

**Sees, in its dynamic instructions:**

```
- WI-1 | kind=agent_search | title="حقوق العامل في فترة التجربة"
  summary: ملخّص الإجابة العامة على حقوق العامل خلال فترة التجربة...
- WI-2 | kind=note | title="ملاحظات على القضية"
  summary: ...
```

**Emits — UUIDs are NOT in any LLM output field. Instead the model emits aliases that the orchestrator validates:**

| Output field | Type the LLM sees | Type after validation |
|---|---|---|
| `DispatchAgent.attached_wis: list[str]` | List of `"WI-{seq}"` strings | Validator converts to `list[UUID]` for downstream use |
| `DispatchAgent.target_wi: str \| None` | `"WI-{seq}"` or `None` | Validator converts to `UUID \| None` |
| `read_workspace_item(wi: str)` tool | `"WI-{seq}"` (or `UUID` for backward compat) | Tool resolver accepts either; resolves to UUID before fetch |

**Resolver behavior:** an alias that doesn't match any current `(conversation_id, wi_seq)` row raises an explicit validation error («العنصر WI-X غير موجود») — the planner's prose defense becomes a hard fail.

### Planner (deep_search, Layer 2)

**Sees, in its decider XML blocks:**

```xml
<prior_searches>
  <prior_search wi="WI-1" confidence="high">
    <title>حقوق العامل في فترة التجربة</title>
    <describe_query>...</describe_query>
    <summary>...</summary>
  </prior_search>
</prior_searches>

<attached_items>
  <attached_item wi="WI-2" kind="note">
    <title>ملاحظات على القضية</title>
    <content_md>...</content_md>
  </attached_item>
</attached_items>
```

**Emits — same alias contract:**

| Output field | LLM-side type | Post-validation |
|---|---|---|
| `PlannerResponse.referenced_wi: str \| None` | `"WI-{seq}"` or `None` | Converted to `referenced_item_id: UUID \| None` |
| `read_workspace_item(wi: str)` tool call | `"WI-{seq}"` | Resolved to UUID before fetch |

The decider/responder prompts drop the «ولا تُجرّب item_id لا تعرفه» rule in favour of a structural guarantee: the validator refuses any alias that isn't in the planner's input.

### Writer planner / executor (Layer 2 / Layer 3) — picking refs

**Default case (single WI in scope, which covers ~80% of writer flows):** the planner is already operating on its current WI by orchestrator context — no WI handle needed in the LLM output at all. The model emits just `n`:

```json
{ "refs_to_cite": [3, 7, 12] }
```

The orchestrator pre-resolves these to fully-loaded `Reference` rows via:

```sql
SELECT * FROM workspace_item_references
WHERE wi_id = $current_wi_uuid AND n = ANY($1)
```

…joins to source tables via `references_service.fetch_item_references`, and hands the writer the full grounding content. The writer never types a UUID.

**Multi-WI case (planner draws refs from multiple WIs in the conversation):** use the `(WI-{seq}, n)` tuple:

```json
{
  "picks": [
    { "wi": "WI-3", "n": 5 },
    { "wi": "WI-3", "n": 8 },
    { "wi": "WI-7", "n": 2 }
  ]
}
```

The orchestrator runs the alias→UUID resolver on `wi`, then one query per WI to pull the refs.

### Aggregator (Layer 3)

The aggregator **does not emit identifiers at all** — its only ID-shaped output is the inline `[n]` markers in `synthesis_md`, which are validated against the pre-numbered reference list by `extract_cited_numbers`. References themselves are stamped with `n` by code (`preprocess_references`) and never named by the LLM. This stays unchanged.

### Memory layer (Layer 4)

Memory agents (`item_analyzer`, summarisers, compactors) work on a single target WI by orchestrator context and never reference siblings by alias. No change.

---

## Resolution: how aliases become UUIDs

Two layers, both in `agents/`:

### 1. Output validators (Pydantic)

Each agent's structured-output model owns a validator that resolves aliases via deps:

```python
class DispatchAgent(BaseModel):
    attached_wis: list[str]  # ["WI-3", "WI-7"]
    target_wi: str | None    # "WI-3" or None
    # …
    # After agent.run() returns, the orchestrator calls:
    #   resolved = resolve_wi_aliases(output, alias_map=deps.wi_alias_map)
    # which returns a parallel dict with item_id UUIDs filled in.
```

The validator is intentionally NOT a `field_validator` inside the model — the alias map isn't available at parse time. Resolution happens immediately after `agent.run()` in the agent's runner, with full access to deps.

### 2. Tool resolver

`read_workspace_item(wi: str)` and any future WI-scoped tool accepts both alias and raw UUID for safety:

```python
def _resolve_wi(wi: str, deps: AgentDeps) -> str:
    """Return the item_id UUID. Accepts 'WI-{n}' alias or a raw UUID."""
    s = wi.strip()
    if s.startswith("WI-"):
        seq = int(s[3:])
        item_id = deps.wi_alias_map.get(seq)
        if not item_id:
            raise ToolRetry(f"العنصر {s} غير موجود في هذه المحادثة")
        return item_id
    # Legacy / orchestrator-supplied UUID — accept verbatim.
    if _is_uuid(s):
        return s
    raise ToolRetry(f"المعرف {s} غير صالح")
```

`ToolRetry` (Pydantic AI built-in) lets the model see the error and self-correct on the next call instead of crashing the run.

### 3. Alias map per turn

Both router and planner deps now carry:

```python
@dataclass
class WiAliasMap:
    """{seq: item_id} for every WI in the agent's conversation context."""
    by_seq: dict[int, UUID]
    by_uuid: dict[UUID, int]   # reverse, for rendering

    @classmethod
    def from_workspace_items(cls, rows: list[dict]) -> WiAliasMap: ...
```

Built once by the context loader (`router/context.py::load_router_context`, `planner` deps builder). Carries the same set of WIs the prompt rendered, so by construction every alias the model sees can be resolved.

---

## Edge cases

**Soft-deleted items.** They keep their `wi_seq` slot. Context loaders filter `deleted_at IS NULL` for prompt rendering, so the LLM doesn't see them — but if a stale tool call somehow references one, the resolver returns "not found" rather than silently fetching tombstone content.

**Concurrent inserts.** Handled by `pg_advisory_xact_lock(hashtext('workspace_items_seq:' || conversation_id))` inside the trigger. Two parallel publishes to the same conversation serialise on the lock and get different seqs.

**Conversation move / forking.** Not currently supported. If we ever let users fork or migrate a WI to a different conversation, the seq becomes ambiguous — design has to be revisited then.

**Items with `conversation_id IS NULL`.** Don't get a `wi_seq`. They're unreachable from router/planner anyway (case-only items, system items). The partial unique index excludes them.

**LLM emits an unknown alias.** Validator raises an Arabic error («العنصر WI-X غير موجود») which is surfaced via Pydantic AI's `ToolRetry` for tool calls and via a hard structured-output validation failure otherwise. The model can self-correct (tool calls) or the orchestrator falls back to its existing error envelope (structured output).

**Cross-conversation references.** Not allowed by design. The alias map is conversation-scoped. A planner trying to reference `WI-3` from a different conversation simply gets "not found" because the resolver only looks at items in the current conversation.

---

## DB columns this protocol depends on

| Column | Migration | Purpose |
|---|---|---|
| `workspace_items.wi_seq` | 052 | The integer behind `WI-{seq}` |
| `workspace_items.item_id` | 026 (rename from `artifact_id`) | Canonical UUID, orchestrator-side only |
| `workspace_item_references.n` | 049 | The integer behind `[n]` |
| `workspace_item_references.item_id` | 050 | Canonical UUID of the source row (chunks_v2/cases/services) |
| `workspace_item_references.ref_id` | 050 | URA-emitted text id, forensic fallback |

`UNIQUE(workspace_items.conversation_id, wi_seq)` and `UNIQUE(workspace_item_references.wi_id, n)` guarantee the alias→UUID resolver is always single-valued.

---

## What this protocol intentionally does NOT do

- **Doesn't invent a new short-token mapping table.** `n` and `wi_seq` are the natural minimal handles. Anything more (random base32 codes, hashes, etc.) adds a mapping layer with no semantic payoff.
- **Doesn't try to make refs globally unique.** `n` is per-WI on purpose — collisions across WIs are impossible because the resolver always knows which WI's `n` it's reading (either from context or from a `(WI-{seq}, n)` tuple).
- **Doesn't migrate the URA layer's `ref_id` format.** `reg:<uuid>`, `case:<case_ref>`, `compliance:<hash>` stay as the URA-internal handles. They never reach the LLM I/O surface (the aggregator already only emits `[n]`), so they're not part of this protocol.
- **Doesn't try to validate `n` against the WI's actual reference set at LLM-emit time.** The Aggregator validator already does that against the synthesis body; downstream consumers of a `(WI-{seq}, n)` tuple resolve via the table, so a bad `n` just returns no row (caller decides how to handle).

---

## Implementation status

| Surface | Status |
|---|---|
| `workspace_items.wi_seq` column + trigger + backfill | ✅ Migration 052 applied |
| Protocol doc (this file) | ✅ |
| Router context loader includes `wi_seq` | ⏳ Pending |
| Router prompt renders `WI-{seq}` aliases | ⏳ Pending |
| `DispatchAgent` output uses `attached_wis`/`target_wi` aliases | ⏳ Pending |
| Router `read_workspace_item` tool accepts alias | ⏳ Pending |
| Planner XML blocks render `wi="WI-{seq}"` | ⏳ Pending |
| `PlannerResponse.referenced_wi` alias | ⏳ Pending |
| Planner `read_workspace_item` tool accepts alias | ⏳ Pending |
| Writer planner outputs `n` only (single-WI) or `(wi, n)` tuples (multi-WI) | ⏳ Pending — designed in this doc; lands when writer Layer 2 flow is built |
