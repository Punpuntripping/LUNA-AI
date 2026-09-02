"""Public blog wing (مدونة ريحان) — the ANONYMOUS read surface, under ``/api/v1``.

Plan: ``.claude/plans/blog_subjects.md`` §3 (routing) + §5 (publishing).
Tables: migrations 153 (``public_blogs``, versioned) + 154 (``blog_subjects``).

    GET /public/blogs                    — the gallery feed (newest first)
    GET /public/blogs/subjects           — the browse vocabulary + counts
    GET /public/blogs/subjects/{slug}    — the blogs carrying one subject
    GET /public/blogs/{slug}             — one blog, the CURRENT version

⚠ ONE MORE ROUTE OF THIS WING LIVES NEXT DOOR, deliberately:

    GET /public/blogs/{slug}/references/{n}/source   → ``api/blog.py``

The metered «عرض المصدر» reveal is declared beside its token-keyed twin so both
wings run ONE ``reveal_reference_source`` body — the token was only ever the
addressing, never the entitlement (plan D17), and two copies of an entitlement
rule is two copies that drift. Slug resolution for it goes through
``public_blog_service.get_references_by_slug``, which shares this module's
by-slug predicate but does NOT bump ``view_count``: a citation click is not a
page view.

None of these declare ``Depends(get_current_user)``, and that omission IS what
makes them anonymous: auth is per-endpoint in this codebase (there is no global
auth middleware), exactly as with ``/public/blog/{token}`` next door. The
rate-limit middleware still applies, IP-keyed for anon callers.

ROUTE ORDER MATTERS
-------------------
``/public/blogs/subjects`` is declared BEFORE ``/public/blogs/{slug}``. FastAPI
matches in declaration order, so the literal segment has to win or the subject
index would be read as a blog slug. This mirrors the frontend dispatcher, where
``app/blog/subjects/`` is a static segment that always beats ``[slug]``.

This wing owns ``GET /public/blogs``: ``api/blog.py``'s ``blog_posts`` gallery
of the same path was deleted, not shadowed. Shadowing would have left the
OpenAPI document — which is keyed by PATH, not by registration order —
describing a handler that could never run.

⚠ THE GALLERY AND THE ARTICLE DO NOT SHARE A VISIBILITY RULE. The feeds list
only ``is_public`` blogs; the by-slug read serves retracted ones too, because
retraction delists without deleting and the direct link must keep working (plan
§5). ``is_public`` rides along in the response so the frontend can set
``robots: noindex`` — a live 200 never deindexes anything by itself (§7).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from supabase import Client as SupabaseClient

from backend.app.deps import get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.models.responses import (
    PublicBlogCard,
    PublicBlogDetailResponse,
    PublicBlogListResponse,
    PublicBlogSubject,
    PublicBlogSubjectFeedResponse,
    PublicBlogSubjectsResponse,
)
from backend.app.services import public_blog_service
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/public/blogs", response_model=PublicBlogListResponse)
async def list_public_blog_gallery(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The anonymous gallery feed: current, public, published, not deleted.

    Newest first. Each card carries the blog's ``type`` (a badge, never a URL —
    plan D3) and its active subject chips, which are the internal-linking spine
    of the wing: how a reader who landed from Google discovers the subject, and
    how link equity reaches the subject pages.
    """
    rows = await run_db(
        public_blog_service.list_gallery, supabase, limit=limit, offset=offset
    )
    return PublicBlogListResponse(blogs=[PublicBlogCard(**r) for r in rows])


@router.get("/public/blogs/subjects", response_model=PublicBlogSubjectsResponse)
async def list_public_blog_subjects(
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The browse vocabulary — ACTIVE subjects with their public-blog counts.

    Returns the whole vocabulary with honest counts; the ``>=1`` filter that
    keeps empty subjects out of the hub grid and the sitemap (plan §7, D13) is
    applied by the caller, so a curator can still see a freshly seeded subject
    sitting at zero.
    """
    rows = await run_db(public_blog_service.list_subjects, supabase)
    return PublicBlogSubjectsResponse(
        subjects=[PublicBlogSubject(**r) for r in rows]
    )


@router.get(
    "/public/blogs/subjects/{slug}",
    response_model=PublicBlogSubjectFeedResponse,
)
async def list_blogs_by_subject(
    slug: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Every public blog carrying one subject, newest first.

    An unknown OR inactive subject is a 404 (Arabic) — indistinguishable on
    purpose: retiring a subject is ``is_active=false``, never a delete (the join
    FK is RESTRICT), and it must take the page down the same way a typo does.
    """
    subject = await run_db(public_blog_service.get_subject_by_slug, supabase, slug)
    if subject is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="الموضوع غير موجود",
        )

    total, rows = await run_db(
        public_blog_service.list_blogs_for_subject,
        supabase,
        subject["subject_id"],
        limit=limit,
        offset=offset,
    )
    return PublicBlogSubjectFeedResponse(
        subject=PublicBlogSubject(
            slug=subject["slug"],
            label_ar=subject["label_ar"],
            description_ar=subject.get("description_ar"),
            sort_rank=int(subject.get("sort_rank") or 0),
            # The FULL qualifying count, not this page's length — the header
            # must not shrink when the reader pages forward.
            blog_count=total,
        ),
        blogs=[PublicBlogCard(**r) for r in rows],
    )


@router.get("/public/blogs/{slug}", response_model=PublicBlogDetailResponse)
async def get_public_blog(
    slug: str,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """One blog by its Arabic slug — the CURRENT version.

    404 (Arabic) when nothing resolves, i.e. no current, published, non-deleted
    row holds that slug. A RETRACTED blog (``is_public=false``) DOES resolve:
    retraction delists it from the gallery and the sitemap, and the returned
    ``is_public=false`` is what makes the page ``noindex`` (plan §5/§7).

    ⚠ Arabic slugs arrive percent-encoded from Next's dynamic segment; Starlette
    decodes the path param once before it gets here, so ``slug`` is already the
    literal Arabic string. Do not decode again.
    """
    blog = await run_db(public_blog_service.get_by_slug, supabase, slug)
    if blog is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المدونة غير موجودة",
        )
    return PublicBlogDetailResponse(**blog)
