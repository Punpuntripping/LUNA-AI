# Aggregator Validation → Self-Correction Plan

**Date**: 2026-06-01
**Status**: approved by user, executing
**Scope**: `agents/deep_search_v4/aggregator/{runner,postvalidator,correction}.py` + tests
**Driver**: validation-fail retry is firing on cosmetic Latin parentheticals (4/4 retries in last 14d), producing worse outputs in 4/4 cases. See audit at `agents_reports/aggregator_validation_audit/audit_2026-06-01.md`.

---

## Goal

Replace the current "fail validation → run a different prompt on a different model" retry with a targeted self-correction loop that:

1. Reduces the four hard gates to two: **`citation_ok`**, **`gap_honesty_ok`**. `arabic_only_ok` and `structure_ok` demote to soft / observational.
2. On hard-gate failure, sends the model its own prior output + a **per-gate dynamic correction instruction** in a single follow-up turn via `agent.run(message_history=...)`.
3. Caps at one corrective turn. If still failing, ships primary output with `passed=False` in the report.
4. Preserves planner-chosen prompt mode and model — no more CRAC collapse.

### Success criteria

- Retry rate on `arabic_ok`-only failures → 0% (gate no longer hard).
- Retry rate on real failures (`citation_ok`, `gap_honesty_ok`) → expected near-zero in current traffic (0 observed in last 14d), but framework ready when it happens.
- For any correction that does fire: latency cut roughly in half vs current (cache hit on prefix + smaller delta output), structure preserved, planner's mode preserved.
- All existing observability (Logfire spans, monitor reports, SSE events) intact.
- No production consumer affected (publisher still reads `validation.cited_numbers`).

---

## Why this design

| Aspect | Current "retry" | Self-correction with history |
|---|---|---|
| Prompt | Switches to `prompt_1` CRAC | Keeps planner's chosen mode |
| Model | Same as primary (audit finding) | Same |
| Model sees | Fresh user message, no prior output | Its own prior output + specific error |
| Input tokens | Full reprocess, no cache hit | Same prefix, prompt-cache hit |
| Output tokens | Full regeneration | Targeted patch |
| Quality | 4/4 worse (38-46% shorter, CRAC collapse) | Surgical correction, structure preserved |
| Failure mode | If retry fails → ships fallback anyway | If correction fails → ships primary with `passed=False` |

### Why drop `arabic_ok` and `structure_ok`

- **`arabic_ok`**: 4/4 of observed retries fired here, all on legitimate Latin parentheticals (`(Privacy by Design)`, `(Secure Coding Standards)`, URLs `sbis.hrsd.gov.sa`). Legal/tech vocabulary needs these glosses.
- **`structure_ok`**: 95% of production traffic uses `prompt_mode_*` keys not in `check_structure`'s recognized list, so the gate is already silently lenient. Additionally `PROMPT_MODE_REG` explicitly tells the model "structure is flexible per question type" (`prompts.py:522-549`) — contradicting CRAC enforcement.

Both remain computed and ship in `ValidationReport.notes` for observability. Just demoted from hard gates.

---

## Scope

### Files changed

| File | Change |
|---|---|
| `agents/deep_search_v4/aggregator/postvalidator.py` | Narrow `passed` to `citation_ok AND gap_honesty_ok`. Docstring update. |
| `agents/deep_search_v4/aggregator/runner.py` | Delete current retry block (lines 105-129). Add self-correction loop. |
| `agents/deep_search_v4/aggregator/correction.py` | **NEW**. Per-gate dynamic-instruction builders. |
| `agents/deep_search_v4/aggregator/tests/test_agent.py` | Update existing retry tests, add new ones. |
| `agents/deep_search_v4/aggregator/tests/test_correction.py` | **NEW**. Unit tests for correction builder. |
| `agents/deep_search_v4/aggregator/logger.py` | Docstring touch-up (cosmetic). |
| `agents/deep_search_v4/monitor/run_monitor.py` | Docstring touch-up (cosmetic). |

### Files NOT changed

- `models.py` — `ValidationReport` shape stays. `passed` semantics narrow but field name same.
- `artifact_builder.py`, `agent_search/publisher.py`, `cli.py` — untouched. They consume `validation.cited_numbers` (still populated) / `notes` / `passed` for display, all still working.
- Frontend — no frontend consumer of any validation field.
- Database schema — no validation column.
- `deps.fallback_model` — kept; still used by DCR exception fallback at `runner.py:238`.

---

## Implementation details

### 1. `postvalidator.py` — gate demotion

Single-line change in `validate_llm_output` at line 537:

```python
# Before
passed = bool(citation_ok and arabic_ok and structure_ok and gap_honesty_ok)
# After
passed = bool(citation_ok and gap_honesty_ok)
```

Also: update the docstring on `ValidationReport.passed` to note the field reflects only the two hard gates from 2026-06 onward. `arabic_only_ok` and `structure_ok` remain individually surfaced.

No deletions. `check_arabic_only`, `check_structure`, `_LATIN_SENTENCE_RE` all stay — still useful in notes / monitor.

### 2. `correction.py` — NEW dynamic instruction builder

```python
"""Build per-gate corrective prompts for the aggregator self-correction loop.

Each builder addresses ONE failing hard gate; they compose into a single
Arabic corrective user message that the model sees in the follow-up turn.
"""
from __future__ import annotations

from .models import AggregatorInput, AggregatorLLMOutput, Reference, ValidationReport


def build_correction_prompt(
    validation: ValidationReport,
    agg_input: AggregatorInput,
    prev_output: AggregatorLLMOutput,
    references: list[Reference],
) -> str | None:
    """Compose a single Arabic correction message addressing all failing hard gates.

    Returns None when no hard gate failed.
    """
    blocks: list[str] = []
    if validation.dangling_citations:
        blocks.append(_citation_correction_block(validation, references))
    if not validation.gap_honesty_ok:
        gap_block = _gap_correction_block(agg_input, prev_output)
        if gap_block:
            blocks.append(gap_block)
    if not blocks:
        return None
    intro = (...)  # see code
    outro = (...)
    return intro + "\n\n" + "\n\n".join(blocks) + outro


def _citation_correction_block(validation, references) -> str: ...
def _gap_correction_block(agg_input, prev_output) -> str | None: ...
def _failing_gate_names(validation) -> list[str]: ...
```

Key design points:
- Pure functions. No I/O.
- Each gate block is independent — adding a new gate later is one more function + one more `if` in `build_correction_prompt`.
- Citation block names dangling refs + valid range. Gap block lists insufficient sub_queries the model already missed in `gaps[]`.
- Output asks for a fresh `final_result` carrying the corrected JSON, structure/content otherwise preserved.

### 3. `runner.py` — self-correction loop

Replace lines 105-129 with:

```python
# 4. Self-correct on hard-gate failure (one bounded turn).
if not validation.passed and primary_result is not None:
    correction_msg = build_correction_prompt(
        validation, agg_input, llm_output, references,
    )
    if correction_msg is not None:
        _emit(deps, {
            "event": "correction_triggered",
            "failing_gates": _failing_gate_names(validation),
            "notes": validation.notes,
        })
        try:
            corrected_output, corrected_raw = await _run_correction(
                deps, model_used, agg_input.prompt_key,
                prior_messages=primary_result.new_messages(),
                correction_msg=correction_msg,
            )
            llm_output = corrected_output
            raw_logs = {
                **{f"primary_{k}": v for k, v in raw_logs.items()},
                "correction": corrected_raw,
                "correction_notes": "\n".join(validation.notes),
            }
            corrected_final_refs = _compute_final_references(llm_output, references)
            validation = _validate(
                llm_output, references, agg_input, ref_to_sub_queries,
                agg_input.prompt_key,  # KEEP planner's chosen mode
                final_references=corrected_final_refs,
            )
            _emit(deps, {"event": "correction_done", "passed": validation.passed})
        except Exception as exc:
            logger.warning(
                "aggregator: self-correction failed: %s — shipping primary", exc,
            )
            _emit(deps, {"event": "correction_failed", "error": str(exc)})
            # validation stays the failing report; raw_logs unchanged
```

Then `final_prompt_key = agg_input.prompt_key` (always — no collapse).

#### `_run_primary_path` signature

Returns 4-tuple now: `(llm_output, model_used, raw_logs, primary_result)`. The new `primary_result` is the `AgentRunResult` from the single-shot path (`None` for DCR — see below).

#### `_run_correction` helper

```python
async def _run_correction(
    deps: AggregatorDeps,
    model_name: str,
    prompt_key: str,
    prior_messages: list,
    correction_msg: str,
) -> tuple[AggregatorLLMOutput, str]:
    agent = create_aggregator_agent(prompt_key=prompt_key, model_name=model_name)
    result = await agent.run(correction_msg, message_history=prior_messages)
    return result.output, _stringify_result(result)
```

#### DCR

Self-correction is skipped for DCR paths (`_run_primary_path` returns `primary_result=None` for DCR). DCR has its own exception fallback at `runner.py:230-243`; we leave it untouched. Zero DCR runs in last 14d of production traffic; not a real coverage gap.

#### SSE event hygiene

- **Removed**: `fallback_triggered`. Replaced by `correction_triggered`.
- **Added**: `correction_triggered` (before call), `correction_done` (after success), `correction_failed` (on exception).
- **Kept**: `aggregator_done` (with `passed` flag) — unchanged.

### 4. Test changes — `tests/test_agent.py`

**Delete**:
- `test_primary_fails_validation_triggers_fallback` (lines 237-256)

**Rewrite / rename**:
- `test_primary_exception_returns_placeholder_then_fallback_succeeds` →
  `test_primary_exception_returns_placeholder_no_correction`. Primary raises → degraded placeholder ships, no second call.

**Add**:
- `test_primary_fails_citation_then_self_correction_succeeds`: primary cites `[99]`, correction emits clean `[1]`. Assert one correction call, `model_used == primary_model`, `prompt_key == sample_input.prompt_key` (not collapsed), `passed == True`.
- `test_self_correction_fails_ships_primary_with_passed_false`: both primary and correction emit `[99]`. Assert correction called once (not twice), output ships, `passed == False`, `dangling_citations == [99]`.
- `test_arabic_only_failure_ships_without_correction`: synthesis contains `(Privacy by Design)`. Assert correction is NOT called, output ships, `passed == True` (gate demoted), `arabic_only_ok == False`.
- `test_structure_failure_ships_without_correction`: synthesis has wrong CRAC headings on `prompt_1`. `structure_ok == False`, `passed == True`. No correction.
- `test_combined_citation_and_gap_failure_single_correction`: primary fails both. Correction message contains both blocks. Assert single correction call.

**Keep untouched**:
- `test_empty_references_returns_placeholder`
- `test_single_shot_happy_path`
- `test_dcr_chain_runs_all_three_stages_when_prompt_3`
- `test_dcr_stage_failure_falls_back_to_single_shot` (DCR exception fallback unchanged)
- `test_artifact_built_when_enabled`, `test_artifact_skipped_when_disabled`
- `test_thinking_block_stripped_from_synthesis_md`
- `test_sse_events_emitted`

### 5. `tests/test_correction.py` — NEW

- `test_no_failures_returns_none`
- `test_citation_only_block_present` (verifies dangling numbers + valid range appear)
- `test_gap_only_block_present` (verifies sub_query indices + query text snippets appear)
- `test_both_blocks_concatenated` (verifies order: citation first, then gap)
- `test_gap_block_filters_already_mentioned_sub_queries`
- `test_intro_outro_in_arabic` (sanity that framing is Arabic and asks for `final_result`)

### 6. Cosmetic docstring updates

- `aggregator/logger.py:191`: extend doc mention of `'fallback_single'` to also note `'correction'`.
- `monitor/run_monitor.py:818`: replace mention of `prompt_fallback_single.md` with `prompt_correction.md`.

---

## Edge cases

| Case | Behavior |
|---|---|
| Primary passes validation | No correction. Ships primary. Unchanged from today. |
| Primary fails `arabic_ok` only | `passed=True` (demoted). Notes carry violation. Ships primary. **Saves 100% of current retries.** |
| Primary fails `structure_ok` only | Same as arabic_ok. |
| Primary fails `citation_ok` | Correction called with citation block. If passes, ships corrected. If fails, ships corrected with `passed=False`. |
| Primary fails `gap_honesty_ok` | Correction called with gap block. |
| Primary fails both hard gates | Correction with both blocks concatenated. One call. |
| Primary raises exception | Placeholder ships with `passed=False`. No correction. |
| Correction raises exception | Caught. Logged. Ships primary with original failing `ValidationReport`. |
| DCR primary | Self-correction skipped. DCR exception fallback at runner.py:230-243 unchanged. |
| `build_correction_prompt` returns `None` | Defensive — skip correction. Ships primary with failing report. |

---

## Pydantic AI spot-check (preflight)

Pinned to `pydantic-ai>=1.39.0,<2.0.0` (`backend/requirements.txt`). Installed: `1.39.0`.

Key behaviors to verify with a throwaway script BEFORE coding:

1. `agent.run(user_message, message_history=result.new_messages())` carries the prior user + assistant turn cleanly.
2. With structured `output_type` (Pydantic model + TextOutput salvager), the second run can emit a new `final_result` tool call and Pydantic AI parses it.
3. The aggregator agent uses `instructions=system_prompt` (not `system_prompt=`). `instructions` is added at every turn, NOT stored in history → no system-prompt duplication when we feed message_history back.

Action: write `scripts/_throwaway_pydantic_history_check.py` that:
- Builds a minimal Pydantic AI agent with a structured output type
- Runs once with a user message → captures result
- Runs again with a corrective message + `message_history=result.new_messages()`
- Prints the second result + the message thread

Delete the script after verification. Do NOT commit.

---

## Implementation order

1. Persist this plan doc.
2. Pydantic AI spot-check.
3. Create `correction.py` + `test_correction.py`. Run tests in isolation.
4. Narrow `postvalidator.py` `passed` definition.
5. Wire `runner.py` self-correction loop (`_run_primary_path` returns 4-tuple, `_run_correction` helper added, retry block deleted).
6. Update `test_agent.py` (delete + rewrite + add).
7. Cosmetic doc string touch-ups in logger.py + run_monitor.py.
8. Run full aggregator test suite. All green.

Target diff: ~250 lines added (correction module + tests), ~80 lines deleted (old retry + obsolete tests).

---

## Rollback

- `postvalidator.py` `passed` change: flip line 537 back. One-line revert.
- Self-correction block: single contiguous chunk in `runner.py`, easy `git revert` on the runner change alone.
- No DB / migration / frontend changes to undo.

---

## Follow-ups (not in this PR)

1. **Improve `_LATIN_SENTENCE_RE` to ignore parenthetical Latin / URLs / acronyms in quotes.** Currently it's noisy as a soft signal; tightening the regex would make `arabic_only_ok` notes more informative. Independent of this PR.
2. **Decide on `check_structure` for `prompt_mode_*` keys.** Current behavior (silently lenient) is accidentally aligned with `PROMPT_MODE_REG`'s "structure is flexible" intent. Could be left as-is, or actively replaced with a looser per-mode check ("must have at least one H2, must not begin with `<thinking>`"). Not load-bearing.
3. **Track `correction_triggered` rate over time** — if it climbs, we'll see a real signal (something is breaking) instead of cosmetic noise drowning it out.
