"""Case-B library carrier route — mounted under ``/api/v1``.

    POST /conversations/{conversation_id}/library-items   — auth, owner.

The pinned contract (``.claude/plans/simple_search_family.md`` §12a C3, which the
frontend codes against byte for byte):

    body  { "page_type": "regulation|article|judgment|blog", "page_id": "<slug>" }
    200   { "item": { "item_id": "...", "title": "...", "kind": "references" } }

``item`` is the full ``WorkspaceItemResponse`` — a superset of the three pinned
fields, and the same shape ``POST /conversations/{id}/blog-items`` returns, so the
frontend's ``WorkspaceItem`` type covers both twins with no branch.
``already_attached`` rides along as the blog twin's dedup flag (the client treats
it as optional).

This lives in its own module rather than in ``blog.py`` (a different public
object) or ``workspace.py`` (generic item CRUD, and the metered source-view path):
the carrier is a library concern with its own coverage rules.

All business logic — validation, ownership, dedup, grounding, titling — is in
``backend.app.services.library_item_service``. The handler stays thin.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.api.workspace import _to_response as _wi_to_response
from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.models.responses import WorkspaceItemResponse
from backend.app.services import library_item_service
from backend.app.services.case_service import get_user_id
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# BODIES (carrier-only, not shared)
#
# Kept local rather than added to ``models/responses.py`` for the same reason
# ``blog.py`` keeps ``ImportBlogRequest`` local: these two schemas have exactly
# one route between them.
# ============================================


class CreateLibraryItemRequest(BaseModel):
    """POST /conversations/{id}/library-items.

    ``page_type`` is validated in the service against
    ``library_item_service.SUPPORTED_PAGE_TYPES`` rather than by a Pydantic
    ``Literal``: an unsupported-but-real library type (``circular`` / ``form`` /
    ``calculator`` / ``topic``) deserves the explicit Arabic 400 «لا يمكن إحضار
    هذا النوع…», not a 422 schema dump the UI cannot render.

    ``page_id`` is the public slug — and for an ``article`` the composite
    ``{reg_slug}/{article_slug}`` shape ``fetch_grounding`` already parses. The
    2,000-char bound is generous headroom over the longest live judgment slug.

    Five shapes reach the service, because ``fetch_grounding`` accepts five:
    a نظام/حكم slug OR its corpus uuid, and for a مادة the composite slug, the
    ``seo_articles`` uuid, or the ``{regulation_id}#{article_no}`` gate key.
    **All five now resolve to a ``simple_search`` identity**, so no shape can
    produce a carry that looks perfect while silently downgrading the turn to a
    Case-A re-search — the failure the eval found on two of them. The one shape
    that stays unbridged is a BARE article slug («المادة-80», which ~1,769
    regulations each have): it is carried and titled, but it names no single
    مادة, so the identity is withheld rather than guessed and the service logs
    the degradation. It is not rejected here because it grounds correctly and a
    working carry must not become a 400.
    """
    page_type: str = Field(..., min_length=1, max_length=40)
    page_id: str = Field(..., min_length=1, max_length=2000)


class LibraryItemResponse(BaseModel):
    """The §12a C3 response envelope.

    The contract pins ``item`` only; ``already_attached`` mirrors the blog twin
    so the client can tell a fresh carry from a dedup hit (it only removes the
    server-side item when it knows the chip created it). The frontend declares it
    OPTIONAL, so it stays additive.
    """
    item: WorkspaceItemResponse
    already_attached: bool = False


# ============================================
# ROUTE
# ============================================


@router.post(
    "/conversations/{conversation_id}/library-items",
    response_model=LibraryItemResponse,
)
async def create_library_item(
    conversation_id: str,
    body: CreateLibraryItemRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Carry a public library page into the conversation as a
    ``kind='references'`` workspace item — «تحدّث مع ريحان عن هذه الصفحة».

    Conversation ownership is verified in the service. Idempotent per
    conversation + page (``already_attached=true`` returns the existing item).
    Unsupported page types and unreachable pages surface as Arabic 400 / 404.

    The returned ``item_id`` goes into the EXISTING ``attachment_ids`` array on
    send — there is no send-payload change, and the item costs zero OCR quota
    (``_estimate_ocr_pages`` skips non-``attachment`` kinds).
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    item, already_attached = await run_db(
        library_item_service.create_library_item,
        supabase,
        user_id=user_id,
        conversation_id=conversation_id,
        page_type=body.page_type,
        page_id=body.page_id,
    )
    return LibraryItemResponse(
        item=_wi_to_response(item),
        already_attached=already_attached,
    )
