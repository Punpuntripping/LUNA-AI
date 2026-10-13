# Crusoe as primary for DeepSeek V4 Flash — three routes, thinking always on

Status: **planned 2026-09-29, reviewed + revised same day (D9–D11, §4.2, §4.4, §5, §8);
nothing built. Migration 169 APPLIED to prod 2026-09-30 (verified).** Scope: LUNA_AI (all DeepSeek-Flash
slots) **and** the marketing dashboard (`C:\Programming\marketing\marketing_dashboard`).

## 1. Why

Measured 2026-09-29 (evidence in §10):

- **Quality.** Blind A/B, 10 full deep searches (questions from the marketing Blog API),
  5 judge lenses. Crusoe **7.66** vs Alibaba **6.58** mean; Crusoe won every lens
  (coverage 8.4/7.0, grounding 7.5/5.9, legal accuracy 7.1/6.3, usefulness 7.6/6.6,
  writing 7.7/7.1) and 6 of 10 questions on combined score (Alibaba 3, one even).
- **Speed.** Generation ~166 t/s median on Crusoe vs ~108 on Alibaba (92 t/s over two
  weeks of dashboard traffic). Deep search median 225 s vs 253 s — the gain is modest
  because Crusoe thinks more and retrieval/rerank time is unchanged.
- **No thinking cap.** 22.8k reasoning tokens observed at `max` (Baseten was rejected for
  a 4,096 cap). Thinking is a hard requirement — never trade it for speed.
- **Cost.** ~$0.010 DeepSeek cost per deep search vs ~$0.007 (1.5× — more thinking, and
  Crusoe's cache hit rate was 26% vs 47%). At current volume ≈ +$2/month product,
  +$1/month dashboard. Verified against Crusoe billing ($0.23 spent vs ~$0.25 estimated).

## 2. Decisions (operator, 2026-09-29)

| # | Decision |
|---|---|
| D1 | Every chain whose head is DeepSeek V4 Flash becomes exactly **Crusoe → Alibaba → OpenRouter**, same model on all three. The Qwen 3.5 Flash middle cell is dropped from these chains. |
| D2 | **Rerankers stay on Qwen** (`reg_compliance_reranker`, `case_search_reranker` chains unchanged). Revisit later. |
| D3 | **Max effort:** `aggregator`, `reg_compliance_aggregator`, `case_search_aggregator`, `agent_writer` (the writer executor, not the writer planner). |
| D4 | **High effort for every other DeepSeek slot**, including `item_analyzer` (thinking was deliberately off) and `router` / `sector_picker` / `anon_ask`. No DeepSeek slot runs without thinking. `writer_planner_decider` = high. |
| D5 | **Straight to all users**, with a one-variable kill switch back to Alibaba-first. |
| D6 | **Failover protocol** (§5): fail over immediately, no same-route retry; empty thinking counts as a failure; a per-route breaker skips a route after N failures in a short window. |
| D7 | The **marketing dashboard** migrates too, same three routes, same protocol. |
| D8 | Built on a **separate worktree/branch off `origin/master`** — the main checkout carries another session's uncommitted deep_search work that must not ride along. |
| D9 | **Quota impact accepted.** Quota points = `llm_calls.cost_usd × 100` (`shared/quota/__init__.py:77`), so the ~1.5× deep-search cost and thinking-on for router/memory slots burn user points faster. No change to `POINTS_PER_USD` or plan limits. |
| D10 | **Separate pricing row per provider.** `crusoe-deepseek-v4-flash` gets its own `model_pricing` row; every existing row stays Alibaba. `provider` column on `model_pricing` and `llm_calls` (migration 169, §4.4). History is Alibaba. |
| D11 | **Failover timing** (§5): no client-level retries on Crusoe/Alibaba; hung routes are detected by an internal streaming idle timeout, not a 600 s read timeout. |

## 3. Crusoe API facts (verified 2026-09-29)

- Base URL `https://api.inference.crusoecloud.com/v1`, OpenAI-compatible. Model id
  **`deepseek-ai/Deepseek-V4-Flash`** (exact casing from `/v1/models`; snapshot 0423 vs
  0731 not stated).
- **Thinking is OFF by default.** Enable per request:
  `extra_body={"chat_template_kwargs": {"thinking": True, "reasoning_effort": "high"|"max"}}`.
  `max` measurably thinks more than `high` (aggregator 22.8k vs 13.1k).
- **Traps.** DashScope's `thinking: {"type": "enabled"}` → **HTTP 403** ("parameter
  'thinking' is not allowed"; proxy allowlists OpenAI params only). A bare top-level
  `reasoning_effort` → **HTTP 200 with no thinking** (silent). `enable_thinking` is not an
  OpenAI param either → expect 403.
- Response: `reasoning_content` + `content`; `usage.completion_tokens` **includes**
  reasoning; `reasoning_tokens` reported. Tool calls work with `tool_choice="auto"`
  (same profile as Alibaba: `openai_supports_tool_choice_required=False`).
- Prices per 1M: input $0.14, cached input $0.03, output $0.28. Rate limits unpublished
  (429 on excess). Usage/billing is **not** readable with the inference key — console only.
- Data terms: the ZDR evidence found so far is Crusoe's *Terms for OpenRouter Customers*
  ("only for inference… not for training… not stored on disk"). **Confirm the direct-customer
  terms say the same before ship** (§9, pre-ship check P1).

## 4. LUNA_AI changes

### 4.1 Config and registry
- `shared/config.py`: `CRUSOE_API_KEY`, `CRUSOE_BASE_URL` (default above),
  `LLM_PRIMARY_PROVIDER` (`crusoe` | `alibaba`, default `crusoe`) — the kill switch (D5),
  plus breaker knobs (§5). Rename the local `.env` key `Cruseo` → `CRUSOE_API_KEY`; add
  it to every Railway service that runs agents.
- `agents/model_registry.py`: new provider `crusoe` in `get_api_key` and `create_model`;
  new entry `crusoe-deepseek-v4-flash` (model_id `deepseek-ai/Deepseek-V4-Flash`,
  prices 0.14 / 0.28 / cached 0.03, max_tokens 65536). `create_model` builds its
  `OpenAIProvider` from an explicit `openai_client=AsyncOpenAI(base_url, api_key,
  max_retries=0, http_client=httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=45,
  write=30, pool=10)))`. The **Alibaba** branch gets the same `max_retries=0` + timeout
  client (today it inherits the OpenAI SDK default of 2 silent same-route retries and a
  600 s read). OpenRouter (last cell) keeps `max_retries=1` — it is the only retry left.
  `read=45` is an *idle* limit, valid because RouteModel streams internally (§5.2).

### 4.2 Chains (`agents/utils/agent_models.py`)
- `resolve_chain`: when the head is `deepseek-v4-flash` →
  `[crusoe-deepseek-v4-flash, deepseek-v4-flash, or-deepseek-v4-flash]`
  (`LLM_PRIMARY_PROVIDER=alibaba` → `[deepseek-v4-flash, or-deepseek-v4-flash]`).
  Every other head (rerankers, `form_drafter` Qwen tier 1) keeps today's chain (D2).
- Policies: `aggregator`, `agent_writer` already `_FLASH_MAX`; move
  `reg_compliance_aggregator`, `case_search_aggregator` to `_FLASH_MAX` (D3). Every other
  DeepSeek slot gets high (D4) **on the policy, not by magic inside `_reasoning_settings`**:
  add `"high"` to `Reasoning`; `_FLASH = ModelPolicy("tier_2", primary="deepseek",
  reasoning="high")`; the bare `ModelPolicy("tier_2", primary="deepseek")` slots
  (`artifact_summarizer`, `item_analyzer`, `template_ingester`, `anon_ask`, `sector_picker`,
  `sharh_generator`) → `reasoning="high"`; `_FLASH_MEDIUM` slots → `high` as well
  (DeepSeek-on-Alibaba already maps medium→high; this aligns Crusoe + OpenRouter).
  `"default"` keeps meaning "no settings" — so the **rerankers' DeepSeek fallback cell and
  `form_drafter`'s `deepseek-v4-pro` cell are untouched** (D2).
- `_reasoning_settings`: branch on `cfg.provider` **first**. The existing
  `if "deepseek" in key` test also matches `crusoe-deepseek-v4-flash` and would send
  DashScope's `thinking` param → Crusoe 403. Order: `crusoe` → `openrouter` → alibaba
  deepseek → alibaba qwen. Crusoe branch:
  `{"extra_body": {"chat_template_kwargs": {"thinking": True, "reasoning_effort": "max" if is_max else "high"}}}`.
  OpenRouter `high` → `reasoning.effort="high"`.
- `build_fallback_model`: wrap **every** cell in `RouteModel` (§5). Crusoe/Alibaba get the
  breaker + guard; the OpenRouter cell gets stamping only (never skipped, no guard).
- **Overrides** (`apply_override`, CLI `--model`, `OVERRIDE_TOKENS`, `Provider`,
  `_OTHER_PROVIDER`): add `crusoe` to `Provider`. Tokens on a DeepSeek-headed slot:
  `crusoe` → the three-route chain (explicit); `alibaba` → `[deepseek-v4-flash,
  or-deepseek-v4-flash]` (same as the kill switch, per run); `openrouter` →
  `[or-deepseek-v4-flash, deepseek-v4-flash]`; `qwen` → today's Qwen-headed chain.
  Qwen-headed slots: `crusoe` token is rejected with the usual error.

### 4.3 Agent-level `model_settings` audit — must fix
pydantic_ai merges model settings **shallowly**: an agent-level `extra_body` replaces the
cell's. On Crusoe that drops `chat_template_kwargs` (silent no-thinking) and sends
`enable_thinking` (likely 403).
- `agents/memory/artifact_summarizer/agent.py:63` sets `extra_body={"enable_thinking": True}`
  on a DeepSeek-headed agent → **remove it**; thinking comes from the cell.
- Reranker agents set `extra_body` too but stay on Qwen (unaffected).
- Add a test that fails if any DeepSeek-headed agent sets agent-level `extra_body`.

### 4.4 Usage, cost, labels
Prices live in the **`model_pricing` table** (`shared/pricing/registry.py`, loaded at
startup), not in code. Today an unknown echo name falls back to the slot's primary
(`tracking._cost`) — so without this section Crusoe calls would be billed at Alibaba's
cached rate (0.0028 vs 0.03).

- **Migration 169** (`shared/db/migrations/169_llm_provider_column.sql`, APPLIED
  2026-09-30): `model_pricing.provider` (NOT NULL DEFAULT `'alibaba'`, all 6 existing rows
  Alibaba), new row `crusoe-deepseek-v4-flash` / `crusoe` / 0.14 · 0.28 · cached 0.03;
  `llm_calls.provider` (nullable) backfilled — LLM history = `alibaba`, except
  `mistral%`→`mistral`, `deepseek/%`→`openrouter`, `gemini%`→`google`. Additive; apply
  **before** the code deploys (§8).
- **One name per call = the registry key.** `RouteModel` stamps every response it returns:
  `model_name = <registry key>` (`crusoe-deepseek-v4-flash` / `deepseek-v4-flash` /
  `or-deepseek-v4-flash`) and `provider_name = <registry provider>`. Everything downstream
  (`_model_from_result`, deep_search `per_model`, `record_call`) then already sees a
  pricing key — no echo-name alias table. Side effect: fixes the OpenRouter echo rows
  (`deepseek/deepseek-v4-flash`) that currently fall through to the slot primary.
- `record_call` writes `provider`: explicit arg if given, else
  `get_model_config(model).provider` for registry keys (the `or-` prefix → `openrouter`),
  `mistral` for OCR, else NULL. `load_pricing` keeps keying by `model_name`
  (unique), `provider` is informational there.
- deep_search sub-agents fall back to a static label (`model_of_subagent`,
  `agent_models.py:500`) when usage carries no model → with stamping this fallback should
  no longer fire for DeepSeek slots; the static map's `"deepseek-v4-flash"` entries become
  `resolve_chain(...)[0]` so the kill switch changes them too.
- `scripts/model_consumption.py` / `cost_for_day.py`: group by `(provider, model)`.
- Log every failover (route, reason: http code / timeout / empty_thinking / breaker_open)
  as a Logfire event so the agentic_monitor can report failover rate.

## 5. Failover protocol (D6)

Applies to every route except the last. One `RouteModel` wrapper
(`agents/utils/route_model.py`, a pydantic_ai `WrapperModel`) around each Crusoe/Alibaba
cell; the dashboard gets the same logic in its own client (§6).

1. **Breaker check first.** If the route's breaker is open → raise `RouteSkipped`
   (a `ModelAPIError` subclass) at once → `FallbackModel` moves to the next route. No
   network call.
2. **Call — streamed internally.** `RouteModel.request()` calls the wrapped cell's
   `request_stream()`, drains it, and returns the assembled `ModelResponse` (callers still
   see a plain non-streaming request; nothing reaches the user until it completes, so a
   discarded attempt is invisible). Why: a non-streamed call sends no bytes until the
   whole answer exists, so a hung route and a legitimately long max-effort answer look
   identical and the only knob is a huge read timeout. Streamed, DeepSeek emits reasoning
   tokens within ~3 s (measured first-token 2.7 s both providers), so:
   - **idle timeout** = httpx `read=45` s (no chunk for 45 s → dead route);
   - **wall-clock cap** per effort via `asyncio.timeout`: high **180 s**, max **300 s**
     (Crusoe max observed 105 s; Alibaba max ≈ 22.8k tok ÷ ~108 t/s ≈ 210 s);
   - connect 5 s.
   Worst case for a hung Crusoe: ~45 s lost, then Alibaba — inside the 420 s producer-side
   pipeline timeout. (With today's 600 s read × 3 SDK retries a hang would outlive the
   pipeline and never fail over.) The Crusoe replay (`scripts/crusoe_replay.py`) already
   ran streamed with tool calls; verify in build that streamed `usage` carries
   `reasoning_tokens` + `cached_tokens` on both Crusoe and Alibaba.
3. **Errors.** Any `ModelAPIError` (4xx/5xx incl. 429 and 403), connection error, idle
   timeout or wall-clock cap → record a failure → raise (`ModelAPIError` subclass for the
   two timeouts) → next route. **No same-route retry** — `max_retries=0` on the SDK client
   (§4.1); the next route is the retry. Verified: pydantic_ai 1.39 maps `APIStatusError` →
   `ModelHTTPError` and `APIConnectionError` (incl. `APITimeoutError`) → `ModelAPIError`
   (`models/openai.py:611-616`), so the default `fallback_on` is enough.
4. **Empty-thinking guard — staged.** Thinking was requested but the response has no
   thinking part and `reasoning_tokens == 0`.
   - Applies to the **first request of a run only** (no `ModelResponse` in `messages`).
     Continuation steps after a tool result may legitimately think little or nothing.
   - **Weeks 0–2: log-only** (`llm.route.empty_thinking` Logfire event with slot, route,
     step). **Then enforce** (raise `EmptyThinking` → failure → next route, answer
     discarded) only if the log shows first-step empties are real and rare, controlled by
     `LLM_EMPTY_THINKING_ENFORCE` (default `false`).
5. **Not failovers:** output-validation / tool-retry errors keep today's behaviour (retried
   by the agent on the same model).
6. **Breaker.** Per route, per process: **N failures within W seconds → open for C
   seconds**, then half-open: the next request is let through; success closes it, failure
   re-opens. Defaults `LLM_ROUTE_BREAKER_FAILS=3`, `LLM_ROUTE_BREAKER_WINDOW_S=120`,
   `LLM_ROUTE_BREAKER_COOLDOWN_S=300`, all env-tunable. State is in-memory; the backend is
   single-worker by design (`WEB_CONCURRENCY` boot guard), so per-process = per-replica.
7. **Stamp** every returned response with the registry key + provider (§4.4) — on all
   three cells, including OpenRouter.

## 6. Marketing dashboard changes (`marketing_dashboard/server`)

- `config.py`: `crusoe_api_key` (env `CRUSOE_API_KEY`), `crusoe_base_url`,
  `crusoe_model`, `llm_primary_provider`, breaker knobs. `build_cells` order:
  **crusoe → alibaba → openrouter** (kill switch drops Crusoe).
- `clients/llm.py` `Cell.reasoning_kwargs`: Crusoe branch —
  `chat_template_kwargs` thinking + effort (`medium`→`high`, `max`→`max`). All dashboard
  agents are `medium` today (→ high); keep that unless the operator wants a max slot.
- Failover loop in `complete()` already walks cells on exceptions: add the empty-thinking
  guard (log-only first, §5.4) and the breaker (§5.6); `max_retries=0` on the Crusoe and
  Alibaba clients, and the same streamed idle-timeout + wall-clock cap (§5.2).
- **Ledger cost bug (fix in the same change).** `compute_cost` (`llm.py:265`) adds
  `tout * out + reasoning * reasoning`, but `tout` (`completion_tokens`) already includes
  reasoning → thinking is billed twice. And `DEFAULT_PRICE_TABLE` (`config.py:30`) uses
  placeholder 0.28/0.42 (no `PRICE_TABLE_JSON` on Railway). Fix: bill
  `tout` once; prices per provider: Crusoe 0.14/0.28/cache 0.03, Alibaba
  0.14/0.28/cache 0.0028 (confirm against the Alibaba Frankfurt bill), OpenRouter per
  endpoint. The two-week ledger showed $2.29; true ≈ $0.95.
- Ledger rows record `provider="crusoe"`.
- Railway: `CRUSOE_API_KEY` on `dashboard-api`. The dashboard working tree also has
  uncommitted work from other sessions — commit only this change's files; check
  `git status` before `/ship`.

## 7. Tests

Unit (run with `-m "not live"` — the repo has no root pytest config and live tests fire by
default):
- `resolve_chain` for every DeepSeek slot = the three routes; rerankers unchanged;
  kill switch = two routes.
- Reasoning per slot: the four D3 slots → `max`; all other DeepSeek slots → `high`;
  no DeepSeek slot resolves to thinking-off.
- Crusoe settings never contain top-level `thinking`, `enable_thinking` or bare
  `reasoning_effort`.
- `RouteModel`: breaker opens after N in W, skips, half-opens after C, closes on success;
  idle timeout and wall-clock cap raise a `ModelAPIError` subclass and fail over;
  `EmptyThinking` logs only by default, fails over when enforced, and never fires on a
  continuation step; validation errors do not fail over; returned responses carry the
  registry key + provider.
- `_reasoning_settings("crusoe-deepseek-v4-flash", …)` never contains DashScope's
  `thinking` key (branch-order regression test).
- Reranker chains and `form_drafter` produce byte-identical cell settings before/after.
- Overrides: each token × DeepSeek/Qwen head gives the chain in §4.2.
- SDK clients for Crusoe/Alibaba have `max_retries == 0`.
- Audit test from §4.3.
- Cost: Crusoe row priced at Crusoe rates (cached 0.03); `record_call` writes
  `provider`; `or-` key → `openrouter`; dashboard `compute_cost` no double count.

## 8. Rollout

1. `git worktree add` from `origin/master` → branch `crusoe-primary`. Copy this plan in.
2. Build §4 + §5 + tests. Unit green.
3. **Live checks (dev env):**
   - one deep search through the CLI (`python -m agents.deep_search_v4.cli ...`) — every
     DeepSeek call on Crusoe, aggregators at max (reasoning ≫ high), `llm_calls` labels
     and costs correct;
   - fault injection: bad `CRUSOE_API_KEY` → every call lands on Alibaba, breaker opens
     after 3, later calls skip Crusoe with no network attempt; bad Alibaba key too →
     OpenRouter serves;
   - fault injection: a local stub that accepts the connection and never answers →
     failover after ~45 s idle, not 600 s;
   - **re-run the 10-question A/B** through the real code path (no wrapper) and compare
     with the 2026-09-29 results (routes, times, reasoning volume; re-judge only if they
     drift);
   - **add a third arm: Alibaba-first with the new thinking policy** (`LLM_PRIMARY_PROVIDER=alibaba`
     on the branch). The 2026-09-29 A/B ran Crusoe with thinking on for every slot but
     Alibaba on production settings (default slots not thinking), so its 7.66 vs 6.58 mixes
     provider with thinking. Judge all three blind. If Alibaba+thinking ≈ Crusoe, the
     ship reason is speed, not quality — record that in §1 (the decision stands, D1).
4. ~~Apply migration 169~~ — DONE 2026-09-30. Until the code ships, NEW llm_calls rows land with `provider` NULL (nothing writes it yet); backfill them with the same CASE after deploy.
5. Set `CRUSOE_API_KEY` on Railway **before** merge — setting a variable triggers a
   redeploy from GitHub `master`, harmless while the code isn't merged. Then merge to
   `master` → auto-deploy. Never use MCP `deploy` from the repo root (it tarballs the dirty
   tree). Confirm with `list_deployments` that the SUCCESS row carries the merge SHA.
   Expect merge conflicts in `shared/config.py` — it is dirty in the main checkout from
   another session.
6. Dashboard: same slice, then `/ship`.
7. Watch 48 h: route share, failover rate + reasons, 429s, empty-thinking events,
   deep-search latency, **router `duration_ms` p50/p95 vs the prior week** (D4 turns
   router thinking on; the A/B never measured the router path), cost/day by `provider`.
   **Rollback:** `LLM_PRIMARY_PROVIDER=alibaba` on Railway — this triggers a redeploy from
   master (a few minutes, not instant; no code change).

## 9. Pre-ship checks and open risks

- **P1 findings (web research 2026-09-30, legal.crusoe.ai).** The DPA ("Data Processing and
  Security Terms") is public and incorporated into the Cloud ToS automatically, with SCCs.
  The Intelligence Foundry terms (Service Specific Terms v5.0, 14 Jul 2026) repeat the
  OpenRouter wording word for word: Inputs and Outputs are not stored to disk, and there is
  no training without explicit opt-in.
  - **Caveats:**
    - No formal ZDR. Crusoe logs metadata and "may observe and log a small portion of
      Customer Data" for debugging, security or law.
    - Processing may happen "in any country" where Crusoe or its subprocessors have
      facilities. No region is stated for serverless inference.
    - Governing law is New York, courts in Denver.
    - No use in apps directed at under-18s. No EU-AI-Act high-risk use.
    - The customer must pass the relevant terms on to its end users. Check whether `/terms`
      needs a clause.
  - **Still to get from Crusoe** (`notices@crusoe.ai`):
    - which data centre serves `api.inference.crusoecloud.com`;
    - the debug-log retention period;
    - SOC 2 / ISO status and the subprocessor list (trust.crusoe.ai, Vanta — open it in a
      browser).
- **P1 — Data terms (HARD GATE).** Confirm Crusoe's direct-customer terms give the same
  no-storage/no-training commitment as the OpenRouter-customer terms (legal questions
  carry client facts), and get a DPA. `/privacy` §4 lists processors only by category and
  promises they process «وفق تعليماتنا فقط» — no copy change needed, but that promise needs
  the DPA. Do **not** name Crusoe on `/learn/data-protection` (Alibaba is the only named
  partner by owner decision). Masking (وضع السرية) is provider-agnostic and still applies.
- **P2 — Rate limits.** Unpublished. Ask Crusoe for the account's limits; deep search fans
  out several parallel calls per turn. The breaker covers bursts, not steady overload.
- **P3 — Model snapshot.** Crusoe's `Deepseek-V4-Flash` may be 0423 while Alibaba serves
  another snapshot; a failover can change model version mid-turn. Acceptable, but note it
  when reading quality drift.
- **P4 — Cache.** Crusoe cached 26% vs Alibaba 47% of input. Check whether a
  session-affinity header or stable prefixes improve it (cost, not correctness).
- **P5 — Region.** Crusoe serverless region undocumented (likely US). Same class of
  exposure as today (Alibaba Frankfurt, OpenRouter).
- **P6 — Credits.** Free credit is $4.77; production needs a card on the Crusoe account
  before ship.
- **P7 — No model diversity (accepted).** Dropping the Qwen middle cell means all three
  routes serve DeepSeek-V4-Flash. A model-level fault (not a provider outage) has no
  fallback. Revisit if one appears.
- **P8 — Router latency.** D4 puts router / sector_picker / anon_ask on high thinking;
  unmeasured. §8 step 7 watches it; if router p95 regresses badly, the cheapest lever is a
  per-slot policy change, not a rollback of the whole chain.

## 10. Evidence (2026-09-29 session)

**`agents_reports/crusoe_eval_2026-09-29/`** (git-ignored, local) — start with its
`README.md`:
- `scripts/crusoe_replay.py` + `replay_out/` — test 1, 10 single-agent replays.
- `scripts/ab_run.py` — test 2 harness (CLI deep search, DeepSeek chains rebound to a
  Crusoe cell); `questions.json`; `ab_out/` — the 20 runs.
- `scripts/build_packets.py`, `judge_packets/`, `judge_key.json`, `judge_results/` —
  blind judging.
`ab_run.py` is also the starting point for the §8 step-3 re-run: drop the rebinding and
run the real code path.
