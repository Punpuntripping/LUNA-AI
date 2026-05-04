"""Workspace context loader for agent prompt injection.

Wave 8A — Conversation Workspace.

Loads all visible workspace items for a conversation and partitions them
by ``kind`` so agents (router, deep_search, writer) can ground their
prompts on the user's running context: visible attachments, notes,
prior agent outputs, conversation summary, and references.

Targets the post-migration-026 schema (``workspace_items`` table with
``kind`` / ``is_visible`` columns) and falls back to the pre-migration
``artifacts`` table so callers stay safe before migration 026 applies.
The fallback maps Cut-1 rows as follows:

    artifacts.is_editable=False  -> kind='agent_search'
    artifacts.is_editable=True   -> kind='agent_writing'

The fallback cannot synthesize ``note`` / ``attachment`` /
``convo_context`` / ``references`` rows because those kinds did not
exist before migration 026 -- those buckets stay empty.

This loader NEVER raises into agent code: any database error returns
the empty context shape so the writer turn keeps going.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Public empty shape -- callers can rely on every key being present.
_EMPTY_CONTEXT: dict[str, Any] = {
    "attachments": [],
    "notes": [],
    "agent_outputs": [],
    "convo_context": None,
    "references": [],
}

# Excerpt size for attachment text extraction (post-migration document_id path).
_ATTACHMENT_EXCERPT_CHARS = 500

# Kinds that the helper recognizes; anything else is silently ignored so the
# helper stays forward-compatible if future migrations add new kinds.
_AGENT_OUTPUT_KINDS = {"agent_search", "agent_writing"}


async def load_workspace_context(
    supabase: Any, conversation_id: str
) -> dict[str, Any]:
    """Load all visible workspace items for a conversation.

    Returns a dict with these keys (always present):

        attachments:  list[{item_id, title, kind="attachment", text_excerpt}]
        notes:        list[{item_id, title, content_md}]
        agent_outputs:list[{item_id, title, content_md, subtype}]
        convo_context: {item_id, content_md} | None  (most recent only)
        references:   list[{item_id, title, content_md}]

    Args:
        supabase: Supabase client (sync, used in async context per the
            project's established pattern).
        conversation_id: Target conversation.

    Notes:
        * Query orders rows by ``created_at DESC`` so partition lists
          come out newest-first (callers can sort otherwise if needed).
        * On any database error the function returns the empty shape and
          logs a warning -- the caller (typically the writer turn or the
          router) MUST be able to keep going.
        * Pre-migration-026 fallback: if the ``workspace_items`` table
          doesn't exist yet, falls back to ``artifacts`` with the Cut-1
          ``is_editable`` -> kind mapping.
    """
    try:
        rows = _query_workspace_items(supabase, conversation_id)
    except Exception as exc:  # noqa: BLE001 -- broad on purpose, see below
        # Two failure modes we care about:
        #   1. Pre-migration: relation "workspace_items" does not exist.
        #      Fall back to artifacts so Cut-1 keeps working.
        #   2. Anything else (network, RLS, malformed row): return the
        #      empty context so the agent turn doesn't crash.
        msg = str(exc)
        if _is_relation_missing_error(msg):
            logger.info(
                "workspace_context: workspace_items table not present; "
                "falling back to artifacts (pre-migration-026 path)"
            )
            try:
                rows = _query_artifacts_fallback(supabase, conversation_id)
            except Exception as fallback_exc:  # noqa: BLE001
                logger.warning(
                    "workspace_context: artifacts fallback failed: %s",
                    fallback_exc,
                    exc_info=True,
                )
                return _empty_context()
        else:
            logger.warning(
                "workspace_context: load failed for conversation %s: %s",
                conversation_id, exc, exc_info=True,
            )
            return _empty_context()

    if not rows:
        return _empty_context()

    return _partition(supabase, rows)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _query_workspace_items(supabase: Any, conversation_id: str) -> list[dict]:
    """Post-migration-026 query."""
    result = (
        supabase.table("workspace_items")
        .select("*")
        .eq("conversation_id", conversation_id)
        .eq("is_visible", True)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return list(result.data or [])


def _query_artifacts_fallback(
    supabase: Any, conversation_id: str
) -> list[dict]:
    """Pre-migration-026 fallback.

    Translates ``artifacts`` rows into the workspace_items shape so the
    rest of the partitioner doesn't have to branch. Maps:

        artifacts.artifact_id  -> item_id
        artifacts.is_editable  -> kind ('agent_writing' | 'agent_search')
        artifacts.metadata     -> metadata (with subtype falling back to
                                   artifact_type if missing)

    Notes / attachments / convo_context / references: not representable
    pre-migration, so their buckets stay empty.
    """
    result = (
        supabase.table("artifacts")
        .select(
            "artifact_id, conversation_id, title, content_md, "
            "artifact_type, is_editable, metadata, created_at"
        )
        .eq("conversation_id", conversation_id)
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    raw = list(result.data or [])

    translated: list[dict] = []
    for row in raw:
        kind = "agent_writing" if row.get("is_editable") else "agent_search"
        metadata = dict(row.get("metadata") or {})
        # Promote artifact_type -> metadata.subtype so partitioner sees it.
        if "subtype" not in metadata and row.get("artifact_type"):
            metadata["subtype"] = row["artifact_type"]
        translated.append({
            "item_id": row.get("artifact_id"),
            "conversation_id": row.get("conversation_id"),
            "title": row.get("title", ""),
            "content_md": row.get("content_md"),
            "kind": kind,
            "metadata": metadata,
            "storage_path": None,
            "document_id": None,
            "created_at": row.get("created_at"),
        })
    return translated


def _is_relation_missing_error(msg: str) -> bool:
    """Heuristic: did the DB say `workspace_items` doesn't exist?

    PostgREST surfaces this as ``{ code: '42P01', message: ... }`` and
    supabase-py wraps it in an APIError whose ``str(...)`` includes
    either ``42P01`` or the literal ``"relation \"workspace_items\""``.
    Either is enough to trigger the fallback.
    """
    lowered = msg.lower()
    return (
        "42p01" in lowered
        or "workspace_items" in lowered
        and ("does not exist" in lowered or "not found" in lowered)
    )


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def _partition(supabase: Any, rows: list[dict]) -> dict[str, Any]:
    """Bucket rows by kind into the agent-facing context shape."""
    out = _empty_context()

    for row in rows:
        kind = row.get("kind")
        if kind == "attachment":
            out["attachments"].append(_format_attachment(supabase, row))
        elif kind == "note":
            out["notes"].append({
                "item_id": row.get("item_id"),
                "title": row.get("title", ""),
                "content_md": row.get("content_md") or "",
            })
        elif kind in _AGENT_OUTPUT_KINDS:
            metadata = row.get("metadata") or {}
            out["agent_outputs"].append({
                "item_id": row.get("item_id"),
                "title": row.get("title", ""),
                "content_md": row.get("content_md") or "",
                "subtype": metadata.get("subtype"),
            })
        elif kind == "convo_context":
            # Rows are ordered desc by created_at; first one we see wins.
            if out["convo_context"] is None:
                out["convo_context"] = {
                    "item_id": row.get("item_id"),
                    "content_md": row.get("content_md") or "",
                }
        elif kind == "references":
            out["references"].append({
                "item_id": row.get("item_id"),
                "title": row.get("title", ""),
                "content_md": row.get("content_md") or "",
            })
        # else: unknown kind -- silently ignore (forward compat).

    return out


def _format_attachment(supabase: Any, row: dict) -> dict[str, Any]:
    """Build the attachment dict with optional text excerpt.

    Two paths:
        1. ``document_id`` is set -- look up case_documents.content_text
           and surface the first ~500 chars as text_excerpt.
        2. ``storage_path`` is set without document_id -- title only.
           Text extraction from raw storage uploads is out of scope for
           Wave 8A; left as a TODO for the document-ingest pipeline.
    """
    item: dict[str, Any] = {
        "item_id": row.get("item_id"),
        "title": row.get("title", ""),
        "kind": "attachment",
        "text_excerpt": None,
    }

    document_id = row.get("document_id")
    if document_id:
        excerpt = _fetch_document_excerpt(supabase, document_id)
        if excerpt:
            item["text_excerpt"] = excerpt
        return item

    # storage_path-only path: title and (eventually) signed URL via the
    # workspace endpoints. Text extraction TODO for Wave 8A+.
    if row.get("storage_path"):
        # TODO(wave-8A+): plumb attachment text extraction from raw
        # Supabase Storage uploads (mirror case_documents OCR pipeline).
        pass

    return item


def _fetch_document_excerpt(
    supabase: Any, document_id: str
) -> Optional[str]:
    """Pull the first ~500 chars of case_documents.content_text.

    Returns None on any error or if the document has no extracted text.
    Never raises -- attachment context degrades gracefully to title-only.
    """
    try:
        result = (
            supabase.table("case_documents")
            .select("content_text")
            .eq("document_id", document_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "workspace_context: case_documents lookup failed for %s: %s",
            document_id, exc,
        )
        return None

    if not result or not getattr(result, "data", None):
        return None

    text = result.data.get("content_text")
    if not text:
        return None

    return text[:_ATTACHMENT_EXCERPT_CHARS]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _empty_context() -> dict[str, Any]:
    """Fresh empty context dict (callers mutate, so don't share state)."""
    return {
        "attachments": [],
        "notes": [],
        "agent_outputs": [],
        "convo_context": None,
        "references": [],
    }


__all__ = ["load_workspace_context"]
