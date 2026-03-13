"""
Audit logging service.
Fire-and-forget writer -- failures NEVER block user operations.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def write_audit_log(
    supabase,
    *,
    user_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """
    Insert a row into audit_logs. Best-effort only.
    Failures are logged as warnings and swallowed -- they must NEVER
    raise exceptions or block the response.
    """
    try:
        payload: dict = {
            "user_id": str(user_id),
            "action": action,
            "resource_type": resource_type,
        }
        if resource_id:
            payload["resource_id"] = str(resource_id)
        if metadata:
            payload["metadata"] = metadata

        supabase.table("audit_logs").insert(payload).execute()
    except Exception as e:
        logger.warning("Audit log write failed (non-blocking): %s", e)
