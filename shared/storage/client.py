"""
Supabase Storage client wrapper.
Provides upload, download (signed URL), and delete operations.
"""
from __future__ import annotations

import logging
import re
import uuid

from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.client import get_supabase_client

logger = logging.getLogger(__name__)


def _sanitize_filename(filename: str) -> str:
    """Remove path separators, null bytes, and other unsafe characters."""
    # Remove path separators and null bytes
    sanitized = re.sub(r'[\x00/\\]', '', filename)
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    # Keep only safe characters
    sanitized = re.sub(r'[^\w.\-]', '', sanitized)
    return sanitized or "file"


def upload_file(
    bucket: str,
    path: str,
    file_bytes: bytes,
    content_type: str,
    supabase: SupabaseClient | None = None,
) -> str:
    """
    Upload a file to Supabase Storage.

    Args:
        bucket: Storage bucket name (e.g. "case-documents")
        path: Full storage path (e.g. "cases/{case_id}/convos/{convo_id}/{uuid}_{filename}")
        file_bytes: Raw file bytes
        content_type: MIME type (e.g. "application/pdf")
        supabase: Optional Supabase client to reuse (falls back to get_supabase_client())

    Returns:
        The storage path of the uploaded file.
    """
    client = supabase or get_supabase_client()

    try:
        client.storage.from_(bucket).upload(
            path=path,
            file=file_bytes,
            file_options={"content-type": content_type},
        )
        logger.info("Uploaded file to %s/%s", bucket, path)
        return path
    except Exception as e:
        logger.exception("Failed to upload file to %s/%s: %s", bucket, path, e)
        raise


def get_signed_url(
    bucket: str,
    path: str,
    expires_in: int = 3600,
    supabase: SupabaseClient | None = None,
) -> str:
    """
    Generate a signed download URL for a storage object.

    Args:
        bucket: Storage bucket name
        path: Storage path
        expires_in: URL expiry in seconds (default 1 hour)
        supabase: Optional Supabase client to reuse (falls back to get_supabase_client())

    Returns:
        Signed URL string.
    """
    client = supabase or get_supabase_client()

    try:
        result = client.storage.from_(bucket).create_signed_url(path, expires_in)
        return result["signedURL"]
    except Exception as e:
        logger.exception("Failed to create signed URL for %s/%s: %s", bucket, path, e)
        raise


def delete_file(
    bucket: str,
    path: str,
    supabase: SupabaseClient | None = None,
) -> bool:
    """Delete a single file from storage."""
    client = supabase or get_supabase_client()

    try:
        client.storage.from_(bucket).remove([path])
        logger.info("Deleted file %s/%s", bucket, path)
        return True
    except Exception as e:
        logger.exception("Failed to delete file %s/%s: %s", bucket, path, e)
        return False


def delete_folder(
    bucket: str,
    folder_path: str,
    supabase: SupabaseClient | None = None,
) -> int:
    """Delete all files under a folder prefix."""
    client = supabase or get_supabase_client()

    try:
        files = client.storage.from_(bucket).list(folder_path)
        if not files:
            return 0

        paths = [f"{folder_path}/{f['name']}" for f in files]
        client.storage.from_(bucket).remove(paths)
        logger.info("Deleted %d files from %s/%s", len(paths), bucket, folder_path)
        return len(paths)
    except Exception as e:
        logger.exception("Failed to delete folder %s/%s: %s", bucket, folder_path, e)
        return 0


def build_storage_path(
    case_id: str | None,
    user_id: str,
    conversation_id: str | None,
    filename: str,
) -> str:
    """Build a storage path with UUID prefix to prevent collisions."""
    safe_name = _sanitize_filename(filename)
    file_id = str(uuid.uuid4())[:8]

    if case_id and conversation_id:
        return f"cases/{case_id}/convos/{conversation_id}/{file_id}_{safe_name}"
    elif case_id:
        return f"cases/{case_id}/{file_id}_{safe_name}"
    elif conversation_id:
        return f"general/{user_id}/convos/{conversation_id}/{file_id}_{safe_name}"
    else:
        return f"general/{user_id}/{file_id}_{safe_name}"
