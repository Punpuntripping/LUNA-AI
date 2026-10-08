"""
Templates API routes — /api/v1/  ("قوالبي" — per-user markdown templates).

Two scopes share these endpoints:
  * ``scope='user'``   — قالب خاص, a user_templates row owned by the caller.
  * ``scope='system'`` — قالب عام, a repo file (agents/writer/system_templates.py)
    every user sees read-only. PATCH → 403; DELETE → hides it for this user
    (user_hidden_templates, migration 173).

Endpoints:
    GET    /templates                → list (newest-updated first, or ?q= ranked)
    POST   /templates                → 201 create
    GET    /templates/{template_id}  → get one
    PATCH  /templates/{template_id}  → update title/content
    DELETE /templates/{template_id}  → 204 soft delete
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.deps import get_current_user, get_redis, get_supabase, validate_uuid
from backend.app.models.requests import (
    CreateTemplateRequest,
    IngestTemplateRequest,
    UpdateTemplateRequest,
)
from backend.app.models.responses import (
    IngestTemplateResponse,
    TemplateListResponse,
    TemplateResponse,
)
from backend.app.services import search_service, templates_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# MAPPER
# ============================================


def _to_response(data: dict) -> TemplateResponse:
    """Translate a user_templates row (or a system_template_row) into the response model."""
    return TemplateResponse(
        template_id=data["template_id"],
        user_id=data.get("user_id"),
        title=data.get("title", ""),
        content_md=data.get("content_md") or "",
        created_by=data.get("created_by", "user"),
        metadata=data.get("metadata") or {},
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        scope=data.get("scope", "user"),
        subtype=data.get("subtype"),
        court=data.get("court"),
        sources=data.get("sources") or [],
    )


def _reject_system_edit(template_id: str) -> None:
    if templates_service.get_system_template_row(template_id) is not None:
        raise LunaHTTPException(
            status_code=403,
            code=ErrorCode.TEMPLATE_READ_ONLY,
            detail=templates_service.SYSTEM_READ_ONLY_AR,
        )


# ============================================
# ROUTES
# ============================================


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    q: Optional[str] = Query(
        None, description="BM25 search over the caller's own templates (>= 3 chars)"
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List the current user's markdown templates.

    ``q`` ranks through the shared ``bm25_search()`` (bm25 plan §5.2). Scoping is
    belt-and-braces: the RPC's ``p_owner`` restricts the index to this user's
    rows, and ``list_templates`` re-scopes by ``user_id`` on the table — one
    search path, two independent owner checks. Templates index title +
    ``content_md`` in full (the caller's own text, nothing gated), so a search
    finds a clause the reader remembers writing, not just the title they gave it.

    ONE endpoint serves both ``/templates`` and ``/templates/mine`` in the
    frontend, so wiring the box in either place lights up both.
    """
    query = search_service.normalize_query(q)

    # قوالب عامة (minus the ones this user hid) follow the user's own.
    system_rows = await run_db(
        templates_service.list_system_templates,
        supabase,
        current_user.auth_id,
        query,
    )

    template_ids: Optional[list[str]] = None
    if query:
        user_id = await run_db(get_user_id, supabase, current_user.auth_id)
        template_ids = await run_db(
            search_service.corpus_search_ids,
            supabase,
            "template",
            query,
            owner_user_id=user_id,
        )
        if not template_ids:
            return TemplateListResponse(templates=[_to_response(r) for r in system_rows])

    rows = await run_db(
        templates_service.list_templates,
        supabase,
        current_user.auth_id,
        template_ids=template_ids,
    )
    return TemplateListResponse(
        templates=[_to_response(r) for r in rows + system_rows]
    )


@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: CreateTemplateRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a new user-authored markdown template."""
    row = await run_db(
        templates_service.create_template,
        supabase,
        current_user.auth_id,
        title=body.title,
        content_md=body.content_md,
    )
    return _to_response(row)


@router.post("/templates/ingest", response_model=IngestTemplateResponse)
async def ingest_template(
    body: IngestTemplateRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    redis: Optional[AsyncRedis] = Depends(get_redis),
):
    """Clean an attached workspace item into a reusable قوالبي template.

    Runs the dedicated ingester pipeline directly (NO router/orchestrator).
    Returns ``{ok: true, template_id, title}`` on success, or
    ``{ok: false, error}`` (Arabic) on failure — failures do NOT raise a 5xx so
    the frontend chip can render the Arabic message in place. ``redis`` backs
    the per-user in-flight ingest concurrency cap.
    """
    result = await templates_service.ingest_template(
        supabase,
        current_user.auth_id,
        item_id=body.item_id,
        redis=redis,
    )
    return IngestTemplateResponse(**result)


@router.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a single template by id (a قالب عام resolves even if hidden — old
    drafts link to it)."""
    validate_uuid(template_id, "معرف القالب")
    system_row = templates_service.get_system_template_row(template_id)
    if system_row is not None:
        return _to_response(system_row)
    row = await run_db(
        templates_service.get_template,
        supabase, current_user.auth_id, template_id,
    )
    return _to_response(row)


@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    body: UpdateTemplateRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update a template's title and/or content (قالب عام → 403, copy it first)."""
    validate_uuid(template_id, "معرف القالب")
    _reject_system_edit(template_id)
    row = await run_db(
        templates_service.update_template,
        supabase,
        current_user.auth_id,
        template_id,
        title=body.title,
        content_md=body.content_md,
    )
    return _to_response(row)


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a قالب خاص, or hide a قالب عام for this user."""
    validate_uuid(template_id, "معرف القالب")
    if templates_service.get_system_template_row(template_id) is not None:
        await run_db(
            templates_service.hide_system_template,
            supabase, current_user.auth_id, template_id,
        )
        return Response(status_code=204)
    await run_db(
        templates_service.delete_template,
        supabase, current_user.auth_id, template_id,
    )
    return Response(status_code=204)
