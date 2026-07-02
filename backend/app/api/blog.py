"""Blog / public share-by-link routes (مدونة) — mounted under ``/api/v1``.

v2 inverts the access model: viewing is open (anon, indexable); the only gate
is *curation* (``users.can_access_blog``) — who may push a post into the public
gallery (``is_public=true``).

    GET    /public/blog/{token}              — PUBLIC (no auth). The reading
                                               surface for one snapshot.
    GET    /public/blogs                     — PUBLIC (no auth). The public
                                               gallery list (is_public posts).
    GET    /blogs/mine                        — auth. The caller's own blogs
                                               (مدوناتي), both display modes.
    GET    /workspace/{item_id}/share-draft  — auth. Pre-fills the publish
                                               dialog with the default question.
    POST   /workspace/{item_id}/share        — auth. Snapshots an agent_writing
                                               item into a blog_posts row.
    POST   /blogs/{post_id}/publish          — auth + curation gate. Push a post
                                               into the public gallery.
    DELETE /blogs/{post_id}/publish          — auth, owner. Retract a post from
                                               the public gallery.
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
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.responses import (
    BlogCardPublic,
    BlogPostPublicResponse,
    MyBlogItem,
    MyBlogsResponse,
    PublicBlogsResponse,
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
    """POST /workspace/{item_id}/share.

    Two templates:
      - ``display_mode='question'`` (default) needs a non-empty ``question_text``.
      - ``display_mode='title'`` (مدونة) needs a non-empty ``title``; the
        question block is omitted, so ``question_text`` may be empty.
    Per-mode requirements are enforced in the handler (a single schema can't
    express "exactly one of these is required").
    """
    question_text: str = Field("", max_length=5000)
    display_mode: str = Field("question")
    title: Optional[str] = Field(None, max_length=300)


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
        display_mode=post.get("display_mode") or "question",
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
    return ShareDraftResponse(
        default_question=default_question,
        default_title=item.get("title"),
    )


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
    content_md = item.get("content_md") or ""

    # Per-template validation. A single schema can't express "exactly one of
    # title/question_text is required", so the two modes are validated here.
    display_mode = body.display_mode if body.display_mode in ("question", "title") else "question"
    if display_mode == "title":
        # مدونة: needs a non-empty title; the question block is omitted.
        title = (body.title or "").strip()
        if not title:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.VALIDATION_ERROR,
                detail="لا يمكن نشر مدونة بدون عنوان",
            )
        question_text = ""
    else:
        # سؤال (default): needs a non-empty question; keep the artifact's title.
        question_text = (body.question_text or "").strip()
        if not question_text:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.VALIDATION_ERROR,
                detail="لا يمكن نشر سؤال فارغ",
            )
        title = item.get("title")

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
        question_text=question_text,
        title=title,
        content_md=content_md,
        references_json=references_json,
        display_mode=display_mode,
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


# ============================================
# PUBLIC GALLERY — no auth (anon, SEO)
# ============================================


@router.get(
    "/public/blogs",
    response_model=PublicBlogsResponse,
)
async def list_public_blogs(
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous public gallery of published, public blogs (/blog).

    v2 inverts the v1 gate: viewing is open to everyone (no login, indexable).
    Keys on ``is_public`` — any user's published, public share appears, newest
    first. The curation gate (``can_access_blog``) now lives only on the
    publish-to-public action, not on this read.
    """
    rows = await run_db(blog_service.list_public_blogs, supabase)
    return PublicBlogsResponse(posts=[BlogCardPublic(**r) for r in rows])


# ============================================
# مدوناتي — auth, owner-scoped (my own blogs)
# ============================================


@router.get(
    "/blogs/mine",
    response_model=MyBlogsResponse,
)
async def list_my_blogs(
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The caller's own blogs (مدوناتي) — owner-scoped, both display_modes.

    ``can_publish_public`` mirrors ``users.can_access_blog`` so the UI can show
    the «نشر في المدونة العامة» toggle only to curators.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    can_pub = await run_db(
        blog_service.user_can_access_blog, supabase, current_user.auth_id
    )
    rows = await run_db(blog_service.list_my_blogs, supabase, user_id)
    return MyBlogsResponse(
        can_publish_public=can_pub,
        posts=[MyBlogItem(**r) for r in rows],
    )


# ============================================
# CURATION — publish / retract a post to the public gallery
# ============================================


@router.post(
    "/blogs/{post_id}/publish",
    response_model=SuccessResponse,
)
async def publish_blog_public(
    post_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Push the caller's own post into the public gallery (``is_public=true``).

    Gated by ``users.can_access_blog`` (the v2 curation gate) — 403 (Arabic)
    when the caller is not a curator. Then owner-scoped: a post that isn't the
    caller's surfaces as 404.
    """
    validate_uuid(post_id, "معرف المنشور")
    if not await run_db(
        blog_service.user_can_access_blog, supabase, current_user.auth_id
    ):
        raise LunaHTTPException(
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            detail="غير مصرح لك بالنشر في المدونة العامة",
        )
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    await run_db(
        blog_service.set_post_public, supabase, user_id, post_id, True
    )
    return SuccessResponse(success=True)


@router.delete(
    "/blogs/{post_id}/publish",
    response_model=SuccessResponse,
)
async def unpublish_blog_public(
    post_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Retract the caller's own post from the public gallery (``is_public=false``).

    Owner-scoped only — no curation gate: you may always pull your own post
    back out of the public gallery. 404 if the post isn't the caller's.
    """
    validate_uuid(post_id, "معرف المنشور")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    await run_db(
        blog_service.set_post_public, supabase, user_id, post_id, False
    )
    return SuccessResponse(success=True)
