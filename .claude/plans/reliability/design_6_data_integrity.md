# Design 6 — Data Integrity + API Hygiene

**Scope:** transactional RPCs for case writes, list_cases N+1 kill, atomic preferences merge, workspace pagination, references fan-out semaphore, Idempotency-Key sketch, silent-failure sweep.

**Repo facts verified:**
- Backend always talks to Postgres with the **service-role** key (`shared/db/client.py:56-62`), which has `bypassrls`. New RPCs use **`SECURITY INVOKER`** — they inherit service-role privileges when called by the backend; if an `anon`/`authenticated` caller ever reached them, RLS would still apply (defense in depth). Additionally `REVOKE ... FROM PUBLIC` so only `service_role` can execute the write RPCs.
- House migration style (from 064, 054): numbered file, header comment with rationale + "This migration is idempotent", `CREATE OR REPLACE FUNCTION`, `SET search_path = public`, `COMMENT ON FUNCTION`, explicit `GRANT`.
- Latest migration on disk is **064** (055 is duplicated — `055_user_templates.sql` and `055_model_pricing_unify.sql` — do not repeat that). New migrations: **065** and **066**.
- `supabase.rpc(name, params).execute()` is the established call pattern (`agents/deep_search_v4/case_search/search.py:165`).
- `user_preferences.user_id` is `UNIQUE` on disk (migration 020) — required for `ON CONFLICT (user_id)`. **Must be verified live.**

> **PROJECT CONSTRAINT (both migrations):** migration files on disk are NOT all applied to prod. Each migration must be applied via Supabase MCP (`mcp__supabase__apply_migration`) with the live schema verified first: `list_tables` for `lawyer_cases`/`conversations`/`case_documents`/`user_preferences` columns, confirm `user_preferences_user_id_key` unique constraint exists live, and `SELECT proname FROM pg_proc WHERE proname IN ('create_case_with_conversation','soft_delete_case_cascade','case_counts','merge_preferences')` for signature collisions.

---

## 1. Migration 065 — transactional case RPCs + batched counts

`shared/db/migrations/065_case_transactional_rpcs.sql`

```sql
-- Migration 065: Transactional case RPCs + batched counts
--
-- Fixes from reliability audit 2026-06-11 (§2 CRUD):
--   1. create_case + first conversation were two PostgREST round-trips with
--      no transaction (case_service.py:195-224) — failure between them left
--      an orphaned case with zero conversations.
--   2. delete_case soft-deleted the case then its conversations in two
--      statements (case_service.py:449-458) — failure between them left
--      live conversations under a deleted case.
--   3. list_cases did 1 + 2N count queries (41 round-trips per page of 20).
--      case_counts() returns both counts for a batch of case_ids in ONE call.
--
-- SECURITY INVOKER on purpose: the backend connects as service_role
-- (bypassrls); EXECUTE revoked from PUBLIC so PostgREST anon/authenticated
-- roles cannot call the write RPCs.
--
-- This migration is idempotent (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION public.create_case_with_conversation(
    p_user_id     UUID,
    p_case_name   TEXT,
    p_case_type   TEXT DEFAULT 'عام',
    p_description TEXT DEFAULT NULL,
    p_case_number TEXT DEFAULT NULL,
    p_court_name  TEXT DEFAULT NULL,
    p_priority    TEXT DEFAULT 'medium'
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_case lawyer_cases%ROWTYPE;
    v_conversation_id UUID;
BEGIN
    INSERT INTO public.lawyer_cases
        (lawyer_user_id, case_name, case_type, priority, status,
         description, case_number, court_name)
    VALUES
        (p_user_id, p_case_name,
         p_case_type::case_type_enum,
         p_priority::case_priority_enum,
         'active'::case_status_enum,
         p_description, p_case_number, p_court_name)
    RETURNING * INTO v_case;

    INSERT INTO public.conversations (user_id, case_id, title_ar)
    VALUES (p_user_id, v_case.case_id,
            left('محادثة - ' || p_case_name, 500))
    RETURNING conversation_id INTO v_conversation_id;

    -- Whole function body is one transaction: any failure rolls back both.
    RETURN jsonb_build_object(
        'case', to_jsonb(v_case) - 'embedding',
        'first_conversation_id', v_conversation_id
    );
END;
$$;

COMMENT ON FUNCTION public.create_case_with_conversation(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) IS
    'Atomically inserts a lawyer_case and its first conversation. '
    'Replaces the two-step non-transactional write in case_service.create_case.';

REVOKE ALL ON FUNCTION public.create_case_with_conversation(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_case_with_conversation(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) TO service_role;

CREATE OR REPLACE FUNCTION public.soft_delete_case_cascade(
    p_case_id UUID,
    p_user_id UUID
)
RETURNS INT
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
    v_case_rows INT;
    v_conv_rows INT;
BEGIN
    UPDATE public.lawyer_cases
       SET deleted_at = v_now, updated_at = v_now
     WHERE case_id = p_case_id
       AND lawyer_user_id = p_user_id
       AND deleted_at IS NULL;
    GET DIAGNOSTICS v_case_rows = ROW_COUNT;

    IF v_case_rows = 0 THEN
        RETURN -1;  -- not found / not owned / already deleted → caller maps to 404
    END IF;

    UPDATE public.conversations
       SET deleted_at = v_now, updated_at = v_now
     WHERE case_id = p_case_id
       AND user_id = p_user_id
       AND deleted_at IS NULL;
    GET DIAGNOSTICS v_conv_rows = ROW_COUNT;

    RETURN v_conv_rows;
END;
$$;

COMMENT ON FUNCTION public.soft_delete_case_cascade(UUID, UUID) IS
    'Atomically soft-deletes a case and all its live conversations. '
    'Returns -1 if the case was not found/owned (caller maps to 404), '
    'else the number of conversations soft-deleted.';

REVOKE ALL ON FUNCTION public.soft_delete_case_cascade(UUID, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.soft_delete_case_cascade(UUID, UUID) TO service_role;

CREATE OR REPLACE FUNCTION public.case_counts(p_case_ids UUID[])
RETURNS TABLE (
    case_id            UUID,
    conversation_count BIGINT,
    document_count     BIGINT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT
        ids.case_id,
        COALESCE(conv.cnt, 0) AS conversation_count,
        COALESCE(doc.cnt, 0)  AS document_count
    FROM unnest(p_case_ids) AS ids(case_id)
    LEFT JOIN (
        SELECT c.case_id, count(*) AS cnt
        FROM public.conversations c
        WHERE c.case_id = ANY(p_case_ids) AND c.deleted_at IS NULL
        GROUP BY c.case_id
    ) conv USING (case_id)
    LEFT JOIN (
        SELECT d.case_id, count(*) AS cnt
        FROM public.case_documents d
        WHERE d.case_id = ANY(p_case_ids) AND d.deleted_at IS NULL
        GROUP BY d.case_id
    ) doc USING (case_id);
$$;

COMMENT ON FUNCTION public.case_counts(UUID[]) IS
    'Batched non-deleted conversation + document counts per case. '
    'Replaces the per-case count loop in case_service.list_cases (1+2N → 2 queries).';

REVOKE ALL ON FUNCTION public.case_counts(UUID[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.case_counts(UUID[]) TO service_role;
```

Notes:
- `to_jsonb(v_case) - 'embedding'` strips the vector from the response payload (free improvement; `CaseDetail` doesn't declare `embedding`).
- Enum casts raise `invalid_text_representation` for bad values — Python validates first; a cast failure means Python/DB enum drift and a 500 is correct.
- The RPC re-checks ownership in its WHERE clause, closing the verify→delete TOCTOU window.

**N+1 decision: RPC (a) over two `.in_()` queries (b).** (b) transfers one row per conversation/document (a case with 500 conversations ships 500 ids to count them; `count="exact"` with `head=True` can't be grouped), and it's two round-trips on a sync client. The RPC is one round-trip returning ≤ per_page rows, the migration ships anyway for the transactional functions, and it's reusable by `update_case`/`update_case_status`.

## 2. Migration 066 — atomic preferences merge

`shared/db/migrations/066_merge_preferences_rpc.sql`

```sql
-- Migration 066: merge_preferences RPC — atomic JSONB patch merge
--
-- PATCH /preferences was read-merge-write with no lock
-- (preferences_service.py:81-130): two concurrent PATCHes silently dropped
-- one patch. The frontend sends PARTIAL patches, so last-write-wins on the
-- whole blob (plain upsert) is NOT acceptable — the merge must happen in
-- SQL: preferences || patch inside one INSERT..ON CONFLICT statement.
--
-- Shallow merge (||) deliberately matches the previous Python semantics
-- {**existing, **patch}: top-level keys in the patch replace, others persist.
--
-- The existing update_user_preferences_updated_at trigger (migration 020)
-- fires on the conflict-update path, keeping updated_at fresh.
--
-- PRE-FLIGHT (live schema): confirm UNIQUE constraint on
-- user_preferences.user_id exists in prod before applying.
--
-- This migration is idempotent.

CREATE OR REPLACE FUNCTION public.merge_preferences(
    p_user_id UUID,
    p_patch   JSONB
)
RETURNS jsonb
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
    INSERT INTO public.user_preferences (user_id, preferences)
    VALUES (p_user_id, COALESCE(p_patch, '{}'::jsonb))
    ON CONFLICT (user_id) DO UPDATE
        SET preferences = user_preferences.preferences || EXCLUDED.preferences
    RETURNING jsonb_build_object(
        'user_id', user_id,
        'preferences', preferences
    );
$$;

COMMENT ON FUNCTION public.merge_preferences(UUID, JSONB) IS
    'Atomic upsert-merge of a partial preferences patch into the user''s JSONB '
    'blob (shallow merge, patch keys win). Replaces the racy read-merge-write '
    'in preferences_service.update_preferences.';

REVOKE ALL ON FUNCTION public.merge_preferences(UUID, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.merge_preferences(UUID, JSONB) TO service_role;
```

Decision: RPC, not plain `.upsert(on_conflict="user_id")` — a client-side upsert can only send a full value, so two concurrent partial PATCHes would still drop keys. `preferences || patch` inside the single statement is race-free under READ COMMITTED — `ON CONFLICT DO UPDATE` takes the row lock and re-reads the current value.

## 3. Python service changes

### 3a. `case_service.create_case`

```python
    result = supabase.rpc("create_case_with_conversation", {
        "p_user_id": user_id, "p_case_name": case_name, "p_case_type": case_type,
        "p_description": description, "p_case_number": case_number,
        "p_court_name": court_name, "p_priority": priority,
    }).execute()
    payload = result.data  # {"case": {...}, "first_conversation_id": "..."}
    # ... 500 on empty; audit log stays OUTSIDE the transaction (best-effort by design)
    return {"case": {**payload["case"], "conversation_count": 1, "document_count": 0},
            "first_conversation_id": payload["first_conversation_id"]}
```

### 3b. `case_service.delete_case`

```python
    _verify_case_ownership(supabase, case_id, user_id)  # clean 404 before write
    result = supabase.rpc("soft_delete_case_cascade",
                          {"p_case_id": case_id, "p_user_id": user_id}).execute()
    if result.data == -1:
        # deleted concurrently between verify and RPC — treat as already done
        raise LunaHTTPException(status_code=404, code=ErrorCode.CASE_NOT_FOUND,
                                detail="القضية غير موجودة")
    write_audit_log(...)
```

### 3c. `case_service.list_cases` — N+1 kill

```python
    counts: dict[str, dict] = {}
    if cases:
        try:
            counts_result = supabase.rpc(
                "case_counts", {"p_case_ids": [c["case_id"] for c in cases]}).execute()
            counts = {row["case_id"]: row for row in (counts_result.data or [])}
        except Exception as e:
            logger.exception("Error fetching case counts: %s", e)
            raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR,
                                    detail="حدث خطأ أثناء جلب القضايا")
    enriched = [{**case,
                 "conversation_count": counts.get(case["case_id"], {}).get("conversation_count", 0),
                 "document_count": counts.get(case["case_id"], {}).get("document_count", 0)}
                for case in cases]
```

2 queries per page regardless of N (down from 41). Per-case helpers dropped from this path. Also update `update_case` (lines 379-380) and `update_case_status` (lines 423-424) to one `case_counts([case_id])` call each.

### 3d. `preferences_service.update_preferences`

```python
def update_preferences(supabase, auth_id, preferences: dict) -> dict:
    """Atomically merge a partial preferences patch (RPC merge_preferences)."""
    user_id = get_user_id(supabase, auth_id)
    try:
        result = supabase.rpc("merge_preferences",
                              {"p_user_id": user_id, "p_patch": preferences}).execute()
    except Exception as e:
        logger.exception("Error merging preferences: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED,
                                detail="حدث خطأ أثناء تحديث الإعدادات")
    if not result.data:
        raise LunaHTTPException(status_code=500, code=ErrorCode.PREFERENCES_FAILED,
                                detail="حدث خطأ أثناء تحديث الإعدادات")
    return result.data
```

`PreferencesResponse` declares exactly `user_id` + `preferences` — the RPC's jsonb shape matches; no contract change.

## 4. Workspace list pagination

`workspace_service.py:170-225` — both list functions get `limit`/`offset` kwargs; routes get `Query` params. Default `limit=100` preserves current behavior for normal users. **No breaking change:** response model stays `WorkspaceItemListResponse {items, total}`; `total` upgrades from `len(items)` to the true count via `count="exact"` so clients detect truncation.

```python
def list_workspace_items_by_conversation(
    supabase, auth_id, conversation_id, *, limit: int = 100, offset: int = 0,
) -> tuple[list[dict], int]:
    user_id = get_user_id(supabase, auth_id)
    limit = max(1, min(limit, 200)); offset = max(0, offset)
    result = (supabase.table("workspace_items")
        .select("*", count="exact")
        .eq("user_id", user_id).eq("conversation_id", conversation_id)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute())
    return result.data or [], result.count or 0
```

Route: `limit: int = Query(default=100, ge=1, le=200)`, `offset: int = Query(default=0, ge=0)`. Mirror on the case variant. Offset-based (not cursor) is sufficient: items are mostly append-only per conversation and page 2 is rare.

## 5. References fan-out semaphore

`references_service.py:387-409` (`_attach_source_views`):

```python
_SOURCE_VIEW_CONCURRENCY = 5

async def _attach_source_views(supabase, pending) -> None:
    if not pending:
        return
    sem = asyncio.Semaphore(_SOURCE_VIEW_CONCURRENCY)

    async def _one(shell):
        async with sem:
            try:
                return await build_source_view(supabase, shell)
            except Exception as exc:  # noqa: BLE001
                logger.warning("references_service: build_source_view(%s) failed: %s",
                               getattr(shell, "ref_id", "?"), exc)
                return None

    views = await asyncio.gather(*(_one(shell) for _, shell in pending))
    for (ref, _), view in zip(pending, views):
        if view is not None:
            ref.source_view = view
```

Semaphore created per-call (not module-level) so it binds to the running event loop — important because this codebase mixes loops via `asyncio.to_thread`.

## 6. Idempotency-Key — backlog sketch (BL-18)

For duplicate-creating workspace endpoints (notes, references add, upload init, from-document):
- Client sends optional `Idempotency-Key: <uuidv4>` header.
- FastAPI dependency: key = `idem:{user_id}:{route_name}:{header_value}`; `redis.set(key, "PENDING", nx=True, ex=86400)` — SETNX wins → proceed, then store created item_id (`xx=True`). SETNX loses: value is item_id → fetch + return it (200, replay); value is `"PENDING"` → 409 `REQUEST_IN_FLIGHT`.
- In-memory fallback when Redis is None (valid under the single-worker constraint; invalid the day `--workers>1` ships — document with the send-dedup caveat).
- No header → behave exactly as today. Zero breaking change.

## 7. Silent-failure sweep

| Function | Today | Change |
|---|---|---|
| `get_case_detail` (case_service.py:285-289) | conversation fetch failure swallowed → stats lie | Raise 500 "حدث خطأ أثناء جلب تفاصيل القضية" |
| `_count_conversations` (502-514) | fake 0 | **Deleted** — no callers after `case_counts` adoption |
| `_count_documents` (517-529) | fake 0 | Remaining caller `get_case_detail:293` — fold into the same `case_counts([case_id])` call (preferred) or propagate 500 |
| `_count_memories` (532-544) | fake 0 | Only caller `get_case_detail:294` — propagate as 500 (optionally add memory_count to case_counts later) |
| `update_case` / `update_case_status` (379-380, 423-424) | call fake-0 helpers | Switch to `case_counts([case_id])` with error propagation |

`preferences_service.get_detail_level` keeps swallow-and-default **deliberately** — the chat dispatch path must survive a broken preferences row; different contract than CRUD endpoints.

## 8. Breaking-change analysis

- **`CaseSummary.conversation_count`/`document_count`**: stay `int = 0`, required-in-output — **no model change**. The semantic change: counts failure is now a 500 instead of silent `0` (the audit's explicit intent).
- **`CaseCreateResponse.first_conversation_id`**: previously could be `None`; with the RPC it's always a real UUID or the request 500s. Strictly safer.
- **`PreferencesResponse`**: unchanged.
- **`WorkspaceItemListResponse`**: shape unchanged; `total` becomes true count. Defaults keep one-page behavior identical.
- **DELETE /cases/{id}**: still 204; the -1→404 path only fires on a genuine concurrent-delete race.

## 9. Implementation sequence

1. **Pre-flight (live DB, read-only):** `list_tables` + `pg_proc` name check + confirm `user_preferences.user_id` UNIQUE live.
2. Apply **065** via Supabase MCP; smoke-test (`SELECT public.case_counts(ARRAY[]::uuid[]);` etc.).
3. Apply **066**; smoke-test `merge_preferences` with a throwaway row in a rolled-back transaction.
4. Python changes (case_service, preferences_service) in one PR — migrations first.
5. Workspace pagination + semaphore + silent-failure sweep in a second PR (no DB dependency).
6. Idempotency-Key stays backlog.

## 10. Verification

**Preferences race (key test):** fire two genuinely concurrent partial PATCHes (`{"detail_level": "high"}` and `{"language": "ar"}`) after seeding `{"theme": "dark"}`; final blob must contain all three keys. Run 50 iterations — old code fails probabilistically, RPC cannot.

**create_case atomicity:** on a Supabase branch (or rolled-back transaction), add a temporary `BEFORE INSERT ON conversations` trigger that raises; call the RPC; assert it errors AND no `lawyer_cases` row persists. Drop trigger.

**delete cascade:** create case via RPC, add second conversation, call cascade → returns 2; both conversations + case have `deleted_at`; call again → -1.

**N+1:** Logfire span counts on `GET /api/v1/cases` before/after (~41 → ~3 round-trips); counts in response match ground truth for seeded user.

**Pagination:** seed 120 items; default GET returns 100 with `total=120`; `?limit=50&offset=100` returns 20.

## Critical files
- `backend/app/services/case_service.py`, `preferences_service.py`, `workspace_service.py` (+ routes), `references_service.py`
- `shared/db/migrations/065_case_transactional_rpcs.sql`, `066_merge_preferences_rpc.sql` — **apply via Supabase MCP, verify live schema first**
