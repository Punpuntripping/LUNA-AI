"""Blog / public share-by-link routes (مدونة) — mounted under ``/api/v1``.

Four endpoints back the "publish an artifact to a public, unlisted, read-only
page" feature:

    GET    /public/blog/{token}              — PUBLIC (no auth). The reading
                                               surface for the snapshot.
    GET    /workspace/{item_id}/share-draft  — auth. Pre-fills the publish
                                               dialog with the default question.
    POST   /workspace/{item_id}/share        — auth. Snapshots an agent_writing
                                               item into a blog_posts row.
    DELETE /blog/posts/{post_id}             — auth, owner-only. Revoke a post.

Snapshot model: at publish time we freeze ``content_md`` + the fully-resolved
``Reference[]`` into the post row, so the public page never touches live
workspace data and survives later edits/deletes of the source artifact.

The public GET intentionally has NO ``Depends(get_current_user)`` — auth is
per-endpoint in this codebase (no global auth middleware), so omitting the dep
is what makes the endpoint anonymous-accessible. The rate-limit middleware
still applies (IP-keyed for anon callers; it does not reject for a missing
token).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.responses import (
    BlogPostPublicResponse,
    ShareArtifactResponse,
    ShareDraftResponse,
    SuccessResponse,
)
from backend.app.services import blog_service, workspace_service
from backend.app.services.case_service import get_user_id
from backend.app.services.references_service import fetch_item_references
from shared.auth.jwt import AuthUser
from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# REQUEST BODIES (blog-only, not shared)
# ============================================


class ShareArtifactRequest(BaseModel):
    """POST /workspace/{item_id}/share"""
    question_text: str = Field(..., min_length=1, max_length=5000)


# ============================================
# PUBLIC READ — no auth dependency
# ============================================


@router.get(
    "/public/blog/{token}",
    response_model=BlogPostPublicResponse,
)
async def get_public_blog_post(
    token: str,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Public, anonymous read of a published snapshot by its unguessable token.

    Returns the question + the frozen answer + the snapshotted references. 404
    (Arabic) when the token doesn't resolve to a published, non-deleted post.
    Best-effort increments the post's view counter.
    """
    post = await run_db(blog_service.get_public_post, supabase, token)
    if post is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المنشور غير موجود",
        )
    return BlogPostPublicResponse(
        question_text=post["question_text"],
        title=post.get("title"),
        content_md=post["content_md"],
        references=post.get("references") or [],
        subtype=post.get("subtype"),
        created_at=post["created_at"],
    )


# ============================================
# SHARE-DRAFT — auth
# ============================================


@router.get(
    "/workspace/{item_id}/share-draft",
    response_model=ShareDraftResponse,
)
async def get_share_draft(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Return the default question to pre-fill the publish dialog.

    ``get_workspace_item`` enforces ownership (cross-user item_id -> 404)
    before any derivation runs.
    """
    validate_uuid(item_id, "معرف العنصر")
    item = await run_db(
        workspace_service.get_workspace_item,
        supabase, current_user.auth_id, item_id,
    )
    default_question = await run_db(
        blog_service.derive_default_question, supabase, item
    )
    return ShareDraftResponse(default_question=default_question)


# ============================================
# SHARE — auth (create snapshot)
# ============================================


@router.post(
    "/workspace/{item_id}/share",
    response_model=ShareArtifactResponse,
    status_code=201,
)
async def share_artifact(
    item_id: str,
    body: ShareArtifactRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Publish an ``agent_writing`` artifact to a public, unguessable URL.

    Steps: ownership/404 via ``get_workspace_item`` → assert
    ``kind == 'agent_writing'`` (else 400) → resolve internal user_id →
    snapshot content_md/subtype/title → resolve cited references
    (``used_only=True``) → insert the blog_posts row (DB mints the token) →
    return ``{token, public_url}``.
    """
    validate_uuid(item_id, "معرف العنصر")

    item = await run_db(
        workspace_service.get_workspace_item,
        supabase, current_user.auth_id, item_id,
    )
    # 400 (Arabic) if this isn't a written artifact.
    blog_service.assert_publishable(item)

    user_id = await run_db(get_user_id, supabase, current_user.auth_id)

    # Snapshot fields from the artifact.
    metadata = item.get("metadata") or {}
    subtype = metadata.get("subtype")
    title = item.get("title")
    content_md = item.get("content_md") or ""

    # Resolve the cited references the synthesis grounded against. Snapshot the
    # full Reference payload (incl. source_view) so the public page renders the
    # same fluid citations as the in-app artifact view.
    references = await fetch_item_references(supabase, item_id, used_only=True)
    references_json = [r.model_dump(mode="json") for r in references]

    token = await run_db(
        blog_service.insert_post,
        supabase,
        owner_user_id=user_id,
        source_item_id=item_id,
        subtype=subtype,
        question_text=body.question_text,
        title=title,
        content_md=content_md,
        references_json=references_json,
    )

    settings = get_settings()
    public_url = f"{settings.PUBLIC_WEB_URL}/blog/{token}"
    return ShareArtifactResponse(token=token, public_url=public_url)


# ============================================
# REVOKE — auth, owner-only
# ============================================


@router.delete(
    "/blog/posts/{post_id}",
    response_model=SuccessResponse,
)
async def delete_blog_post(
    post_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Owner-scoped soft-revoke of a published post (kill switch for a leaked
    link). 404 if the post isn't the caller's (or doesn't exist / is already
    revoked)."""
    validate_uuid(post_id, "معرف المنشور")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    await run_db(
        blog_service.unpublish_post,
        supabase, user_id, post_id,
    )
    return SuccessResponse(success=True)
