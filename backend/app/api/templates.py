"""
Templates API routes — /api/v1/  ("قوالبي" — per-user markdown templates).

DISTINCT from the global ``system_templates`` feature. Every row is scoped to
the authenticated user via the internal ``user_id`` resolved from ``auth_id``.

Endpoints:
    GET    /templates                → list (newest-updated first)
    POST   /templates                → 201 create
    GET    /templates/{template_id}  → get one
    PATCH  /templates/{template_id}  → update title/content
    DELETE /templates/{template_id}  → 204 soft delete
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.models.requests import CreateTemplateRequest, UpdateTemplateRequest
from backend.app.models.responses import TemplateListResponse, TemplateResponse
from backend.app.services import templates_service
from shared.auth.jwt import AuthUser

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# MAPPER
# ============================================


def _to_response(data: dict) -> TemplateResponse:
    """Translate a user_templates row into the response model."""
    return TemplateResponse(
        template_id=data["template_id"],
        user_id=data["user_id"],
        title=data.get("title", ""),
        content_md=data.get("content_md") or "",
        created_by=data.get("created_by", "user"),
        metadata=data.get("metadata") or {},
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


# ============================================
# ROUTES
# ============================================


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List the current user's markdown templates."""
    rows = templates_service.list_templates(supabase, current_user.auth_id)
    return TemplateListResponse(templates=[_to_response(r) for r in rows])


@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    body: CreateTemplateRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a new user-authored markdown template."""
    row = templates_service.create_template(
        supabase,
        current_user.auth_id,
        title=body.title,
        content_md=body.content_md,
    )
    return _to_response(row)


@router.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a single template by id."""
    validate_uuid(template_id, "معرف القالب")
    row = templates_service.get_template(
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
    """Update a template's title and/or content."""
    validate_uuid(template_id, "معرف القالب")
    row = templates_service.update_template(
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
    """Soft-delete a template."""
    validate_uuid(template_id, "معرف القالب")
    templates_service.delete_template(
        supabase, current_user.auth_id, template_id,
    )
    return Response(status_code=204)
