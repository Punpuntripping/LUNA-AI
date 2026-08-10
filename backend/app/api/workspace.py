"""
Workspace API routes -- /api/v1/

Replaces the old artifacts router. Targets the post-026 schema:
``workspace_items`` table, ``item_id`` PK, ``kind``-driven permissions.

Endpoints (existing, renamed paths):
    GET    /conversations/{conversation_id}/workspace
    GET    /cases/{case_id}/workspace
    GET    /workspace/{item_id}
    PATCH  /workspace/{item_id}
    DELETE /workspace/{item_id}

Endpoints (new):
    POST   /conversations/{conversation_id}/workspace/notes
    POST   /conversations/{conversation_id}/workspace/attachments/upload
    POST   /conversations/{conversation_id}/workspace/attachments/from-document
    POST   /conversations/{conversation_id}/workspace/references
    PATCH  /workspace/{item_id}/visibility
    GET    /workspace/{item_id}/file
    GET    /workspace/{item_id}/references/{n}/source   (metered reveal, Phase C)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase, validate_uuid
from backend.app.errors import ErrorCode, LunaHTTPException, library_refusal_response
from shared.observability import get_logfire
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.models.responses import (
    DownloadResponse,
    SuccessResponse,
    UploadInitResponse,
    WorkspaceItemListResponse,
    WorkspaceItemResponse,
)
from backend.app.models.requests import UpdateWorkspaceItemRequest, UploadInitRequest
from backend.app.services import library_service, message_service, workspace_service
from backend.app.services.case_service import get_user_id
from backend.app.services.reference_resolver import ResolvedRef, resolve_ref
from backend.app.services.references_service import (
    build_reference_source_view,
    fetch_item_references_payload,
    fetch_reference_row,
)
from shared.auth.jwt import AuthUser
from shared.config import get_settings
from shared.db.run import run_db
from shared.storage.client import get_signed_url

logger = logging.getLogger(__name__)
_logfire = get_logfire()

router = APIRouter()


# ============================================
# REQUEST BODIES (workspace-only, not shared)
# ============================================


class CreateNoteRequest(BaseModel):
    """POST /conversations/{conversation_id}/workspace/notes"""
    title: str = Field(..., min_length=1, max_length=500)
    content_md: str = Field(default="", max_length=200_000)


class CreateReferenceRequest(BaseModel):
    """POST /conversations/{conversation_id}/workspace/references"""
    title: str = Field(..., min_length=1, max_length=500)
    content_md: Optional[str] = Field(default=None, max_length=200_000)


class FromDocumentRequest(BaseModel):
    """POST /conversations/{conversation_id}/workspace/attachments/from-document"""
    document_id: str = Field(..., min_length=1)


class UpdateVisibilityRequest(BaseModel):
    """PATCH /workspace/{item_id}/visibility"""
    is_visible: bool


class UpdateFeedbackRequest(BaseModel):
    """PATCH /workspace/{item_id}/feedback

    ``feedback`` is the user's 👍/👎 rating: ``'up'`` / ``'down'`` / ``None``
    (None clears it). Validation of the literal values happens in the service.
    """
    feedback: Optional[str] = None


# ============================================
# MAPPERS
# ============================================


def _to_response(data: dict) -> WorkspaceItemResponse:
    """Translate a workspace_items row into the response model."""
    item_id = data.get("item_id") or data.get("artifact_id") or ""
    # Migration 052 alias. Coerced defensively: pre-052 rows and case-only items
    # carry NULL, and a bad value must not 500 the whole list — the badge simply
    # doesn't render.
    raw_wi_seq = data.get("wi_seq")
    try:
        wi_seq = int(raw_wi_seq) if raw_wi_seq is not None else None
    except (TypeError, ValueError):
        wi_seq = None
    return WorkspaceItemResponse(
        item_id=item_id,
        user_id=data["user_id"],
        conversation_id=data.get("conversation_id"),
        case_id=data.get("case_id"),
        message_id=data.get("message_id"),
        wi_seq=wi_seq,
        agent_family=data.get("agent_family"),
        kind=data.get("kind", "agent_search"),
        created_by=data.get("created_by", "agent"),
        title=data.get("title", ""),
        content_md=data.get("content_md"),
        storage_path=data.get("storage_path"),
        document_id=data.get("document_id"),
        is_visible=bool(data.get("is_visible", True)),
        feedback=data.get("feedback"),
        metadata=data.get("metadata") or {},
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


# ============================================
# LIST endpoints (renamed paths)
# ============================================


@router.get(
    "/conversations/{conversation_id}/workspace",
    response_model=WorkspaceItemListResponse,
)
async def list_conversation_workspace(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List workspace items for a conversation."""
    validate_uuid(conversation_id, "معرف المحادثة")
    items, total = await run_db(
        workspace_service.list_workspace_items_by_conversation,
        supabase, current_user.auth_id, conversation_id,
        limit=limit, offset=offset,
    )
    return WorkspaceItemListResponse(
        items=[_to_response(i) for i in items],
        total=total,
    )


@router.get(
    "/cases/{case_id}/workspace",
    response_model=WorkspaceItemListResponse,
)
async def list_case_workspace(
    case_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List workspace items for a case."""
    validate_uuid(case_id, "معرف القضية")
    items, total = await run_db(
        workspace_service.list_workspace_items_by_case,
        supabase, current_user.auth_id, case_id,
        limit=limit, offset=offset,
    )
    return WorkspaceItemListResponse(
        items=[_to_response(i) for i in items],
        total=total,
    )


# ============================================
# SINGLE-ITEM endpoints
# ============================================


@router.get(
    "/workspace/{item_id}",
    response_model=WorkspaceItemResponse,
)
async def get_workspace_item(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Get a single workspace item."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.get_workspace_item,
        supabase, current_user.auth_id, item_id,
    )
    return _to_response(data)


@router.get(
    "/workspace/{item_id}/references",
)
async def list_workspace_item_references(
    item_id: str,
    used: Optional[bool] = Query(
        default=None,
        description="When true, only return references the synthesis cited inline.",
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """List the references attached to an ``agent_search`` workspace item.

    Replaces the pre-migration-049 ``metadata.references`` JSONB read path.
    ``get_workspace_item`` is called first to enforce ownership; a cross-user
    item_id surfaces as 404 before any references are exposed.

    PHASE C (§6.2 step 1): this is the **citation mesh only** — ``n``, ``title``,
    ``snippet``, ``ref_id``, ``domain``, links, ``cross_refs``. It carries NO
    source bodies: ``source_view`` is always ``null`` here, and the full text is
    fetched one item at a time from
    ``GET /workspace/{item_id}/references/{n}/source`` (metered). Citation lists
    are in the never-gated class (§1.3), so this endpoint stays free — only the
    body moved. Each entry gains ``has_source`` so the panel knows whether a
    «عرض المصدر» affordance exists without probing for it.
    """
    validate_uuid(item_id, "معرف العنصر")
    # Ownership check via the existing service. Raises 404 if the item is
    # not visible to this user — same envelope as get_workspace_item.
    await run_db(workspace_service.get_workspace_item, supabase, current_user.auth_id, item_id)
    references = await fetch_item_references_payload(
        supabase, item_id, used_only=bool(used) if used is not None else False,
    )
    return {"references": references}


# --- the metered reveal (§6.2 step 2) --------------------------------------

# Every response on the reveal path is a per-USER answer. One of these landing in
# a shared cache would hand somebody else's unlocked source to the next visitor.
_SOURCE_CACHE_CONTROL = "private, no-store"


async def _record_library_use(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Shelf the revealed source in «مكتبتي» and bump ``use_count`` — D16.2.

    Called EXACTLY ONCE per reveal, inside this handler, for a charged reveal and
    for a free-because-already-unlocked one alike: the shelf counts USES, not
    purchases. The frontend must NOT also fire ``POST /library/mine/use`` for a
    gated item, or one user action would count twice (plan §5B).

    ``library_items_service`` is created by the Phase B2 agent in this same wave,
    so the import is deferred and a missing module is survivable — a shelf write
    must never break a content read (D16.2), and neither must its absence.
    """
    if not user_id or not content_type or not content_id:
        return
    try:
        from backend.app.services import library_items_service
    except ImportError:  # pragma: no cover - only until B2 lands this wave
        logger.debug("library_items_service not present yet — skipping shelf write")
        return
    try:
        await library_items_service.record_use(
            supabase, user_id, content_type, content_id
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "record_use failed (%s/%s): %s — source read is unaffected",
            content_type, content_id, e,
        )


async def _library_page_url(supabase: SupabaseClient, resolved: ResolvedRef) -> Optional[str]:
    """The cited item's page in OUR library, for «فتح ... في ريحان». Fail-soft.

    ``None`` whenever the item has no published page — the panel then renders the
    external link alone rather than a fallback that goes nowhere. A failure here
    must never cost the reader the source they just unlocked, so every error
    degrades to "no in-app link".
    """
    try:
        from backend.app.services import library_items_service

        return await library_items_service.public_page_url(
            supabase,
            resolved.content_type,
            resolved.content_id,
            resolved.parent_regulation_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "library page url failed (%s/%s): %s",
            resolved.content_type, resolved.content_id, e,
        )
        return None


class _UnresolvableRef:
    """Duck-typed stand-in for an ``AccessDecision`` that never happened.

    ``library_refusal_payload`` reads ``.reason``/``.used``/``.limit``/
    ``.resets_at``/``.stored_count`` off whatever it is handed (D16.1), so a ref
    we could not resolve refuses through the SAME D14 402 body as a quota
    refusal. The frontend keeps one branch, and there is no code path where an
    unresolvable id degrades into content.
    """

    may_unlock = False
    charged = False
    reason = "unresolvable"
    cost = 0
    used = 0
    limit = None
    resets_at = None
    stored_count = 0


@router.get(
    "/workspace/{item_id}/references/{n}/source",
)
async def get_reference_source(
    item_id: str,
    response: Response,
    n: int = Path(..., ge=1, description="The reference's 1-based [n] citation number."),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    _rl=Depends(library_rate_limit),
):
    """Reveal ONE reference's full original source — the metered path (§6.2).

    This is where the charge lives now. The references list ships the mesh for
    free; the body is only ever produced here, after ``resolve_access`` has said
    yes, so the meter finally has a server call to sit on.

    Order is load-bearing:

    1. **Ownership.** ``get_workspace_item`` first, exactly like
       ``GET /workspace/{item_id}/references``. Skipping it would be an IDOR that
       hands out another lawyer's research — and would let an attacker mine the
       corpus through victims' item_ids.
    2. **Row lookup**, scoped to that WI. Unknown ``n`` → 404, no charge.
    3. **Resolve** ``ref_id`` → ``(content_type, content_id)`` (D15). Anything
       unresolvable FAILS CLOSED: 402 ``reason='unresolvable'``, never content.
    4. **Entitlement** via ``resolve_access(..., surface='reference')``.
       ``surface`` is analytics ONLY — it must never change the charge, or this
       endpoint becomes the bypass it was built to close (migration 104).
    5. **Build + shelf.** One ``record_use`` call, here (D16.2).

    Returns 200 with ``source_view``, ``unlocked`` (what was unlocked — the نظام,
    not the chunk, per D15.1) and ``balance``; or the D14 402 refusal body;
    ``Cache-Control: private, no-store`` on every path. Rate-limited to 20/min
    per verified caller, sharing ONE budget with ``/library/full/*`` (D13.2).
    """
    validate_uuid(item_id, "معرف العنصر")

    # 1. OWNERSHIP — before anything else. 404 (Arabic) for someone else's item.
    await run_db(
        workspace_service.get_workspace_item, supabase, current_user.auth_id, item_id
    )

    # 2. The reference row, scoped to the WI we just proved ownership of.
    row = await fetch_reference_row(supabase, item_id, n)
    if row is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="المرجع غير موجود",
            headers={"Cache-Control": _SOURCE_CACHE_CONTROL},
        )

    # 3. ref_id → the identity the ledger and the sidecar speak. Fail closed.
    resolved: Optional[ResolvedRef] = await resolve_ref(
        supabase,
        row.get("ref_id") or "",
        domain=row.get("domain"),
        item_id=row.get("item_id"),
    )
    if resolved is None:
        return library_refusal_response(_UnresolvableRef())

    # 4. ENTITLEMENT. user_id is a users.user_id — NEVER an auth_id.
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)

    if resolved.always_free:
        # Policy-open: a compliance service (§1.3) or a short circular that the
        # public page already serves in full. No charge, no ledger row — and no
        # quota numbers either, so the balance chip is left alone.
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
        # NOTHING is built, let alone returned. The refusal sets its own
        # private/no-store header.
        return library_refusal_response(decision)

    # 5. Build the one source view. A None here is a corpus gap (the same
    #    condition the list reports as has_source=False), not a refusal — the
    #    unlock is permanent, so a later retry costs nothing.
    view = await build_reference_source_view(supabase, row)
    if view is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="تعذّر عرض هذا المصدر",
            headers={"Cache-Control": _SOURCE_CACHE_CONTROL},
        )

    # 6. Shelf the use — once, here, charged or not (D16.2).
    await _record_library_use(
        supabase, user_id, resolved.content_type, resolved.content_id
    )

    response.headers["Cache-Control"] = _SOURCE_CACHE_CONTROL
    return {
        "n": int(row.get("n") or n),
        "ref_id": row.get("ref_id") or "",
        "domain": row.get("domain") or "",
        "source_view": view.model_dump(mode="json"),
        # The in-app twin of the external link: where this citation lives in our
        # own library. ``None`` when the item has no published page — the panel
        # drops the button rather than linking into a 404.
        "library_url": await _library_page_url(supabase, resolved),
        "unlocked": {
            "content_type": resolved.content_type,
            "content_id": resolved.content_id,
            # D15.1: name the نظام, never the chunk. The resolver's label wins
            # when it has one; otherwise the source view's own title is already
            # the parent regulation / case / circular title.
            "title": resolved.title or getattr(view, "title", "") or "",
            "article_no": resolved.article_no,
            "charged": bool(decision.charged),
            "cost": int(decision.cost or 0),
            "reason": decision.reason,
        },
        # None when the quota was never consulted (policy-open item), so the
        # frontend can tell "unlimited" apart from "not applicable".
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


@router.patch(
    "/workspace/{item_id}",
    response_model=WorkspaceItemResponse,
)
async def update_workspace_item(
    item_id: str,
    body: UpdateWorkspaceItemRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Update workspace item title/content. Permission keyed on ``kind``."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.update_workspace_item,
        supabase,
        current_user.auth_id,
        item_id,
        content_md=body.content_md,
        title=body.title,
    )
    return _to_response(data)


@router.delete(
    "/workspace/{item_id}",
    response_model=SuccessResponse,
)
async def delete_workspace_item(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a workspace item."""
    validate_uuid(item_id, "معرف العنصر")
    await run_db(
        workspace_service.delete_workspace_item,
        supabase, current_user.auth_id, item_id,
    )
    return SuccessResponse(success=True)


@router.patch(
    "/workspace/{item_id}/visibility",
    response_model=WorkspaceItemResponse,
)
async def update_workspace_visibility(
    item_id: str,
    body: UpdateVisibilityRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Toggle ``is_visible`` (works on any kind, including non-editable ones)."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.update_visibility,
        supabase,
        current_user.auth_id,
        item_id,
        is_visible=body.is_visible,
    )
    return _to_response(data)


@router.patch(
    "/workspace/{item_id}/feedback",
    response_model=WorkspaceItemResponse,
)
async def update_workspace_feedback(
    item_id: str,
    body: UpdateFeedbackRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Set the user's 👍/👎 rating ('up' / 'down' / null). Works on any kind
    (including read-only ones like ``agent_search``) — feedback is a UX flag,
    not content mutation, so it bypasses the kind-edit permission check."""
    validate_uuid(item_id, "معرف العنصر")
    data = await run_db(
        workspace_service.update_feedback,
        supabase,
        current_user.auth_id,
        item_id,
        feedback=body.feedback,
    )
    return _to_response(data)


# ============================================
# CREATE: notes / references
# ============================================


@router.post(
    "/conversations/{conversation_id}/workspace/notes",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def create_note(
    conversation_id: str,
    body: CreateNoteRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a user-authored note inside the conversation workspace."""
    validate_uuid(conversation_id, "معرف المحادثة")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    # OWNERSHIP — load-bearing. ``conversation_id`` is caller-supplied and the
    # row is written with the CALLER's user_id, so without this an attacker who
    # knows a victim's conversation_id plants an item in that conversation: it
    # gets a valid ``wi_seq`` (migration 052), its title/summary are rendered
    # into the victim's router instructions, and it burns the victim's 15-item
    # cap (migration 031 counts per conversation, not per user). Same guard and
    # same Arabic 404 as every sibling write path (api/messages.py:59,
    # workspace_service.py:586 / :722).
    await run_db(
        message_service.verify_conversation_ownership, supabase, conversation_id, user_id
    )
    row = await run_db(
        workspace_service.create_workspace_item,
        supabase,
        user_id,
        kind="note",
        created_by="user",
        title=body.title,
        conversation_id=conversation_id,
        content_md=body.content_md,
    )
    return _to_response(row)


@router.post(
    "/conversations/{conversation_id}/workspace/references",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def create_reference(
    conversation_id: str,
    body: CreateReferenceRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Create a placeholder ``references`` workspace item."""
    validate_uuid(conversation_id, "معرف المحادثة")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)
    # OWNERSHIP — see ``create_note``. Caller-supplied conversation_id.
    await run_db(
        message_service.verify_conversation_ownership, supabase, conversation_id, user_id
    )
    row = await run_db(
        workspace_service.create_workspace_item,
        supabase,
        user_id,
        kind="references",
        created_by="user",
        title=body.title,
        conversation_id=conversation_id,
        content_md=body.content_md or "",
    )
    return _to_response(row)


# ============================================
# CREATE: attachments
# ============================================
#
# Content validation rules (MIME / size / magic bytes) live in
# ``workspace_service.upload_attachment_bytes`` now — the legacy upload route
# only does the async chunked read and delegates the rest off the event loop.


@router.post(
    "/conversations/{conversation_id}/workspace/attachments/upload",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def upload_workspace_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Upload a file attachment into the conversation workspace (legacy
    single-shot multipart).

    The file is stored in the same bucket as case_documents but under a
    ``conversations/{conversation_id}/`` prefix so it is conceptually
    scoped to the conversation, not the case.
    """
    validate_uuid(conversation_id, "معرف المحادثة")

    # Async chunked read — never blocks the loop, enforces the 50 MB cap.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > workspace_service._MAX_FILE_SIZE:
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.DOC_TOO_LARGE,
                detail="حجم الملف يتجاوز الحد الأقصى (50 ميغابايت)",
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    # All sync Supabase/storage round-trips (validation, insert-first, storage
    # write, promote) run off the event loop.
    row = await asyncio.to_thread(
        workspace_service.upload_attachment_bytes,
        supabase,
        current_user.auth_id,
        conversation_id,
        file_bytes=file_bytes,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
    )
    return _to_response(row)


@router.post(
    "/conversations/{conversation_id}/workspace/attachments/from-document",
    response_model=WorkspaceItemResponse,
    status_code=201,
)
async def attach_from_case_document(
    conversation_id: str,
    body: FromDocumentRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Pin an existing case_documents row into this conversation's workspace.

    No file copy -- the workspace item carries a ``document_id`` FK that
    resolves to ``case_documents.storage_path`` at signing time.
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    validate_uuid(body.document_id, "معرف المستند")
    user_id = await run_db(get_user_id, supabase, current_user.auth_id)

    # OWNERSHIP of the CONVERSATION — see ``create_note``. The document check
    # below proves the caller owns the file; it says nothing about the
    # conversation the pin lands in, so both are required.
    await run_db(
        message_service.verify_conversation_ownership, supabase, conversation_id, user_id
    )

    # Verify the document is owned by this user (joins back to lawyer_cases).
    def _fetch_doc_row():
        return (
            supabase.table("case_documents")
            .select("document_name, mime_type, file_size_bytes, lawyer_cases!inner(lawyer_user_id)")
            .eq("document_id", body.document_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )

    try:
        doc_row = await run_db(_fetch_doc_row)
    except Exception as e:
        logger.exception("Error verifying document ownership: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ داخلي",
        )

    if doc_row is None or doc_row.data is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail="المستند غير موجود",
        )
    if doc_row.data.get("lawyer_cases", {}).get("lawyer_user_id") != user_id:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail="المستند غير موجود",
        )

    metadata = {
        "filename": doc_row.data.get("document_name"),
        "mime_type": doc_row.data.get("mime_type"),
        "file_size_bytes": doc_row.data.get("file_size_bytes"),
        "linked_from_case_documents": True,
    }
    row = await run_db(
        workspace_service.create_workspace_item,
        supabase,
        user_id,
        kind="attachment",
        created_by="user",
        title=doc_row.data.get("document_name") or "مرفق",
        conversation_id=conversation_id,
        document_id=body.document_id,
        metadata=metadata,
    )
    return _to_response(row)


# ============================================
# Resumable attachment upload (TUS) — init / finalize / cancel
# ============================================
# Browser uploads bytes directly to Supabase Storage and the backend only
# brokers the session. The legacy multipart upload route above stays for the
# 7-day deprecation soak. Frontend cuts over to these endpoints in Phase 2.


@router.post(
    "/conversations/{conversation_id}/workspace/attachments/init",
    response_model=UploadInitResponse,
    status_code=201,
)
async def init_workspace_attachment_upload(
    conversation_id: str,
    body: UploadInitRequest,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Open a resumable-upload session for a chat-conversation attachment.

    Creates a ``workspace_items`` row with ``kind='attachment'`` and
    ``metadata.upload_status='uploading'``. The client uploads bytes to
    ``upload_url`` (Supabase TUS) using its existing access token, then
    calls ``/finalize``.
    """
    validate_uuid(conversation_id, "معرف المحادثة")
    with _logfire.span(
        "upload.init",
        flow="attachment",
        conversation_id=conversation_id,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
    ) as _span:
        session = await run_db(
            workspace_service.init_attachment_upload,
            supabase,
            current_user.auth_id,
            conversation_id,
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            page_count=body.page_count,
        )
        try:
            _span.set_attribute("item_id", session["item_id"])
        except Exception:
            pass
        return UploadInitResponse(
            item_id=session["item_id"],
            storage_path=session["storage_path"],
            bucket=session["bucket"],
            upload_url=session["upload_url"],
            expires_at=session["expires_at"],
        )


@router.post(
    "/workspace/attachments/{item_id}/finalize",
    response_model=WorkspaceItemResponse,
)
async def finalize_workspace_attachment_upload(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Confirm a resumable chat-attachment upload landed in storage.

    Same semantics as ``/documents/{id}/finalize``: HEAD + size match +
    magic byte check. Flips ``metadata.upload_status='ready'``. Returns
    409 ``UPLOAD_NOT_COMPLETE`` if the object isn't in storage yet.
    """
    validate_uuid(item_id, "معرف العنصر")
    t0 = perf_counter()
    with _logfire.span(
        "upload.finalize",
        flow="attachment",
        item_id=item_id,
    ) as _span:
        result_code = "success"
        try:
            row = await run_db(
                workspace_service.finalize_attachment_upload,
                supabase, current_user.auth_id, item_id
            )
            return _to_response(row)
        except LunaHTTPException as exc:
            if exc.code == ErrorCode.UPLOAD_NOT_COMPLETE:
                result_code = "not_complete"
            elif exc.code == ErrorCode.UPLOAD_SIZE_MISMATCH:
                result_code = "size_mismatch"
            elif exc.code == ErrorCode.DOC_MAGIC_MISMATCH:
                result_code = "magic_mismatch"
            else:
                result_code = "error"
            raise
        finally:
            try:
                _span.set_attributes({
                    "duration_ms": int((perf_counter() - t0) * 1000),
                    "result": result_code,
                })
            except Exception:
                pass


@router.post(
    "/workspace/attachments/{item_id}/cancel",
    response_model=SuccessResponse,
)
async def cancel_workspace_attachment_upload(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Soft-delete a resumable chat-attachment row and best-effort wipe the
    partial storage object. Idempotent."""
    validate_uuid(item_id, "معرف العنصر")
    with _logfire.span(
        "upload.cancel",
        flow="attachment",
        item_id=item_id,
    ):
        await run_db(
            workspace_service.cancel_attachment_upload,
            supabase, current_user.auth_id, item_id
        )
        return SuccessResponse(success=True)


# ============================================
# Signed URL for attachment files
# ============================================


@router.get(
    "/workspace/{item_id}/file",
    response_model=DownloadResponse,
)
async def get_workspace_file_url(
    item_id: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Return a signed URL for the file backing an ``attachment`` item.

    Rules:
        * 404 if the item is not an attachment.
        * If ``storage_path`` is set on the item, sign that path.
        * Otherwise resolve ``document_id`` -> ``case_documents.storage_path``
          and sign that. (No file copy -- linked attachments share the
          underlying object with the case library.)
        * 404 with a purge-specific message once the retention sweep has
          cleared ``storage_path`` (``metadata.original_purged_at`` is set).
          The item itself is alive and ``content_md`` still holds its text --
          only the original file is gone.
    """
    validate_uuid(item_id, "معرف العنصر")
    item = await run_db(
        workspace_service.get_workspace_item,
        supabase, current_user.auth_id, item_id,
    )

    if item.get("kind") != "attachment":
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.ARTIFACT_NOT_FOUND,
            detail="هذا العنصر لا يحتوي ملفاً",
        )

    settings = get_settings()
    bucket = settings.STORAGE_BUCKET_DOCUMENTS

    storage_path = item.get("storage_path")
    if not storage_path and item.get("document_id"):
        # Resolve the linked case_documents path.
        def _fetch_storage_path():
            return (
                supabase.table("case_documents")
                .select("storage_path")
                .eq("document_id", item["document_id"])
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )

        try:
            doc_row = await run_db(_fetch_storage_path)
        except Exception as e:
            logger.exception("Error resolving linked case_document: %s", e)
            raise LunaHTTPException(
                status_code=500,
                code=ErrorCode.INTERNAL_ERROR,
                detail="حدث خطأ داخلي",
            )
        if doc_row is None or doc_row.data is None:
            raise LunaHTTPException(
                status_code=404,
                code=ErrorCode.DOC_NOT_FOUND,
                detail="ملف المستند غير موجود",
            )
        storage_path = doc_row.data.get("storage_path")

    if not storage_path:
        # A purged attachment is not a broken one: the retention sweep removed
        # the original but kept the item and its extracted text, so say which
        # of the two happened rather than reporting a generic missing file.
        purged = (item.get("metadata") or {}).get("original_purged_at")
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.DOC_NOT_FOUND,
            detail=(
                "حُذف الملف الأصلي بعد انتهاء مدة الاحتفاظ — النص المستخرج منه ما زال محفوظاً"
                if purged
                else "ملف المستند غير موجود"
            ),
        )

    try:
        url = await run_db(
            get_signed_url, bucket, storage_path, expires_in=3600, supabase=supabase
        )
    except Exception as e:
        logger.exception("Error generating signed URL for workspace file: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء إنشاء رابط الملف",
        )

    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    return DownloadResponse(url=url, expires_at=expires_at)
