"""
Supabase Storage client wrapper.
Provides upload, download (signed URL), and delete operations.
"""
from __future__ import annotations

import logging
import re
import uuid
from urllib.parse import quote

import httpx
from supabase import Client as SupabaseClient

from shared.config import get_settings
from shared.db.client import get_supabase_client

logger = logging.getLogger(__name__)

# Tight ceiling for the magic-byte range read — 16 bytes should never take
# longer than a few seconds even on a cold connection. Keeps a hung storage
# endpoint from wedging the finalize/reconcile path.
STORAGE_HEAD_TIMEOUT = 5.0  # seconds


class _RangeUnsupported(Exception):
    """Server rejected the Range request (416/501) — fall back to a full
    download. Module-private: callers never see this exception type."""

    def __init__(self, status: int) -> None:
        super().__init__(f"range unsupported (HTTP {status})")
        self.status = status


def _sanitize_filename(filename: str) -> str:
    """Strip a filename to ASCII-safe characters for the storage key.

    Supabase Storage rejects non-ASCII characters in object keys with
    HTTP 400 "Invalid key". Python's ``\\w`` is Unicode-aware so an
    earlier version of this function silently kept Arabic letters,
    which produced storage paths the storage API refused.

    The user-visible filename is preserved separately on the DB row
    (``case_documents.document_name``, ``workspace_items.metadata.filename``),
    so it is safe to strip the filename here without losing display fidelity.
    """
    # Split on the LAST dot so multi-dot names keep their real extension.
    if "." in filename:
        base, ext = filename.rsplit(".", 1)
    else:
        base, ext = filename, ""

    # Spaces -> underscores so the basename stays a single token.
    base = base.replace(" ", "_")

    # ASCII-only: alphanumerics, underscore, hyphen. Anything else is dropped.
    safe_base = re.sub(r"[^A-Za-z0-9_\-]", "", base)
    safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext)

    # If sanitisation stripped everything meaningful (e.g. pure-Arabic name),
    # fall back to "file" so the storage key is still a valid identifier.
    if not safe_base or all(c in "_-" for c in safe_base):
        safe_base = "file"

    return f"{safe_base}.{safe_ext}" if safe_ext else safe_base


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


def head_object(
    bucket: str,
    path: str,
    supabase: SupabaseClient | None = None,
) -> dict | None:
    """
    Return ``{'size': int, 'content_type': str}`` if the object exists, else None.

    Uses the supabase-py v2 ``info()`` endpoint which performs an authenticated
    GET against ``/storage/v1/object/info/{bucket}/{path}``. Returns ``None`` on
    any failure (404, network error, missing keys) so callers can treat absence
    as the "upload not complete" case without try/except gymnastics.
    """
    client = supabase or get_supabase_client()

    try:
        info = client.storage.from_(bucket).info(path)
    except Exception as e:
        # 404 is the normal "object not yet uploaded" case during finalize.
        # Log at debug level; INFO is too noisy when the client polls.
        logger.debug("head_object: %s/%s missing or unreadable (%s)", bucket, path, e)
        return None

    if not isinstance(info, dict):
        return None

    # Supabase ``info`` returns a flat dict. Different supabase-py versions
    # expose the size under either top-level ``size`` or a nested
    # ``metadata.size`` — we tolerate both.
    size = info.get("size")
    if size is None:
        metadata = info.get("metadata") or {}
        size = metadata.get("size") or metadata.get("contentLength")

    content_type = (
        info.get("content_type")
        or info.get("contentType")
        or (info.get("metadata") or {}).get("mimetype")
        or (info.get("metadata") or {}).get("mimeType")
    )

    if size is None:
        # Object exists but we couldn't read its size — treat as missing so the
        # caller falls into the UPLOAD_NOT_COMPLETE path and the client retries.
        logger.warning("head_object: %s/%s exists but size is unreadable", bucket, path)
        return None

    try:
        size_int = int(size)
    except (TypeError, ValueError):
        logger.warning("head_object: %s/%s size %r is not an int", bucket, path, size)
        return None

    return {
        "size": size_int,
        "content_type": content_type or "application/octet-stream",
    }


def download_head_bytes(
    bucket: str,
    path: str,
    n: int = 16,
    supabase: SupabaseClient | None = None,
) -> bytes:
    """Read the first ``n`` bytes of an object via an HTTP Range request.

    Issues a raw httpx ``GET`` against the storage REST endpoint with
    service-role auth and a ``Range: bytes=0-(n-1)`` header — Supabase
    storage-api supports byte-range on authenticated object GETs. This avoids
    pulling a whole (up to 50 MB) object into RAM just to magic-byte-check its
    header, which is what the previous ``download()`` implementation did.

    Falls back to a bounded streamed read if the server rejects ranges
    (416/501) or replies 200 with the whole body. Other exceptions (timeout,
    transport error, non-range HTTP error) propagate — the caller
    (``upload_session_service.verify_finalize``) already maps any failure to
    ``UPLOAD_NOT_COMPLETE`` so the client retries, which is the right outcome
    for a timeout too.
    """
    settings = get_settings()
    base = settings.SUPABASE_URL.rstrip("/")
    url = f"{base}/storage/v1/object/{bucket}/{quote(path)}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Range": f"bytes=0-{n - 1}",
    }
    try:
        with httpx.Client(timeout=STORAGE_HEAD_TIMEOUT) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (200, 206):
                    # 206 = ranged body; 200 = server ignored Range — either
                    # way read at most ``n`` bytes and bail out of the stream
                    # so even a misbehaving 200 cannot force a 50 MB buffer.
                    buf = b""
                    for chunk in resp.iter_bytes(chunk_size=n):
                        buf += chunk
                        if len(buf) >= n:
                            break
                    return buf[:n]
                if resp.status_code in (416, 501):
                    raise _RangeUnsupported(resp.status_code)
                # Some other HTTP error — read the (small) body so
                # raise_for_status reports cleanly, then raise.
                resp.read()
                resp.raise_for_status()
                # raise_for_status only raises on >=400; a 3xx slipping through
                # here is unexpected, so fall back rather than return garbage.
                raise _RangeUnsupported(resp.status_code)
    except _RangeUnsupported as exc:
        logger.warning(
            "download_head_bytes: range unsupported (%s) for %s/%s — "
            "falling back to full download",
            exc.status,
            bucket,
            path,
        )
        return _download_head_bytes_legacy(bucket, path, n, supabase=supabase)


def _download_head_bytes_legacy(
    bucket: str,
    path: str,
    n: int = 16,
    supabase: SupabaseClient | None = None,
) -> bytes:
    """Fallback for ``download_head_bytes``: pull the full object via the
    supabase-py ``download()`` API and slice locally.

    Only reached when the storage server rejects Range requests (416/501).
    ``n`` is small (≤ 16 bytes in practice) so the over-fetch is the cost of
    correctness for files we already accepted at upload time (≤ 50 MB cap).
    """
    client = supabase or get_supabase_client()
    body = client.storage.from_(bucket).download(path)
    if not isinstance(body, (bytes, bytearray)):
        # supabase-py v2 returns bytes; guard against future-shape drift.
        raise TypeError(f"download() returned unexpected type {type(body)!r}")
    return bytes(body[:n])


def get_resumable_upload_url(supabase_url: str | None = None) -> str:
    """
    Return the Supabase TUS resumable upload endpoint URL.

    Supabase routes both ``<project>.supabase.co/storage/v1/upload/resumable``
    and ``<project>.storage.supabase.co/storage/v1/upload/resumable`` — we use
    the former because that is what ``SUPABASE_URL`` already points at.
    """
    base = (supabase_url or get_settings().SUPABASE_URL).rstrip("/")
    return f"{base}/storage/v1/upload/resumable"
