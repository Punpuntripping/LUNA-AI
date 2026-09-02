"""Blog / public share-by-link routes (مدونة) — mounted under ``/api/v1``.

v2 inverted the access model: viewing is open (anon, indexable) and the only
gate left was *curation*. ⚠ **That gate is gone** (``blog_subjects.md`` §8,
2026-09-02): the public wing is a different table now (``public_blogs``,
written by the service key), so ``users.can_access_blog`` guarded a door that
no longer leads anywhere. Publishing here is **owner-scoped and nothing else** —
which is all it ever really was, since moderating someone else's row was
blocked by ownership, never by curation. The COLUMN stays in the DB, dormant
(the ``conversations.case_id`` precedent); no code reads it.

    GET    /public/blog/{token}              — PUBLIC (no auth). The reading
                                               surface for one snapshot.
    GET    /public/blog/{token}/references/{n}/source  — PUBLIC (optional auth).
                                               Metered source reveal, keyed by
                                               token (the legacy share links).
    GET    /public/blogs/{slug}/references/{n}/source  — PUBLIC (optional auth).
                                               The SAME reveal for the versioned
                                               ``public_blogs`` wing, keyed by
                                               slug (plan D17: no token exists).
    GET    /blogs/mine                        — auth. The caller's own blogs
                                               (مدوناتي), both display modes.
    GET    /workspace/{item_id}/share-draft  — auth. Pre-fills the publish
                                               dialog with the default question.
    POST   /workspace/{item_id}/share        — auth. Snapshots an agent_writing
                                               item into a blog_posts row.
    POST   /blogs/{post_id}/publish          — auth, owner. Push a post into
                                               the public gallery.
    DELETE /blogs/{post_id}/publish          — auth, owner. Retract a post from
                                               the public gallery.
    DELETE /blog/posts/{post_id}             — auth, owner-only. Revoke a post.
    POST   /blogs/import                     — auth. Save a pasted share link
                                               into مدوناتي (snapshot copy).
    POST   /conversations/{id}/blog-items    — auth. Copy a blog into a
                                               conversation as an agent_search
                                               item with real references.

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
from typing import Any, Optional

from fastapi import APIRouter, Depends, Path, Query, Response
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import (
    get_current_user,
    get_current_user_optional,
    get_supabase,
    validate_uuid,
)
from backend.app.errors import ErrorCode, LunaHTTPException, library_refusal_response
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.models.responses import (
    BlogItemResponse,
    BlogPostPublicResponse,
    ImportBlogResponse,
    MyBlogItem,
    MyBlogsResponse,
    ShareArtifactResponse,
    ShareDraftResponse,
    SuccessResponse,
)
from backend.app.api.workspace import _to_response as _wi_to_response
from backend.app.services import (
    blog_service,
    library_items_service,
    library_service,
    public_blog_service,
    search_service,
    workspace_service,
)
from backend.app.services.case_service import get_user_id
from backend.app.services.demo_service import is_demo_item
from backend.app.services.reference_resolver import ResolvedRef, resolve_ref
from backend.app.services.references_service import (
    build_reference_source_view,
    fetch_item_references_payload,
)
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


class ImportBlogRequest(BaseModel):
    """POST /blogs/import and POST /conversations/{id}/blog-items.

    ``token`` accepts either a full share URL (…/blog/<token>) or a bare
    32-hex token — the handler extracts tolerantly via ``extract_blog_token``.
    """
    token: str = Field(..., min_length=1, max_length=2000)


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


_SOURCE_CACHE_CONTROL = "private, no-store"


class _UnresolvableRef:
    """Duck-typed stand-in so an unresolvable ref_id reuses the D14 refusal body."""

    may_unlock = False
    charged = False
    reason = "unresolvable"
    cost = 0
    used = 0
    limit = None
    resets_at = None
    stored_count = 0


async def reveal_reference_source(
    references: Any,
    n: int,
    response: Response,
    current_user: Optional[AuthUser],
    supabase: SupabaseClient,
):
    """THE metered source reveal, shared by both public blog wings.

    Two routes address a published article — ``/public/blog/{token}`` (the
    frozen ``blog_posts`` snapshot behind 99 legacy share links) and
    ``/public/blogs/{slug}`` (the versioned ``public_blogs`` wing, which has no
    token: the slug is the whole address, plan D17). **The addressing is the
    only thing that differs.** Everything below — resolution, entitlement,
    metering, the ledger, the refusal body, the no-store header — is one
    implementation on purpose: two copies of an entitlement rule is two copies
    that drift, and the direction they drift in is "free".

    Entitlement is evaluated against the READER, not the author:
      * anonymous → 402 ``reason='anonymous'`` → the panel shows «سجّل مجاناً».
        Deliberately a 402 and not a 401, so the frontend's global
        redirect-to-login never fires on a public page (D14).
      * signed-in → ``resolve_access(..., surface='reference')`` charges once,
        permanently; re-opening is free forever.

    ``references`` is the caller's frozen citation list; each route does its own
    lookup + 404 first, because the two wings 404 in their own words.
    """
    # The frozen snapshot entry stands in for the workspace_item_references row.
    # It carries ref_id + domain, which is all the resolver and the shell
    # builders need (they fall back to parsing ref_id when item_id is absent).
    entry = next(
        (
            r
            for r in (references or [])
            if isinstance(r, dict) and int(r.get("n") or 0) == int(n)
        ),
        None,
    )
    if entry is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المرجع غير موجود",
            headers={"Cache-Control": _SOURCE_CACHE_CONTROL},
        )

    row = {
        "n": int(n),
        "ref_id": entry.get("ref_id") or "",
        "domain": entry.get("domain") or "",
        "item_id": entry.get("item_id"),
    }

    resolved: Optional[ResolvedRef] = await resolve_ref(
        supabase, row["ref_id"], domain=row["domain"], item_id=row["item_id"]
    )
    if resolved is None:
        return library_refusal_response(_UnresolvableRef())

    user_id = (
        await run_db(get_user_id, supabase, current_user.auth_id)
        if current_user
        else None
    )

    if resolved.always_free:
        # Policy-open (a compliance service, or a short تعميم the public page
        # already serves in full). No charge, no ledger row, no balance numbers.
        decision = library_service.AccessDecision(
            may_unlock=True, charged=False, reason="open"
        )
    else:
        decision = await library_service.resolve_access(
            supabase,
            user_id,
            resolved.content_type,
            resolved.content_id,
            surface="reference",
            parent_regulation_id=resolved.parent_regulation_id,
        )

    if not decision.may_unlock:
        return library_refusal_response(decision)

    view = await build_reference_source_view(supabase, row)
    if view is None:
        # Corpus gap, not a refusal — the unlock is permanent, so a retry is free.
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="تعذّر عرض هذا المصدر",
            headers={"Cache-Control": _SOURCE_CACHE_CONTROL},
        )

    if user_id:
        # Shelf the use once (D16.2). No document page is involved here, so this
        # reveal is the only thing that can record it.
        try:
            await library_items_service.record_use(
                supabase, user_id, resolved.content_type, resolved.content_id
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("blog reference record_use failed: %s", e)

    library_url: Optional[str] = None
    try:
        library_url = await library_items_service.public_page_url(
            supabase,
            resolved.content_type,
            resolved.content_id,
            resolved.parent_regulation_id,
        )
    except Exception as e:  # noqa: BLE001
        # Never cost a reader the source they just unlocked over a missing link.
        logger.warning("blog reference library url failed: %s", e)

    response.headers["Cache-Control"] = _SOURCE_CACHE_CONTROL
    return {
        "n": int(n),
        "ref_id": row["ref_id"],
        "domain": row["domain"],
        "source_view": view.model_dump(mode="json"),
        # Same in-app link the chat panel gets — a blog reader who unlocked a
        # source can open it in our library too. ``None`` ⇒ no published page.
        "library_url": library_url,
        "unlocked": {
            "content_type": resolved.content_type,
            "content_id": resolved.content_id,
            "title": resolved.title or getattr(view, "title", "") or "",
            "article_no": resolved.article_no,
            "charged": bool(decision.charged),
            "cost": int(decision.cost or 0),
            "reason": decision.reason,
        },
        "balance": None if decision.reason == "open" else {
            "used": int(decision.used or 0),
            "limit": decision.limit,
            "resets_at": (
                decision.resets_at.isoformat()
                if hasattr(decision.resets_at, "isoformat")
                else decision.resets_at
            ),
        },
    }


@router.get("/public/blog/{token}/references/{n}/source")
async def get_blog_reference_source(
    token: str,
    response: Response,
    n: int = Path(..., ge=1, description="The reference's 1-based [n] citation number."),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
    _rl=Depends(library_rate_limit),
):
    """Reveal ONE cited source on a PUBLIC blog post — metered, same as in-app.

    «عرض المصدر» and the ``[n]`` preview behave exactly as they always did: the
    click opens the source. The only change is that opening it now COSTS an
    unlock, which is why this endpoint exists — the frozen snapshot no longer
    carries bodies, so there is finally a server call for the meter to sit on.

    Why this is not the workspace endpoint: a blog reader is not the author, so
    ``get_workspace_item``'s ownership check would 404 them out of their own
    reading. The post's token IS the capability here — it is unguessable and
    already grants the page — so the reference is addressed by
    ``(token, n)`` against the frozen ``references_json``.

    Entitlement is evaluated against the READER, not the author:
      * anonymous → 402 ``reason='anonymous'`` → the panel shows «سجّل مجاناً».
        Deliberately a 402 and not a 401, so the frontend's global
        redirect-to-login never fires on a public page (D14).
      * signed-in → ``resolve_access(..., surface='reference')`` charges once,
        permanently; re-opening is free forever.

    So a published post can be read by anyone, and its SOURCES cost the reader
    what they would have cost in chat — closing the bypass where one publish
    would otherwise mint an unmetered public mirror of every source it cites.

    The token addresses the post and nothing more; the entitlement rules live in
    ``reveal_reference_source``, shared with the ``public_blogs`` wing below.
    """
    post = await run_db(blog_service.get_public_post, supabase, token)
    if post is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المنشور غير موجود",
            headers={"Cache-Control": _SOURCE_CACHE_CONTROL},
        )
    return await reveal_reference_source(
        post.get("references") or [], n, response, current_user, supabase
    )


@router.get("/public/blogs/{slug}/references/{n}/source")
async def get_public_blog_reference_source(
    slug: str,
    response: Response,
    n: int = Path(..., ge=1, description="The reference's 1-based [n] citation number."),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
    _rl=Depends(library_rate_limit),
):
    """The same metered reveal, for the versioned wing — keyed by SLUG.

    ``public_blogs`` has no token (plan D17): a public blog is *open*, so the
    unguessable string that made a ``blog_posts`` link a capability has nothing
    left to do and the slug is the whole address. Nothing about the meter moves
    with it — the token was only ever the addressing, never the entitlement, and
    ``reveal_reference_source`` is literally the same code the token route runs.

    Reads the CURRENT version's frozen ``references_json``. Retracted blogs are
    included, matching ``GET /public/blogs/{slug}``: retraction delists an
    article, it does not take its page — or its sources — away from someone
    holding the link.

    404 «المدونة غير موجودة» when the slug resolves to nothing, in the wing's
    own words rather than the share-link wing's «المنشور غير موجود».
    """
    references = await run_db(
        public_blog_service.get_references_by_slug, supabase, slug
    )
    if references is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المدونة غير موجودة",
            headers={"Cache-Control": _SOURCE_CACHE_CONTROL},
        )
    return await reveal_reference_source(
        references, n, response, current_user, supabase
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

    # The demo allowance in ``get_workspace_item`` is READ-only, and this is the
    # one write it did not refuse for free: the demo WI is ``kind='agent_search'``,
    # which ``assert_publishable`` accepts, so without this guard any account
    # could mint a blog post out of the tour's fixture. Owner behaviour is
    # unchanged — only non-owners are turned away.
    if is_demo_item(item_id) and item.get("user_id") != user_id:
        raise LunaHTTPException(
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            detail="لا يمكن نشر عنصر من المحادثة التجريبية",
        )

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

    # Resolve the cited references the synthesis grounded against and snapshot
    # them onto the post.
    #
    # PHASE C (access-tiers §6.2): this snapshot deliberately carries NO
    # ``source_view`` — ``fetch_item_references`` defaults to the metered shape.
    # ``blog_posts.references_json`` is served by the ANONYMOUS
    # ``GET /public/blog/{token}`` and ``/public/blogs``, so keeping the bodies
    # would mint a permanent, unmetered, anon-readable mirror of full case /
    # chunk / circular text — one publish per source and the whole ledger is
    # moot. What remains is the citation mesh (title, snippet, links,
    # cross_refs), which §1.3 puts in the never-gated class, plus the official
    # source URL. A reader who wants the full text signs in and reveals it
    # through ``GET /public/blog/{token}/references/{n}/source``, which meters it
    # exactly like the in-app panel does.
    #
    # ``fetch_item_references_payload`` (not ``fetch_item_references``) so each
    # entry carries ``has_source``: that flag is what tells the blog panel to
    # render «عرض المصدر» at all, and it must be in the FROZEN snapshot — a
    # reader has no workspace item to probe.
    references_json = await fetch_item_references_payload(
        supabase, item_id, used_only=True
    )

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
# مدوناتي — auth, owner-scoped (my own blogs)
# ============================================


@router.get(
    "/blogs/mine",
    response_model=MyBlogsResponse,
)
async def list_my_blogs(
    q: Optional[str] = Query(
        None, description="BM25 search over the caller's own posts (>= 3 chars)"
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The caller's own blogs (مدوناتي) — owner-scoped, both display_modes.

    ⚠ ``can_publish_public`` was dropped from the response (plan §8) along with
    the curation gate it mirrored. The frontend drops the publish toggle it fed;
    nothing here reads ``users.can_access_blog`` any more.

    ``q`` ranks through the shared ``bm25_search()`` (bm25 plan §5.2), scoped to
    ``owner_user_id`` INSIDE the RPC — ``blog_posts`` is an owner-only corpus in
    the index, and the RPC's ``p_owner`` branch matches that owner's rows and no
    others, so another user's post cannot appear here even if it outranks
    everything. The id filter below is the second scope, not the first.
    ``content_md`` is indexed in full for blogs (they are the caller's own text,
    with no gate to respect), so a search reaches the body, not just the title.
    Ordered by relevance when searching, newest-first otherwise.
    """
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)

    query = search_service.normalize_query(q)
    post_ids: Optional[list[str]] = None
    if query:
        post_ids = await run_db(
            search_service.corpus_search_ids,
            supabase,
            "blog",
            query,
            owner_user_id=user_id,
        )
        if not post_ids:
            return MyBlogsResponse(posts=[])

    rows = await run_db(
        blog_service.list_my_blogs, supabase, user_id, post_ids=post_ids
    )
    return MyBlogsResponse(posts=[MyBlogItem(**r) for r in rows])


# ============================================
# IMPORT — shared blog → مدوناتي / → conversation note
# (.claude/plans/blog_import.md)
# ============================================


@router.post(
    "/blogs/import",
    response_model=ImportBlogResponse,
)
async def import_blog(
    body: ImportBlogRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Save the published post behind a pasted share URL/token into the
    caller's مدوناتي as a snapshot copy (own DB-minted token, ``is_public``
    false, ``source_post_id`` = root original for dedup).

    Access rule = viewing rule: any valid token of a published post imports.
    Idempotent: an existing live post for the same root (authored or imported)
    is returned with ``already_saved=true`` instead of a duplicate.
    """
    token = blog_service.extract_blog_token(body.token)
    if token is None:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="رابط المدونة غير صالح",
        )
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    row, already_saved = await run_db(
        blog_service.import_post_for_user,
        supabase, user_id=user_id, token=token,
    )
    return ImportBlogResponse(
        post=MyBlogItem(**blog_service.to_my_blog_item(row)),
        already_saved=already_saved,
    )


@router.post(
    "/conversations/{conversation_id}/blog-items",
    response_model=BlogItemResponse,
)
async def create_blog_item(
    conversation_id: str,
    body: ImportBlogRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Copy the published post behind a token into the conversation as a
    ``kind=agent_search`` workspace item — تحليل قانوني with a working
    المراجع panel («اتحدث مع المدونة» / composer paste-chip).

    Conversation ownership is verified in the service. Idempotent per
    conversation+root post (``already_attached=true`` returns the existing
    item). The 15-item cap surfaces as an Arabic 400.
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    token = blog_service.extract_blog_token(body.token)
    if token is None:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="رابط المدونة غير صالح",
        )
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    item, already_attached = await run_db(
        blog_service.create_blog_item,
        supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        token=token,
    )
    return BlogItemResponse(
        item=_wi_to_response(item),
        already_attached=already_attached,
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

    Owner-scoped and nothing more: a post that isn't the caller's surfaces as a
    404, the same envelope a missing post gets.

    ⚠ **The ``users.can_access_blog`` curation gate was REMOVED here** (plan §8).
    It guarded a door that no longer leads anywhere: the public wing moved to
    ``public_blogs``, which this route cannot write, and the ``blog_posts``
    gallery it does write is no longer read by anything public. It also never
    granted the power it looked like it granted — moderating someone else's row
    is blocked by OWNERSHIP, not by curation, so a curator hitting another
    user's post always got a 404 and no flag could change that. Exactly one
    account ever held the flag and published exactly zero posts with it.
    Moderation of the public wing lives at
    ``POST /internal/public-blogs/{root_id}/retract``, behind the service key.
    """
    validate_uuid(post_id, "معرف المنشور")
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
