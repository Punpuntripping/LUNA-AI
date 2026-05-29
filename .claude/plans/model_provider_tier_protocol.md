# Model / Provider / Tier Protocol

> **Reference doc** — how every LLM agent in Luna Legal AI resolves its model.
> Source of truth in code: `agents/utils/agent_models.py` (control surface) backed
> by `agents/model_registry.py` (catalog). Embeddings are deliberately **out of
> scope** — see [Embeddings are out of scope](#embeddings-are-out-of-scope).
>
> Snapshot: **OpenRouter is the PRIMARY provider** (head of every fallback chain),
> Alibaba DashScope is the automatic FALLBACK. This is controlled by a single
> line — see [How-to recipe (a)](#a-change-the-global-default-provider-one-line).

## Summary

Every LLM agent resolves its model through one function — `get_agent_model(slot)`
(`agents/utils/agent_models.py:150`) — which returns a Pydantic AI
`FallbackModel`. An agent never names a concrete model; it declares an **intent**
(`ModelPolicy`) keyed by a stable **slot** name in the `AGENT_MODELS` dict
(`agents/utils/agent_models.py:90`). That intent is expanded into a 4-step
provider+family fallback chain, each step mapping to a concrete entry in the
model registry (`agents/model_registry.py`), which `create_model()` turns into a
provider-specific Pydantic AI client.

This gives three orthogonal axes (Tier, Family, Provider), one editable table,
automatic cross-provider failover, and tier-based cost accounting — all without
hard-coding model names in agent code.

---

## The three axes

A `ModelPolicy` is fully described by three independent dimensions
(`agents/utils/agent_models.py:32-34`, `:56-66`):

| Axis | Type alias | Values | Meaning |
|------|-----------|--------|---------|
| **Tier** | `Tier` | `tier_1`, `tier_2` | Model cost/capability bucket. `tier_1` = capable, `tier_2` = cheap/fast. Locked per slot — agents cannot leave their tier. |
| **Family** | `Family` | `qwen`, `deepseek` | Model family. `qwen` is the default primary family; `deepseek` is the in-tier fallback family. |
| **Provider** | `Provider` | `openrouter`, `alibaba` | Where the model is served. **`openrouter` is the default primary**; `alibaba` is the automatic fallback. |

Each `(tier × family × provider)` cell maps to exactly one registry key. The full
mapping is the `TIERS` table (`agents/utils/agent_models.py:37-46`):

| Tier | Family | Alibaba key | OpenRouter key |
|------|--------|-------------|----------------|
| **tier_1** | qwen | `qwen3.6-plus` | `or-qwen3.6-plus` |
| **tier_1** | deepseek | `deepseek-v4-pro` | `or-deepseek-v4-pro` |
| **tier_2** | qwen | `qwen3.5-flash` | `or-qwen3.5-flash` |
| **tier_2** | deepseek | `deepseek-v4-flash` | `or-deepseek-v4-flash` |

The "other" provider and family are derived automatically — there is no second
table to keep in sync (`agents/utils/agent_models.py:48-49`):

```python
_OTHER_PROVIDER = {"alibaba": "openrouter", "openrouter": "alibaba"}
_OTHER_FAMILY   = {"qwen": "deepseek", "deepseek": "qwen"}
```

So flipping the primary provider or family automatically flips its complement.

---

## The 4-step fallback chain

`resolve_chain(policy)` (`agents/utils/agent_models.py:69-79`) expands a policy
into four ordered registry keys, walking **both families on the primary provider
first, then both families on the fallback provider**:

```
1. (primary provider, primary family)
2. (primary provider, fallback family)   # same provider, other family
3. (fallback provider, primary family)   # other provider, same family
4. (fallback provider, fallback family)  # other provider, other family
```

`build_fallback_model(policy)` (`agents/utils/agent_models.py:82-85`) creates a
Pydantic AI model for each key and wraps them in a `FallbackModel`
(head + tail).

### Failover semantics — IMPORTANT

`FallbackModel` advances to the next step **only on `ModelAPIError`** (4xx/5xx
HTTP API errors from the provider). Output-validation failures and tool-retry
failures do **not** trigger provider fallback — those are handled by Pydantic AI's
normal retry machinery inside a single model
(`agents/utils/agent_models.py:12-13`, `:179-181`).

### Worked example 1 — `ModelPolicy("tier_1")` (the common default)

Defaults: `provider="openrouter"`, `primary="qwen"`. So
`fb_provider="alibaba"`, `fb_family="deepseek"`. `resolve_chain` returns:

| Step | provider | family | registry key |
|------|----------|--------|--------------|
| 1 | openrouter | qwen | `or-qwen3.6-plus` |
| 2 | openrouter | deepseek | `or-deepseek-v4-pro` |
| 3 | alibaba | qwen | `qwen3.6-plus` |
| 4 | alibaba | deepseek | `deepseek-v4-pro` |

### Worked example 2 — `ModelPolicy("tier_2", primary="deepseek")`

(This is the `item_analyzer` / `artifact_summarizer` / `sector_picker` shape.)
Defaults: `provider="openrouter"`. With `primary="deepseek"`,
`fb_family="qwen"`; `fb_provider="alibaba"`. `resolve_chain` returns:

| Step | provider | family | registry key |
|------|----------|--------|--------------|
| 1 | openrouter | deepseek | `or-deepseek-v4-flash` |
| 2 | openrouter | qwen | `or-qwen3.5-flash` |
| 3 | alibaba | deepseek | `deepseek-v4-flash` |
| 4 | alibaba | qwen | `qwen3.5-flash` |

---

## ModelPolicy + AGENT_MODELS

### The dataclass

`ModelPolicy` (`agents/utils/agent_models.py:56-66`) is a frozen dataclass with
sensible defaults so a slot usually only needs to name its tier:

```python
@dataclass(frozen=True)
class ModelPolicy:
    tier: Tier                       # required — locked per slot
    provider: Provider = "openrouter"  # head of chain; fallback derived
    primary: Family = "qwen"           # head family; fallback derived
```

`provider` and `primary` name the **head** of the fallback chain; the fallback
provider and family are derived via `_OTHER_PROVIDER` / `_OTHER_FAMILY`.

### The per-agent control surface

`AGENT_MODELS` (`agents/utils/agent_models.py:90-126`) is the single editable
table mapping each agent **slot** to a `ModelPolicy`. Currently only the three
rerankers plus the three deepseek-primary tier_2 agents use `tier_2`; everything
else is `tier_1`. All slots use the default `provider="openrouter"`.

| Slot | Tier | Provider | Primary family | Notes |
|------|------|----------|----------------|-------|
| `planner_decider` | tier_1 | openrouter | qwen | |
| `planner_responder` | tier_1 | openrouter | qwen | |
| `aggregator` | tier_1 | openrouter | qwen | |
| `agent_writer` | tier_1 | openrouter | qwen | |
| `writer_planner_decider` | tier_1 | openrouter | qwen | Layer-2 Major planner in front of writing_executor. Talks to user (`ask_user`, `present_plan_for_approval`), calls `item_analyzer` for context distillation when prior-WI scope is wide, hands a `WriterPackage` to the writing executor. Multi-turn loop per user turn (capped at 3 `present_plan_for_approval` cycles). Output is a discriminated `list[PlannerDecision \| DeferredToolRequests]` — same shape as the deep_search planner. See `.claude/plans/writer_planner.md`. |
| `router` | tier_1 | openrouter | qwen | |
| `reg_search_expander` | tier_1 | openrouter | qwen | |
| `reg_search_reranker` | tier_2 | openrouter | qwen | |
| `reg_search_aggregator` | tier_1 | openrouter | qwen | |
| `case_search_expander` | tier_1 | openrouter | qwen | |
| `case_search_reranker` | tier_2 | openrouter | qwen | |
| `case_search_aggregator` | tier_1 | openrouter | qwen | |
| `compliance_search_expander` | tier_1 | openrouter | qwen | |
| `compliance_search_reranker` | tier_2 | openrouter | qwen | |
| `artifact_summarizer` | tier_2 | openrouter | **deepseek** | DeepSeek-primary with reasoning enabled — runs once per published workspace item to produce an agent-facing coverage summary. |
| `item_analyzer` | tier_2 | openrouter | **deepseek** | Layer-4 librarian that verdicts `workspace_items` against a caller's query. Two LLM calls max per `analyze()` (one per family: refs vs meta). Short structured outputs — reasoning mode is OFF. See `.claude/plans/item_analyzer_v2.md` §6. |
| `sector_picker` | tier_2 | openrouter | **deepseek** | Runs once per deep_search invocation, in parallel with the expanders, to pick the 2–5 sector AND-filter. Replaces the old `planner_decider.sectors` output (decider had no per-sector corpus visibility — diagnosed in conv `faa3b71e`). DeepSeek-flash is fast/cheap; the call is a short two-field structured output. |

(18 slots total.)

---

## Overrides

A CLI `--model` token can tweak the **head** of an agent's chain at runtime while
keeping it locked to its slot's tier.

### Valid tokens

`OVERRIDE_TOKENS = ("qwen", "deepseek", "alibaba", "openrouter")`
(`agents/utils/agent_models.py:53`). Two name a **family**, two name a
**provider**.

### `apply_override(slot, token)`

(`agents/utils/agent_models.py:129-147`)

1. Starts from the slot's base policy in `AGENT_MODELS`.
2. If `token` is falsy → returns base unchanged.
3. If token is `qwen`/`deepseek` → `replace(base, primary=token)` (swap head family).
4. If token is `alibaba`/`openrouter` → `replace(base, provider=token)` (swap head provider).
5. Anything else → `ValueError`.

Because only `primary`/`provider` are touched, the **tier is never changed** — an
agent can only ever use models within its declared tier. The fallback complement
re-derives automatically from the new head.

### `get_agent_model(slot, override=None)`

(`agents/utils/agent_models.py:150-171`) accepts either:

- `None` → use the base policy.
- a **string** token → routed through `apply_override`.
- a full **`ModelPolicy`** → used directly, but **rejected with `ValueError` if
  its `tier` differs from the slot's declared tier** (agents are locked to their
  tier).

So even a hand-built `ModelPolicy` override cannot escape the slot's tier.

---

## Cost accounting

Cost is tracked **per tier**, not per resolved model
(`agents/utils/agent_models.py:174-184`). Rationale: within a tier the qwen and
deepseek families (and OpenRouter vs Alibaba) price out roughly equal, and
`FallbackModel` only swaps off the primary on a 4xx/5xx error — so billing every
call at one representative rate is accurate enough and avoids per-model bookkeeping.

### Tier rate = the conservative ceiling

`tier_rate(tier)` (`agents/utils/agent_models.py:202-213`, `@lru_cache`) reads
the registry entry for the tier's **Alibaba qwen cell** — `TIERS[tier]["qwen"]["alibaba"]`
— and returns `(input_price, output_price)`. That cell is the **priciest** in the
tier (OpenRouter list prices and the deepseek family are both ≤ it), so billing
every call at this rate is a deliberate **conservative ceiling**. The registry
stays the single source of pricing truth — no duplicated price tables.

- tier_1 ceiling = `qwen3.6-plus` → input `0.57`, output `3.44` (per 1M tokens)
- tier_2 ceiling = `qwen3.5-flash` → input `0.10`, output `0.40` (per 1M tokens)

### Sub-agent → tier mapping

`_SUBAGENT_TIER` (`agents/utils/agent_models.py:189-194`) maps a deep_search
sub-agent **role name** (the `agent` field on `inner_usage` entries) to a tier,
mirroring `AGENT_MODELS`:

| role | tier |
|------|------|
| `expander` | tier_1 |
| `reranker` | tier_2 |
| `aggregator` | tier_1 |
| `sector_picker` | tier_2 |

`tier_of_subagent(agent)` (`:197-199`) looks this up; unknown roles default to
`tier_1`.

### `cost_usd(...)` — single-call cost

(`agents/utils/agent_models.py:216-239`) Computes USD for one LLM call billed at
tier rates:

```
billable_in  = max(input_tokens - cached_tokens, 0)
billable_out = output_tokens + reasoning_tokens
cost = (billable_in * in_rate
        + cached_tokens * in_rate * 0.1     # cached input ~10x cheaper
        + billable_out * out_rate) / 1_000_000
```

Key behaviors:
- **Cached input discount**: `cached_tokens` (a subset of `input_tokens`) is
  billed at `in_rate * 0.1` when prompt caching is active.
- **Reasoning tokens bill at the OUTPUT rate** — providers count them as
  completion tokens, and pydantic_ai's `output_tokens` does **not** include them
  (they live in `usage.details['reasoning_tokens']`), so they're added separately.
- Unknown tier strings fall back to `tier_1` rates.

### `usage_by_tier(inner_usage)` — fold usage into per-tier totals

(`agents/utils/agent_models.py:242-260`) Iterates pydantic_ai usage entries (each
carrying `agent`, `input_tokens`, `output_tokens`, optional
`details.reasoning_tokens`), buckets them by `tier_of_subagent`, and returns
`{tier: {"input", "output", "reasoning"}}` — the shape stored under
`per_phase_stats[phase]["per_tier"]`.

### `estimate_run_cost(...)` — total cost for one agent run

(`agents/utils/agent_models.py:263-305`) Returns `(cost_usd, reasoning_tokens_total)`:

- **Preferred path**: when `per_phase_stats` carries a `per_tier` dict per phase
  (deep_search phases do), sum `cost_usd(tier, in, out, reasoning)` across every
  tier in every phase.
- **Fallback path**: for single-model agents (writer, memory, router) with no
  per-tier breakdown, bill the aggregate `tokens_in`/`tokens_out`/`tokens_reasoning`
  at flat **tier_1** rates.
- Never raises — returns `(0.0, 0)` on malformed input.

The per-run total lands on `agent_runs.cost_usd` (see memory
`project_cost_tracking.md`).

---

## Model registry relationship

`agents/utils/agent_models.py` deals only in **registry keys** (the strings in
`TIERS`). `agents/model_registry.py` owns the concrete config and instantiation.

### `ModelConfig` dataclass

(`agents/model_registry.py:35-69`) fields used by the protocol:

- `model_id` — the provider's actual model id (e.g. `qwen/qwen3.6-plus`).
- `provider` — `openai` / `anthropic` / `google` / `deepseek` / `minimax` /
  `openrouter` / `alibaba`.
- `display_name`.
- capability flags: `supports_temperature/_streaming/_tools/_vision/_json_mode`.
- defaults: `default_temperature`, `max_tokens`, `context_length`.
- pricing (per 1M tokens): `input_price`, `output_price`, `cached_input_price`.
- `output_speed_tps`, `temperature_range`, `fixed_temperature`, `extra_kwargs`.

### Lookup + instantiation

- `get_model_config(name)` (`agents/model_registry.py:881-911`) — direct key
  lookup, then `model_id` match, then prefix match; raises `ValueError` if not
  found.
- `get_api_key(provider)` (`:914-925`) — maps a provider name to the matching
  `settings.*_API_KEY`.
- `create_model(name)` (`:928-1031`) — builds the provider-specific Pydantic AI
  model. Per provider:

| provider | Pydantic AI model + provider class | Notes |
|----------|------------------------------------|-------|
| `openai` | `OpenAIChatModel` + `OpenAIProvider` | |
| `anthropic` | `AnthropicModel` + `AnthropicProvider` | |
| `google` | `GoogleModel` + `GoogleProvider` | |
| `deepseek` | `OpenAIChatModel` + `DeepSeekProvider` | OpenAI-compatible |
| `minimax` | `OpenAIChatModel` + `OpenAIProvider(base_url="https://api.minimax.io/v1")` | OpenAI-compatible |
| `openrouter` | `OpenRouterModel` + `OpenRouterProvider` | Applies `OpenAIModelProfile(openai_supports_tool_choice_required=False)` when `extra_kwargs["no_tool_choice_required"]` is set |
| `alibaba` | `OpenAIChatModel` + `OpenAIProvider(base_url=settings.ALIBABA_BASE_URL)` | Always sets `OpenAIModelProfile(openai_supports_tool_choice_required=False)` (DashScope rejects `tool_choice="required"`) |

`create_model` raises `ValueError` if the provider's API key is missing or the
provider is unknown.

### The 8 tier-system models + prices

(per 1M tokens; `cached_input_price` shown where set)

**Alibaba half** (`agents/model_registry.py:687-723`):

| key | model_id | input | output | cached_in |
|-----|----------|-------|--------|-----------|
| `qwen3.6-plus` | `qwen3.6-plus` | 0.57 | 3.44 | — |
| `deepseek-v4-pro` | `deepseek-v4-pro` | 1.74 | 3.48 | 0.0036 |
| `qwen3.5-flash` | `qwen3.5-flash` | 0.10 | 0.40 | — |
| `deepseek-v4-flash` | `deepseek-v4-flash` | 0.14 | 0.28 | 0.0028 |

**OpenRouter half** (`agents/model_registry.py:553-594`):

| key | model_id | input | output | cached_in |
|-----|----------|-------|--------|-----------|
| `or-qwen3.6-plus` | `qwen/qwen3.6-plus` | 0.50 | 3.00 | — |
| `or-deepseek-v4-pro` | `deepseek/deepseek-v4-pro` | 1.74 | 3.48 | 0.0036 |
| `or-qwen3.5-flash` | `qwen/qwen3.5-flash-02-23` | 0.10 | 0.40 | — |
| `or-deepseek-v4-flash` | `deepseek/deepseek-v4-flash` | 0.112 | 0.224 | 0.0028 |

> Note (`agents/model_registry.py:549-552`, `:698-701`): OpenRouter and Alibaba
> show temporary promos (qwen ~35% off, deepseek-v4-pro 75% off until 2026-05-31).
> The registry deliberately keeps **list prices** for stable cost tracking. This
> confirms the Alibaba qwen cell as the conservative ceiling for `tier_rate`.

---

## Embeddings are out of scope

Embeddings **never touch `ModelPolicy`, `AGENT_MODELS`, the tier system, or
`FallbackModel`**. They live in `agents/utils/embeddings.py` and are wired
provider-by-provider with their own clients. This separation is intentional:
embedding dimensionality must match the stored pgvector corpus (1024-dim for the
regulation/case/compliance corpora), so it cannot be allowed to drift with the
LLM provider default.

Regulation/case/compliance query embedding resolves through the alias
`embed_regulation_query` (`agents/utils/embeddings.py:188`), which defaults to
**Alibaba DashScope `text-embedding-v4`, 1024-dim**. To switch providers you
re-point that one alias.

Available embedding providers:

| function | provider | model | dims |
|----------|----------|-------|------|
| `embed_regulation_query_alibaba` (**default**) | Alibaba DashScope | `text-embedding-v4` | 1024 |
| `embed_regulation_query_qwen3` (backup) | OpenRouter | `qwen/qwen3-embedding-4b` | 1024 |
| `embed_regulation_query_gemini` (legacy) | Google REST | `gemini-embedding-001` | 768 |
| `embed_text` / `embed_texts` (app-side / planner) | OpenAI | `text-embedding-3-small` | 1536 |

Priority order documented in code: alibaba (default) → qwen3 (backup) → gemini
(legacy) (`agents/utils/embeddings.py:186-188`). The DashScope batched helper
auto-splits at `MAX_EMBED_BATCH = 25` per call (`:51`).

---

## Layer vs Tier — do NOT conflate (from CLAUDE.md)

These are **two completely different axes**. A given agent has **both** — a Layer
(where it sits) and a Tier (which model it bills against).

| Word | Meaning | Where defined | Values |
|------|---------|---------------|--------|
| **Layer** (Layer 1–4) | Architectural position in the agent call graph — who can talk to the user, who can write `workspace_items`, what context surface each agent gets. | `.claude/plans/wave_9_agent_runs.md` § "Agent Hierarchy" | Layer 1 Conductor (Router) · Layer 2 Major (planners, user-facing) · Layer 3 Task (transformers — aggregator, agent_writer) · Layer 4 Memory (summarize/compact/distill) |
| **Tier** (tier_1, tier_2) | Model cost/capability bucket — drives which family + provider chain `get_agent_model(slot)` returns. | `agents/utils/agent_models.py:32-45` | tier_1 = qwen3.6-plus / deepseek-v4-pro (capable) · tier_2 = qwen3.5-flash / deepseek-v4-flash (cheap/fast) |

Example: `item_analyzer` is **Layer 4 Memory** running on model **tier_2**
(deepseek-flash). `writer_planner_decider` is **Layer 2 Major** running on model
**tier_1** (qwen3.6-plus). Older plans/reports using "Tier 1–4" for the
architectural concept are pre-rename — read them as "Layer 1–4".

---

## How-to recipes

### (a) Change the global default provider (one line)

OpenRouter is primary because of the default on the `ModelPolicy` dataclass.
Edit **one line**:

1. `agents/utils/agent_models.py:65` — `provider: Provider = "openrouter"`.
2. To flip back to Alibaba-primary, change it to `provider: Provider = "alibaba"`.

Every slot using the default picks this up automatically (the fallback provider
re-derives via `_OTHER_PROVIDER`). No registry or per-agent edits needed.

### (b) Change one agent's tier / family / provider

1. Open `AGENT_MODELS` (`agents/utils/agent_models.py:90-126`).
2. Edit that slot's `ModelPolicy(...)`:
   - tier: first positional arg, `"tier_1"` or `"tier_2"`.
   - family head: `primary="qwen"` / `primary="deepseek"`.
   - provider head: `provider="openrouter"` / `provider="alibaba"`.
3. Nothing else changes — chain + fallback + cost tier all derive from the policy.
   (For deep_search sub-agent roles, also confirm the role→tier mapping in
   `_SUBAGENT_TIER` at `:189-194` if you changed a reranker/expander tier.)

### (c) Add a brand-new agent slot

1. Add `"my_new_slot": ModelPolicy("tier_1")` to `AGENT_MODELS`
   (`agents/utils/agent_models.py:90-126`).
2. In the agent's construction code, call
   `get_agent_model("my_new_slot")` to obtain its `FallbackModel`.
3. If it runs as a deep_search sub-agent under a distinct role name, add that
   `role → tier` entry to `_SUBAGENT_TIER` (`:189-194`) so cost accounting buckets
   it correctly.

### (d) Add a new model to the registry

1. Add a `ModelConfig(...)` entry to `MODEL_REGISTRY`
   (`agents/model_registry.py:77`+) with `model_id`, `provider`, prices, and
   capability flags.
2. Ensure `get_api_key` (`:914-925`) and `create_model` (`:928-1031`) already
   handle that `provider` (the 7 listed are wired; a new provider needs a new
   `elif` branch in `create_model` plus a key in `get_api_key`'s map and a
   `*_API_KEY` field in `shared/config.py`).
3. To make a new model reachable by the tier system, wire its key into the
   `TIERS` table (`agents/utils/agent_models.py:37-46`) — otherwise it's only
   reachable by direct `create_model("key")` calls outside the tier abstraction.

---

## Relevant settings (`shared/config.py`)

Names + defaults only (full file is the source of truth):

| Setting | Default | Lines |
|---------|---------|-------|
| `OPENROUTER_API_KEY` | `None` | `shared/config.py:72` |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | `:73` |
| `OPENROUTER_DEFAULT_MODEL` | `anthropic/claude-sonnet-4` | `:74` |
| `ALIBABA_API_KEY` | `None` | `:102` |
| `ALIBABA_BASE_URL` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `:103` |
| `ALIBABA_EMBEDDING_MODEL` | `text-embedding-v4` | `:104` |
| `ALIBABA_EMBEDDING_DIMENSIONS` | `1024` | `:105` |
| `OPENAI_API_KEY` | `None` | `:82` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | `:83` |
| `OPENAI_EMBEDDING_DIMENSIONS` | `1536` | `:84` |
| `ANTHROPIC_API_KEY` | `None` | `:87` |
| `GOOGLE_API_KEY` | `None` | `:90` |
| `DEEPSEEK_API_KEY` | `None` | `:96` |
| `MINIMAX_API_KEY` | `None` | `:99` |
| `AGENT_AUTO_ROUTE_MODEL` | `anthropic/claude-haiku-4-5-20251001` | `:110` |
| `AGENT_DEFAULT_MODEL` | `anthropic/claude-sonnet-4` | `:111` |
| `FEATURE_COST_TRACKING` | `True` | `:118` |

> Note: `OPENROUTER_DEFAULT_MODEL`, `AGENT_AUTO_ROUTE_MODEL`, and
> `AGENT_DEFAULT_MODEL` are legacy/global defaults **not** consulted by the tier
> protocol — the tier system resolves models exclusively through `AGENT_MODELS` +
> `TIERS`. They remain for older code paths.
