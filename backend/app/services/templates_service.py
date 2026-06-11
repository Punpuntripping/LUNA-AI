"""
Per-user markdown template business logic ("قوالبي").

Targets the migration-055 table:

    table: ``user_templates``
    PK:    ``template_id``
    cols:  ``user_id`` (FK→users.user_id), ``title``, ``content_md``,
           ``created_by`` ('user'|'agent'), ``metadata`` jsonb,
           ``created_at``, ``updated_at``, ``deleted_at``

This is DISTINCT from the global ``system_templates`` table — every query here
is scoped to the internal ``user_id`` resolved from the Supabase ``auth_id``,
exactly like ``preferences_service``. Soft deletes only (``deleted_at``).

All database queries go through the sync Supabase client. All error messages
are Arabic.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services.case_service import get_user_id

logger = logging.getLogger(__name__)


# ============================================
# INGEST CONCURRENCY LIMITER
# ============================================

# An attached document can be large; the ingester LLM call takes seconds. Cap
# how many ingests a single user can have in flight so a fat-fingered burst (or
# a buggy retry loop) can't fan out N expensive pipeline runs at once.
INGEST_CONCURRENCY_LIMIT = 2
_INGEST_CONC_TTL_S = 120          # > 45s LLM timeout: a leaked slot self-heals
_INGEST_CONC_KEY = "ingest:conc:{user_id}"

_local_counts: dict[str, int] = {}          # single-worker in-memory fallback
_local_lock = asyncio.Lock()

# Arabic message surfaced (NOT a 429 — this endpoint's contract is never-5xx;
# the chip renders this in place via {ok: false, error}).
INGEST_TOO_MANY_AR = (
    "لديك عمليات تحويل قوالب قيد التنفيذ بالفعل. "
    "يرجى الانتظار حتى تكتمل ثم المحاولة مجددًا."
)


class IngestConcurrencyExceeded(Exception):
    """Raised when the per-user in-flight ingest limit is already reached."""


async def _acquire_ingest_slot(redis, user_id: str) -> str:
    """Reserve one in-flight ingest slot for ``user_id``.

    Returns ``'redis'`` | ``'local'`` (the mode ``_release_ingest_slot`` needs).
    Raises :class:`IngestConcurrencyExceeded` at the limit. Redis is the shared
    counter across workers; a Redis hiccup degrades to a single-worker in-memory
    fallback rather than blocking the user.
    """
    if redis is not None:
        try:
            key = _INGEST_CONC_KEY.format(user_id=user_id)
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, _INGEST_CONC_TTL_S)
            n, _ = await pipe.execute()
            if int(n) > INGEST_CONCURRENCY_LIMIT:
                try:
                    await redis.decr(key)
                except Exception:
                    pass     # TTL self-heals
                raise IngestConcurrencyExceeded()
            return "redis"
        except IngestConcurrencyExceeded:
            raise
        except Exception as e:             # Redis hiccup → degrade, don't block
            logger.warning(
                "ingest limiter: redis failed, in-memory fallback: %s", e
            )
    async with _local_lock:
        if _local_counts.get(user_id, 0) >= INGEST_CONCURRENCY_LIMIT:
            raise IngestConcurrencyExceeded()
        _local_counts[user_id] = _local_counts.get(user_id, 0) + 1
    return "local"


async def _release_ingest_slot(redis, user_id: str, mode: str) -> None:
    """Release the slot acquired in ``mode``. Best-effort: a Redis leak is
    healed by the key's TTL; the in-memory path is decremented under lock."""
    if mode == "redis" and redis is not None:
        try:
            await redis.decr(_INGEST_CONC_KEY.format(user_id=user_id))
        except Exception:
            pass             # TTL self-heals the leak
        return
    async with _local_lock:
        n = _local_counts.get(user_id, 0) - 1
        if n <= 0:
            _local_counts.pop(user_id, None)
        else:
            _local_counts[user_id] = n


# ============================================
# USER TEMPLATE CRUD
# ============================================


def list_templates(
    supabase: SupabaseClient,
    auth_id: str,
) -> list[dict]:
    """List the user's templates (newest-updated first). Excludes soft-deleted."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("user_templates")
            .select("*")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .order("updated_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.exception("Error listing user_templates: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.TEMPLATE_FAILED,
            detail="حدث خطأ أثناء جلب القوالب",
        )

    return result.data or []


def get_template(
    supabase: SupabaseClient,
    auth_id: str,
    template_id: str,
) -> dict:
    """Get a single template. 404 (Arabic) if not found or not owned."""
    user_id = get_user_id(supabase, auth_id)

    try:
        result = (
            supabase.table("user_templates")
            .select("*")
            .eq("template_id", template_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.exception("Error fetching user_template: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.TEMPLATE_FAILED,
            detail="حدث خطأ أثناء جلب القالب",
        )

    if result is None or result.data is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.TEMPLATE_NOT_FOUND,
            detail="القالب غير موجود",
        )

    return result.data


def create_template(
    supabase: SupabaseClient,
    auth_id: str,
    title: str,
    content_md: str = "",
) -> dict:
    """Create a user-authored template (``created_by='user'``)."""
    user_id = get_user_id(supabase, auth_id)

    payload = {
        "user_id": user_id,
        "title": title,
        "content_md": content_md,
        "created_by": "user",
    }

    try:
        result = supabase.table("user_templates").insert(payload).execute()
    except Exception as e:
        logger.exception("Error creating user_template: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.TEMPLATE_FAILED,
            detail="حدث خطأ أثناء إنشاء القالب",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.TEMPLATE_FAILED,
            detail="حدث خطأ أثناء إنشاء القالب",
        )

    return result.data[0]


def update_template(
    supabase: SupabaseClient,
    auth_id: str,
    template_id: str,
    title: Optional[str] = None,
    content_md: Optional[str] = None,
) -> dict:
    """Update a template's title/content. Ownership verified first.

    Raises:
        400 if no field is provided.
        404 if the template is missing or not owned by this user.
    """
    user_id = get_user_id(supabase, auth_id)

    # Existence + ownership check (raises 404 if not owned).
    get_template(supabase, auth_id, template_id)

    update_data: dict = {}
    if title is not None:
        update_data["title"] = title
    if content_md is not None:
        update_data["content_md"] = content_md

    if not update_data:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.NO_UPDATE_DATA,
            detail="لم يتم تقديم أي بيانات للتحديث",
        )

    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("user_templates")
            .update(update_data)
            .eq("template_id", template_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error updating user_template: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.TEMPLATE_FAILED,
            detail="حدث خطأ أثناء تحديث القالب",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.TEMPLATE_NOT_FOUND,
            detail="القالب غير موجود",
        )

    return result.data[0]


async def ingest_template(
    supabase: SupabaseClient,
    auth_id: str,
    item_id: str,
    redis=None,
) -> dict:
    """Clean ONE attached workspace item into a reusable قوالبي template.

    Resolves the internal ``user_id`` from ``auth_id``, reserves a per-user
    in-flight ingest slot (``INGEST_CONCURRENCY_LIMIT``), builds the ingester
    deps (with a short-lived httpx client like the other agent entry points),
    opens a ``collect_llm_calls`` scope so the ``template.ingest`` cost lands in
    the ``llm_calls`` ledger, and runs the dedicated ingester pipeline directly
    (NO router/orchestrator).

    Returns a plain dict ready for ``IngestTemplateResponse``:
        success → ``{"ok": True, "template_id": ..., "title": ...}``
        failure → ``{"ok": False, "error": "<Arabic>"}``
        at limit → ``{"ok": False, "error": INGEST_TOO_MANY_AR}``

    Never raises for the ingester's own failures (or the concurrency cap) —
    those collapse into the Arabic ``error`` so the frontend chip can render it
    in place, preserving this endpoint's never-5xx contract.
    """
    import httpx

    from agents.memory.template_ingester import (
        build_ingester_deps,
        handle_template_ingestion,
    )
    from agents.utils.usage_sink import collect_llm_calls

    user_id = get_user_id(supabase, auth_id)

    try:
        mode = await _acquire_ingest_slot(redis, user_id)
    except IngestConcurrencyExceeded:
        return {"ok": False, "error": INGEST_TOO_MANY_AR}

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            deps = build_ingester_deps(
                supabase=supabase,
                http_client=http_client,
                user_id=user_id,
            )
            # Capture the ingest LLM call into the per-call cost ledger.
            with collect_llm_calls(
                supabase,
                conversation_id=None,
                user_id=user_id,
            ):
                result = await handle_template_ingestion(item_id, deps)
    finally:
        await _release_ingest_slot(redis, user_id, mode)

    if result.ok:
        return {
            "ok": True,
            "template_id": result.template_id,
            "title": result.title,
        }
    return {"ok": False, "error": result.error_ar}


def delete_template(
    supabase: SupabaseClient,
    auth_id: str,
    template_id: str,
) -> None:
    """Soft delete a template (set ``deleted_at=now()``)."""
    user_id = get_user_id(supabase, auth_id)
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = (
            supabase.table("user_templates")
            .update({"deleted_at": now, "updated_at": now})
            .eq("template_id", template_id)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as e:
        logger.exception("Error deleting user_template: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.TEMPLATE_FAILED,
            detail="حدث خطأ أثناء حذف القالب",
        )

    if not result.data:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.TEMPLATE_NOT_FOUND,
            detail="القالب غير موجود",
        )


__all__ = [
    "list_templates",
    "get_template",
    "create_template",
    "update_template",
    "delete_template",
    "ingest_template",
    "IngestConcurrencyExceeded",
    "INGEST_TOO_MANY_AR",
    "INGEST_CONCURRENCY_LIMIT",
]
