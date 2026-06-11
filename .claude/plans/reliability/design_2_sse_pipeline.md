# Design 2 — Messages/SSE Pipeline Hardening

Repo facts verified during exploration: Python 3.13 (`asyncio.timeout` available), single-worker uvicorn (`backend/Dockerfile:28` passes no `--workers`, but **uvicorn silently honors `WEB_CONCURRENCY`**), and `resolve_pause` **already returns bool** (`agents/paused_runs.py:135-155`) — the real gap for fix 3 is that all 10 call sites in the orchestrator discard it.

---

## Fix 1 — Overall pipeline timeout

### Where the timeout wraps: the producer, not the consumer

The timeout lives **inside `pipeline_producer()`** in `backend/app/services/message_service.py` (lines 416–608), wrapping the `async for event in handle_message(...)` loop — not the consumer loop at 630–644:

1. **Detach policy.** On client disconnect the consumer exits but the pipeline is deliberately detached to `_inflight_pipelines` (lines 674–693) and runs to completion in the background. A consumer-side timeout would not bound detached pipelines — the exact "hung LLM forever" hole. Producer-side, the bound holds whether or not anyone is watching.
2. **Heartbeats vs pipeline.** The consumer loop with `heartbeat_producer` keeps the *connection* alive indefinitely by design; that's correct. The thing to bound is pipeline progress.
3. **Pause interaction is free.** A pause (`ask_user`) ends `handle_message` naturally — the producer's `done` handler runs (`paused=True` branch, lines 533–541), sentinel enqueued (line 608), task completes. A producer-side timeout therefore **cannot fire on a paused run**. No special-casing needed; the resume turn is a fresh pipeline with its own fresh timeout.

### Value

Longest legitimate path: OCR (≤60s/attachment after fix 2) + memory pre-hook + inline summarize + router + deep_search_v4 phases + aggregator + publish ≈ 3–4 min observed. Set:

- `LUNA_PIPELINE_TIMEOUT_S` — new `Settings` field in `shared/config.py` (~line 114, feature-flags block), **default `420.0`** (7 min: 4-min worst case × ~1.75 headroom). Env-overridable for tests.

### Code sketch (message_service.py)

Module level (near line 64):

```python
_PIPELINE_TIMEOUT_MSG_AR = (
    "عذراً، استغرقت معالجة طلبك وقتاً أطول من المعتاد فتم إيقافها. "
    "يرجى المحاولة مرة أخرى."
)
_PIPELINE_TIMEOUT_PARTIAL_NOTE_AR = "\n\n_توقّف إكمال هذا الرد لتجاوز المهلة الزمنية المحددة._"
```

Rewrite `pipeline_producer` (line 416) — body unchanged, wrapped:

```python
async def pipeline_producer() -> None:
    nonlocal full_content, paused
    try:
        async with asyncio.timeout(get_settings().LUNA_PIPELINE_TIMEOUT_S):
            async for event in handle_message(...):
                # ... existing 420-line body unchanged ...
    except TimeoutError:
        _logfire.error(
            "message.stream.pipeline_timeout",
            conversation_id=conversation_id,
            assistant_message_id=assistant_msg_id,
            user_message_id=user_msg_id,
            timeout_s=get_settings().LUNA_PIPELINE_TIMEOUT_S,
            partial_chars=len(full_content),
            paused=paused,
        )
        # Quota/billing: LLM calls already made ARE already billed — the
        # collect_llm_calls scope inside handle_message flushes + settles on
        # the CancelledError path by contract (agents/utils/usage_sink.py:59-61).
        # Nothing to refund; the timeout only stops FUTURE spend.
        try:
            if paused:
                # Pause already happened but 'done' cleanup didn't run: the
                # question row exists; delete the empty placeholder (mirror
                # of lines 536-541).
                supabase.table("messages").delete().eq("message_id", assistant_msg_id).execute()
            elif full_content:
                supabase.table("messages").update({
                    "content": full_content + _PIPELINE_TIMEOUT_PARTIAL_NOTE_AR,
                    "metadata": {"kind": "pipeline_timeout", "partial": True},
                }).eq("message_id", assistant_msg_id).execute()
            else:
                supabase.table("messages").update({
                    "content": _PIPELINE_TIMEOUT_MSG_AR,
                    "metadata": {"kind": "pipeline_timeout"},
                }).eq("message_id", assistant_msg_id).execute()
        except Exception:
            logger.warning("pipeline_timeout: placeholder cleanup failed", exc_info=True)
        await queue.put(_sse_event("error", {
            "detail": _PIPELINE_TIMEOUT_MSG_AR,
            "code": "PIPELINE_TIMEOUT",
        }))
    except Exception as e:
        # ... existing generic handler (lines 604-606) unchanged ...
    finally:
        await queue.put(None)   # existing sentinel (line 608)
```

How each concern resolves:

- **Cancellation propagation**: `asyncio.timeout` cancels at the `__anext__` await; `CancelledError` unwinds through `collect_llm_calls.__exit__` → flush + quota settle (verified docstring contract, `usage_sink.py:59-61`).
- **`_active_runs` cleanup**: nothing to do. The timeout makes the producer task *finish*; `_clear_active_run` (lines 623–628) fires via `add_done_callback`.
- **Client disconnect during timeout**: consumer already detached; the error SSE event lands on an unread queue (harmless), but the DB placeholder update still runs, so the user sees the Arabic timeout message on reload. Dedup unblocks at the same moment.
- **Timeout vs `done` race**: the deadline only cancels while the body is suspended at an await; once `done` processed and the loop exited, the `async with` exits cleanly.
- **`message_count`** not incremented on timeout (the `done` handler owns that). Accepted drift — same as today's error path.

---

## Fix 2 — Mistral OCR timeout

### Insertion point

`agents/memory/ocr_extractor/mistral_ocr.py:63`:

```python
import asyncio  # add to imports

OCR_TIMEOUT_S = 60.0  # module constant next to OCR_MAX_PAGES (line 22)

    try:
        response = await asyncio.wait_for(
            client.ocr.process_async(
                model=model, document=document, pages=list(range(OCR_MAX_PAGES)),
            ),
            timeout=OCR_TIMEOUT_S,
        )
    finally:
        ...  # existing aclose() block unchanged — runs on the cancel path too
```

### What the turn does on timeout — proceed + Arabic status event

Current propagation (verified): `TimeoutError` escapes `ocr_document` → caught per-candidate at `agents/memory/ocr_extractor/runner.py:265-272` → row marked `metadata.ocr_status="failed"` with `ocr_error` → loop continues → `run_ocr_extraction` never raises. So **the turn already continues without extraction** — the only missing piece is telling the user. Failing the whole turn would be strictly worse: OCR runs pre-router and the answer may not even need the attachment. Marking `failed` (never retried) is right for timeouts too: leaving it retriable means every subsequent turn stalls 60s while Mistral is down.

**Surfacing**: change `run_ocr_extraction` to return its existing `OcrExtractionStats` (it already tracks `failed` and `filled_item_ids`) instead of `list[str]`. Sole production caller is `agents/orchestrator.py:1113` (grep for `run_ocr_extraction` before implementing — tests may also call it). In `_handle_message_inner` (orchestrator.py:1109-1116):

```python
    ocr_item_ids: list[str] = []
    _ocr_failed = 0
    try:
        ocr_stats = await asyncio.wait_for(
            run_ocr_extraction(supabase, conversation_id, user_id),
            timeout=180.0,   # whole-step budget: N hung attachments must not eat the 420s pipeline budget
        )
        ocr_item_ids = ocr_stats.filled_item_ids
        _ocr_failed = ocr_stats.failed
    except (asyncio.TimeoutError, Exception):
        logger.warning("OCR extraction step failed", exc_info=True)
        _ocr_failed = 1
    if _ocr_failed:
        yield {
            "type": "status",
            "text": "تعذّرت قراءة بعض المرفقات، وسيتابع المساعد الإجابة دون الاستناد إلى محتواها.",
        }
```

`status` events are already forwarded to the client by `message_service.py:498-501`. The outer 180s `wait_for` caps the worst case of k attachments × 60s each. Cancelling mid-step leaves un-attempted candidates unmarked — they retry next turn, acceptable.

---

## Fix 3 — `resolve_pause` made loud + stale-pause defense

### 3a. Loud wrapper

`resolve_pause` already returns `bool` — no signature change needed. Add a wrapper in `agents/orchestrator.py` near the pause helpers (~line 420):

```python
def _resolve_pause_loud(supabase: SupabaseClient, run_id: str, *, where: str) -> bool:
    """resolve_pause + Logfire alarm on failure. A failed DELETE means the next
    message will re-attach to a consumed pause — the stale-pause guard in
    _handle_message_inner is the recovery path; this event is the alert."""
    ok = resolve_pause(supabase, run_id)
    if not ok:
        _logfire.warning("paused_runs.resolve_failed", run_id=str(run_id), where=where)
    return ok
```

Replace **all 10 call sites** (verified by grep):

| line | context label (`where=`) |
|---|---|
| 497 | `abandon_unsupported_family` |
| 542 | `rehydrate_failed` |
| 617 | `writing_chained_pause_supersede` |
| 666 | `writing_resume_finally` |
| 752 | `planner_resume_failed` |
| 781 | `ds_chained_pause_supersede` |
| 792 | `planner_aborted` |
| 810 | `unexpected_planner_output` |
| 903 | `ds_resume_finally` |
| 1086 | `pause_expired` |

User-facing behavior when resolve fails mid-resume: **nothing in-band** — the answer already streamed by the time the `finally` at 903/666 runs. The risk is entirely the *next* message re-attaching, handled by 3b.

### 3b. Defensive stale check at re-attach

Insertion point: `_handle_message_inner`, pause branch at `orchestrator.py:1079-1100`. Invariant: a *live* pause's question message (inserted by `_record_deferred` at 977-990 with `metadata.kind="agent_question"` and `metadata.run_id`) is the most recent assistant message — **excluding this turn's empty placeholder**, which message_service inserts *before* `handle_message` is called (critical edge; without the exclusion every legitimate resume looks stale).

```python
def _pause_is_current(
    supabase: SupabaseClient,
    pending: dict,
    conversation_id: str,
    current_placeholder_id: str | None,
) -> bool:
    """True when the pause's question is still the conversation's last real
    assistant turn. Fail-OPEN on read errors (never drop a live pause on a
    flaky query)."""
    try:
        rows = (
            supabase.table("messages")
            .select("message_id, content, metadata")
            .eq("conversation_id", conversation_id)
            .eq("role", "assistant")
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        ).data or []
    except Exception:
        return True
    for row in rows:
        if current_placeholder_id and row.get("message_id") == current_placeholder_id:
            continue  # this turn's own empty placeholder
        if not (row.get("content") or "").strip() and not (row.get("metadata") or {}):
            continue  # orphaned empty placeholder from a failed pause-cleanup delete
        meta = row.get("metadata") or {}
        return (
            meta.get("kind") == "agent_question"
            and str(meta.get("run_id") or "") == str(pending.get("run_id") or "")
        )
    return True  # nothing visible to judge by — fail open
```

In `_handle_message_inner` (between lines 1080 and 1088):

```python
    pending = _find_awaiting_user(supabase, conversation_id, user_id)
    if pending and not _expired(pending) and not _pause_is_current(
        supabase, pending, conversation_id, assistant_message_id
    ):
        _logfire.warning("paused_runs.stale_pause_dropped",
                         run_id=str(pending.get("run_id", "")),
                         conversation_id=conversation_id)
        _resolve_pause_loud(supabase, str(pending.get("run_id", "")), where="stale_pause_guard")
        pending = None  # fall through to normal routing
```

Edge cases:
- **Chained pause** (writing 2nd `present_plan` round): the new pause row's question is the newest assistant message and `find_open_pause` orders by `asked_at desc` → run_ids match → not stale. Correct.
- **Question insert failed** in `_record_deferred` (best-effort): no question message exists → treated stale → routed fresh. Correct — the user never saw a question.
- **Stale check itself fails to delete** (`resolve_pause` False again): the guard re-fires on every subsequent message — the conversation is no longer stuck; only the Logfire warning repeats.

---

## Fix 4 — Artifact publish failure surfaced

### Point A — deep_search: `orchestrator.py:1683-1690`

```python
            except Exception as exc:
                logger.warning("deep_search artifact persist failed: %s", exc, exc_info=True)
                _logfire.error("deep_search.publish_failed",
                               conversation_id=input.conversation_id, error=str(exc)[:300])
                sse_events.append({
                    "type": "status",
                    "text": (
                        "تعذّر حفظ نتيجة البحث كبطاقة في مساحة العمل — "
                        "الإجابة معروضة في المحادثة لكنها لن تظهر ضمن المستندات."
                    ),
                })
```

`sse_events` is yielded by both consumers: fresh dispatch (`orchestrator.py:1407`) and resume (`orchestrator.py:849`) — one insertion covers both paths.

### Point B — writer: `agents/writer_planner/runner.py:561-563`

`publish_writer_result` failure currently raises out of `handle_writer_planner_turn` → generic error token, and the **drafted text is lost**. Minimal surfacing:

```python
        try:
            writer_output = await publish_writer_result(llm_output, publish_input, exec_deps)
        except Exception:
            logger.error("writer publish failed", exc_info=True)
            _logfire.error("writer.publish_failed", conversation_id=major_input.conversation_id)
            return WriterPlannerTurnResult(
                kind="completed",
                result=SpecialistResult(
                    output_item_id=None,
                    chat_summary="تعذّر حفظ المسودة في مساحة العمل. يرجى إعادة المحاولة.",
                    key_findings=[],
                    sse_events=[{"type": "status",
                                 "text": "تعذّر حفظ المسودة في مساحة العمل بعد اكتمال الكتابة."}],
                    model_used="writer_planner_decider",
                    tokens_in=0, tokens_out=0, per_phase_stats={},
                ),
            )
```

(The follow-on `template_save_offer` block at 575-612 reads `writer_output` — the early return cleanly skips it.)

---

## Fix 5 — Dedup hardening

### 5a. Startup guard (implement now)

`backend/Dockerfile:28` passes no `--workers`, but **uvicorn reads `WEB_CONCURRENCY` when the flag is absent** — a single Railway env-var change would silently break `_active_runs` (`message_service.py:64`) into N× double-billing. Insert into lifespan (`backend/app/main.py`, after `settings = get_settings()` ~line 60):

```python
    # Dedup invariant — message_service._active_runs is in-PROCESS memory.
    # uvicorn honors WEB_CONCURRENCY when --workers is absent (our Dockerfile
    # passes none). >1 worker = duplicate sends pass the dedup guard on the
    # other worker = double-billed pipelines. Hard-fail rather than run wrong.
    _workers = int(os.getenv("WEB_CONCURRENCY", "1") or "1")
    if _workers > 1:
        logger.critical(
            "WEB_CONCURRENCY=%d: in-flight send dedup is per-process; refusing "
            "to start multi-worker until Redis SET NX dedup ships (see "
            "message_service._active_runs).", _workers)
        raise RuntimeError("multi-worker boot blocked: in-process send dedup")
```

Hard-fail recommended: Railway's `restartPolicyType: ON_FAILURE / maxRetries 10` makes the misconfiguration loudly visible instead of silently double-billing.

### 5b. Redis `SET NX` version (documented follow-up — do NOT implement yet)

- **Key**: `luna:send_dedup:{conversation_id}`; **value**: `assistant_msg_id` (so a second worker rejecting a duplicate can `GET` the in-flight assistant message id and emit the same `duplicate` SSE payload the in-memory path emits at `message_service.py:263-267`).
- **Acquire**: `SET key assistant_msg_id NX EX 600` immediately before reserving the in-memory slot. TTL 600s > pipeline timeout 420s (fix 1) — **fix 1 is a hard prerequisite**: the TTL is only safe because the pipeline is now guaranteed to end before it.
- **Release**: in `_clear_active_run` (existing done callback, line 623) — compare-and-delete (Lua `if GET==argv then DEL`) so a slow old run can't delete a newer run's key after TTL expiry.
- **Redis down**: fall back to the in-memory dict alone + one `_logfire.warning("send_dedup.redis_unavailable")`. Keep the in-memory registry regardless — it holds the task handle for detach bookkeeping; Redis only adds the cross-process guarantee.

---

## Ordering and dependencies

1. **Fix 2 (OCR timeout)** — smallest, standalone, bounds the pre-router leg so fix 1's 420s budget is meaningful. Ship first.
2. **Fix 1 (pipeline timeout)** — headline fix; value assumes fix 2 is in.
3. **Fix 3 (loud resolve + stale guard)** — standalone.
4. **Fix 4 (publish surfacing)** — standalone; trivial once strings agreed.
5. **Fix 5a (worker guard)** — standalone, 10 lines. **5b (Redis dedup)** depends on fix 1 (TTL safety) — backlog.

Fixes 3 and 4 both touch `orchestrator.py`; land as separate commits to keep the 10-call-site rename reviewable.

## Test / verification plan

- **Hung-LLM simulation (fix 1)** — env-gated sleep injection at the top of `_handle_message_inner` (orchestrator.py:1078): `if (_h := float(os.getenv("LUNA_TEST_HANG_S", "0") or 0)) > 0: await asyncio.sleep(_h)`. Deterministic, sits inside the `collect_llm_calls` scope, exercises the exact cancellation path. Run locally with `LUNA_PIPELINE_TIMEOUT_S=10 LUNA_TEST_HANG_S=60`, POST a message with `curl -N`, assert: heartbeats every 15s, `error` event with `code=PIPELINE_TIMEOUT` at ~10s, placeholder row updated to the Arabic timeout text, and a **second send immediately succeeds** (proves `_active_runs` cleared). Repeat with the client killed mid-wait and verify the DB update still lands.
- **Pause non-interaction (fix 1)**: trigger an `ask_user` pause with `LUNA_PIPELINE_TIMEOUT_S=600`; confirm no `pipeline_timeout` event fires and the resume works.
- **OCR (fix 2)**: mirror env gate `LUNA_TEST_OCR_HANG_S`; upload a PDF, send a message, assert the Arabic status event arrives, the turn completes, and `metadata.ocr_status="failed"` with `ocr_error` on the row.
- **Fix 3**: unit test patching `resolve_pause` → `False`, assert `paused_runs.resolve_failed` warning. Integration for the stale guard: manually INSERT a `paused_runs` row whose run_id matches no assistant message, send a message, assert it routes fresh and the row is gone. Regression: a *genuine* pause must still resume (asserts the placeholder-exclusion logic).
- **Fix 4**: monkeypatch `publish_search_result` to raise; assert the `status` SSE event appears after the answer tokens and `deep_search.publish_failed` is in Logfire.
- **Fix 5a**: `WEB_CONCURRENCY=2 uvicorn backend.app.main:app` locally → boot fails with the critical log.

## Critical files
- `backend/app/services/message_service.py` (fix 1)
- `agents/orchestrator.py` (fixes 2-surfacing, 3, 4A)
- `agents/memory/ocr_extractor/mistral_ocr.py` + `runner.py` (fix 2)
- `backend/app/main.py` (fix 5a)
- `agents/writer_planner/runner.py` (fix 4B)
- `shared/config.py` (`LUNA_PIPELINE_TIMEOUT_S`)
