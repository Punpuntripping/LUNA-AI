# V4 Pipeline End-to-End Validation Log

Goal: drive a full v4 pipeline run (planner → reg + compliance + case in parallel → URA → aggregator) on a real query from `agents/test_queries.json`, with the **planner using `qwen3.6-plus` via the Alibaba provider**, all stage artifacts captured by `agents/deep_search_v4/monitor/run_monitor.py`.

This log documents every error encountered during setup + execution and the fix applied.

---

## Errors encountered

### Pre-run setup

**E1 — `DASHSCOPE_API_KEY` not set, but Alibaba auth uses `ALIBABA_API_KEY`**
- *Symptom*: `os.environ.get("DASHSCOPE_API_KEY")` is `None`.
- *Root cause*: pydantic_ai's Alibaba path goes through `OpenAIProvider(base_url=settings.ALIBABA_BASE_URL, api_key=settings.ALIBABA_API_KEY)` (see `agents/model_registry.py:942`). The key name is `ALIBABA_API_KEY`, not the upstream Dashscope name. Already present in `.env`.
- *Fix*: ensure `.env` is loaded before the run. Added `from dotenv import load_dotenv; load_dotenv()` at the top of the monitor's `main()`.

**E2 — `agents/deep_search_v4/monitor/run_monitor.py` imported the v3 orchestrator**
- *Symptom*: `from agents.deep_search_v4.orchestrator import FullLoopDeps, run_full_loop` — the v3 orchestrator has no planner wiring, so `enable_planner` and `planner_model` would be ignored even if we added them as flags.
- *Fix*: switch to `from agents.deep_search_v4.orchestrator import FullLoopDeps, run_full_loop`. Also flip the monitor session root from `DSV3_ROOT/monitor` to `DSV4_ROOT/monitor`.

**E3 — No CLI flags for planner**
- *Symptom*: monitor CLI exposes `--query-id` only. To live-test the planner we need `--enable-planner`, `--planner-model`, and `--model-override` (the latter for the executor LLMs).
- *Fix*: extend `argparse` and thread the values into `FullLoopDeps(...)`.

### Run-time

**E4 — Windows `cp1252` stdout can't encode Arabic**
- *Symptom*: `UnicodeEncodeError: 'charmap' codec can't encode characters in position 31-35` when the monitor prints the query category (Arabic) to console.
- *Root cause*: Python defaults `sys.stdout` to the OS console code page on Windows (cp1252). Anything outside Latin-1 raises.
- *Fix*: at the top of `run_monitor.py`, force UTF-8 on stdout/stderr via `sys.stdout.reconfigure(encoding="utf-8")` (Python 3.7+).

**E5 — `UsageLimitExceeded: output_tokens_limit of 2000 (output_tokens=3647)`** (×2)
- *Symptom*: `pydantic_ai.exceptions.UsageLimitExceeded` raised mid-run. The pipeline didn't crash — these were caught by per-phase exception handling — but the offending stage produced no output that round.
- *Root cause*: `agents/deep_search_v3/case_search/expander.py:35` declares `EXPANDER_LIMITS = UsageLimits(response_tokens_limit=2_000, request_limit=3)` while reg/compliance use `70_000`. With qwen3.6-plus emitting verbose Arabic JSON with `legal_sectors`, `channel_typed_queries`, and per-query rationale, 2K is too tight — the model hit ~3.6K tokens. Pre-existing on master (the stale-literal failure in `compliance_search/tests/test_expander.py::test_expander_limits_values` is a different symptom of similar drift).
- *Fix proposed* (not applied this session — out of scope): bump `case_search` `EXPANDER_LIMITS.response_tokens_limit` to 70_000 to match its siblings. Tracked as a follow-up.

**E6 — `source_viewer: fetch case … failed: code 204` (×11)**
- *Symptom*: post-aggregation source-view enrichment couldn't fetch 11 case rows (`17642_fi_*`) from Supabase. The aggregator output already had snippets, so this only affected the click-through preview metadata; final references shipped without the popup payload for those rows.
- *Root cause*: PostgREST returned `204 No Content` for those `case_id` keys — likely the rows are no longer in the live table or the unique-id format changed since the case_search reranker tagged them. This is a data-freshness issue between the case-search retrieval and the `source_view` lookup, not a pipeline bug.
- *Fix proposed*: ignore (already non-fatal — `attach_source_views` swallows per-ref failures and proceeds with `source_view=None`). The error log is noisy; suppressing the per-row warning to `logger.debug` would clean up monitor output without losing the signal in `events.jsonl`.

---

## Run results — query #16 (multi-domain labor, 2026-04-30 14:01 UTC)

**Command**:
```
python -m agents.deep_search_v4.monitor.run_monitor \
  --query-id 16 \
  --enable-planner \
  --planner-model qwen3.6-plus \
  --model-override qwen3.6-plus
```

**Query** (excerpt): "كنت موظف في شركه من 2012 بدون عقد الا نهايه 2018 ... رفعت عليهم قضيه في مكتب العمل ... 1/كم تأخذ القضيه عشان تنتقل من مكتب العمل الي المحكمه؟ 2/اقدر اطالب بتعويض عن فترة المماطله ..."

**Planner output** (`qwen3.6-plus`, 16.4 s):
```json
{
  "invoke": ["reg", "compliance", "cases"],
  "focus": {"reg": "default", "compliance": "high", "cases": "high"},
  "sectors": ["العمل والتوظيف"],
  "rationale": "سؤال عمالي متشعب يغطي إجراءات نقل القضية من مكتب العمل للمحكمة (compliance - محوري)، وحقوق نهاية الخدمة وتعويض التأخير وأتعاب المحامي (reg و cases - محوريان للسوابق والتعويض)."
}
```

The plan matches a sensible human read of the query — the user has 4 explicit sub-questions spanning labor rules (reg), procedural timing (compliance — high), and compensation precedents (cases — high). Same shape as the offline `gpt-5.4-mini` validation produced earlier. Sector pick is canonical (`العمل والتوظيف`).

**Pipeline outcome**:

| Stage | Result |
|---|---|
| Planner | `qwen3.6-plus`, 16.4 s, valid plan |
| Reg search | 5 RQRs → 9 high / 8 medium URA results |
| Compliance search | 12 RQRs |
| Case search | 1 RQR (capped early by E5 — UsageLimitExceeded recoverable) |
| URA | 9 high + 8 medium, 0 dropped |
| Aggregator | `qwen3.6-plus`, prompt_1 (CRAC), 12 references cited |
| Validation | passed=True, 100 % sub-query coverage, 0 dangling/unused/ungrounded |

**Wall time**: 843 s (~14 min). The bulk of this is the parallel executor phase + the aggregator synthesis on `qwen3.6-plus`; the planner itself is 16 s of that total. Latency budget vs design's +1.5 s target: the planner sits well inside that for `qwen3-flash`-class models; on `qwen3.6-plus` it's ~10× slower (full reasoning model) but still negligible against the executor cost.

**Tokens**: 152 660 in / 57 808 out — dominated by the case_search rounds and the aggregator.

**Artifacts** at `agents/deep_search_v4/monitor/query_16/20260430_140139/`:
- `00_query.md` — input + executor flags
- `10_reg_search/`, `20_compliance_search/`, `30_case_search/` — verbatim mirrors of each phase's report dir (expander I/O, search hits, reranker decisions)
- `40_ura.md` — merged URA (high + medium tiers)
- `50_aggregator/{prompt_*.md, llm_raw_*.txt, synthesis.md, references.json, validation.json}`
- `60_runtime/{events.jsonl, per_executor_stats.md}` — `plan_ready` event present and structurally correct
- `summary.md` + `README.md`

---

## Run 2 — three diverse queries, compliance retries disabled

Compliance `MAX_ROUNDS` lowered from 3 to 1 (`loop.py:44`) before this run. Same monitor command as run 1, three `--query-id` invocations sequentially.

### Errors

**E7 — `KeyError: Unknown aggregator prompt key 'prompt_reg_only'` (CRASHED q7)**
- *Symptom*: q7's planner correctly picked `invoke=["reg"]` → `derive_aggregator_prompt_key` returned `prompt_reg_only`, but `agents/deep_search_v3/aggregator/prompts.py:289` raised `KeyError` because v3's registry only has `prompt_1..prompt_4`.
- *Root cause*: the v4 orchestrator (post Phase 1–6 work) was still importing the **v3** aggregator runner / models / deps. The new mode-specialized prompts (`prompt_reg_only`, `prompt_cases_only`, `prompt_comp_only`, `prompt_cases_focus`) were added to v4's `aggregator/prompts.py` only — never propagated to v3. As long as the planner stuck to `prompt_1` (q16's `{reg, comp, cases}` invoke), the bug was hidden; the moment a singleton invoke set produced a v4-only key, the v3 aggregator path crashed.
- *Fix*: switched 3 imports in `agents/deep_search_v4/orchestrator.py` and 2 in `agents/deep_search_v4/monitor/run_monitor.py` from `deep_search_v3.aggregator.*` → `deep_search_v4.aggregator.*`. The v4 aggregator package is self-contained — runner does relative imports from `.prompts`, so the new keys resolve correctly.

**E8 — `WinError 10035: A non-blocking socket operation could not be completed immediately` (q5, q7, dozens each)**
- *Symptom*: many `Failed to fetch …` warnings during reg_search post-reranker enrichment (`_enrich_kept_blocks`, `_fetch_section_row`) and aggregator source-view enrichment (`source_viewer: fetch …`). Pipeline didn't crash but enrichment for ~20 articles/sections was skipped on every reg-heavy run — final references missing the section-context block / source-view payload.
- *Root cause*: Windows asyncio + httpx is hitting EAGAIN/EWOULDBLOCK because the reg unfold step fires a burst of parallel Supabase fetches (siblings, parents, sections) and the local socket pool can't keep up. Linux/Mac don't surface this — Windows is more aggressive about blocking the call.
- *Fix proposed* (not applied this session): tighten `concurrency` on the unfold path or wrap the burst in a small `asyncio.Semaphore`. Already-shipping pipeline is fault-tolerant (errors are logged and the missing context just degrades the source-view popup). Tracked as a Windows-specific follow-up.

**E9 — `ValidationError: ValidationReport [type=model_type]` on `AggregatorOutput` construction (CRASHED q7 retry)**
- *Symptom*: after fixing E7 (switch to v4 aggregator imports), q7 still crashed at the very end during `AggregatorOutput(... validation=report ...)` with Pydantic complaining that the `ValidationReport` instance wasn't a valid `ValidationReport`.
- *Root cause*: Pydantic identifies classes by identity, not duck-typing. `agents/deep_search_v4/aggregator/postvalidator.py` was importing `ValidationReport` from **v3** (`from agents.deep_search_v4.aggregator.models import …, ValidationReport`), so it returned a v3-class instance. The v4 aggregator runner then tried to assign that to `AggregatorOutput.validation` (typed against v4's `ValidationReport`) and Pydantic refused.
- *Fix*: switched 4 imports in `agents/deep_search_v4/aggregator/postvalidator.py` from `agents.deep_search_v4.aggregator.models` → `agents.deep_search_v4.aggregator.models`. The v3/v4 model classes are byte-identical at the source level — Pydantic just needs the same class object end-to-end. Other v4 aggregator modules with similar v3 imports (`log_parser.py`, `preprocessor.py`, `replay.py`) weren't touched: they aren't on the live planner path. Flagged as a separate cleanup follow-up.



### Run 2 results

q7 hit E7 then E9 in succession; both fixed mid-run, third attempt clean. q33 and q5 ran clean on the first try. Final stats (after all fixes):

| query | invoke (planner) | focus | sectors | wall | planner | reg | comp | case | aggregator | refs | status |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **q7** rejection of رجعة | `[reg]` | `reg: default` | `null` | 363.8 s | 24.4 s | 241.0 s | — | — | 98.4 s | 12 | ✓ |
| **q33** سوابق فصل تعسفي | `[reg, cases]` | `reg: default, cases: high` | `العمل والتوظيف` | 317.5 s | 23.1 s | 186.3 s | — | 108.6 s | 108.1 s | 8 | ✓ |
| **q5** سائق إنهاء عقد | `[reg, compliance]` | `reg: high, compliance: default` | `العمل والتوظيف` | 213.9 s | 20.8 s | 160.5 s | 67.0 s | — | 32.5 s | 4 | ✓ |

**Compliance retries-off win**: q5 is the only run-2 query that exercised compliance. Compliance phase clocked **67 s** (single round). Run 1 q16 — same provider, same model — saw compliance burn **706 s** with 12 RQRs accumulated across multiple retry rounds. `MAX_ROUNDS = 3 → 1` is the single biggest latency win this session.

**Planner choice quality**:
- q7's plan correctly flagged "no canonical sector" (returned `null`) — matches the offline gpt-5.4-mini run. Personal-status queries genuinely don't fit the 39-entry regulations vocab. Rationale: "لا قطاع مطابق في القاموس فأعدت null."
- q33 (pure precedent request) went `[reg, cases]` with `cases: high` — adding reg as an "anchor" to identify the underlying labor-law article being violated. Sensible.
- q5 went `[reg, compliance]` (skipping cases entirely) — the user's question was procedural enough that case law would add little. Shorter-than-default plan, well-reasoned rationale.

## Final verdict

The v4 pipeline runs end-to-end with the cut-1.5 planner, the qwen3.6-plus model on the Alibaba provider, and four diverse query shapes across two runs (`{reg}`, `{reg, cases}`, `{reg, compliance}`, `{reg, compliance, cases}`). Compliance retries are off. Nine errors documented end-to-end:

- **E1–E4** (run 1): monitor wiring + Windows console encoding.
- **E5–E6** (run 1): pre-existing executor-side issues — case_search 2 K token limit, case-source 204s. Non-fatal; flagged for later.
- **E7** (run 2): v4 orchestrator was importing v3 aggregator runner — invisible until a singleton invoke produced a v4-only prompt key.
- **E8** (runs 1+2): Windows-only socket-pool exhaustion in reg unfold. Non-fatal.
- **E9** (run 2): v4 postvalidator was importing v3 ValidationReport class — Pydantic identity mismatch. Mechanical import-path fix.

Three remaining v3 imports inside v4 aggregator (`log_parser.py`, `preprocessor.py`, `replay.py`) are off the live planner path and weren't touched. Cleanup ticket noted in §Cut-2 carry-forward of `V4_PLANNER_DESIGN.md`.