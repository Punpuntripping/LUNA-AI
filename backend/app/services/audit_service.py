"""
Audit logging service.
Fire-and-forget writer -- failures NEVER block user operations.
"""
import logging
from typing import Optional

from backend.app.middleware.request_context import get_client_ip, get_user_agent

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

    ``ip_address`` and ``user_agent`` are filled from the ambient request
    context (see backend/app/middleware/request_context.py) rather than from
    arguments, so no call site has to thread a Request through. Outside a
    request -- scripts, APScheduler jobs, agent workers -- both resolve to None
    and the columns stay NULL, which is the historical behaviour.
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

        ip = get_client_ip()
        if ip:
            payload["ip_address"] = ip
        user_agent = get_user_agent()
        if user_agent:
            payload["user_agent"] = user_agent

        supabase.table("audit_logs").insert(payload).execute()
    except Exception as e:
        logger.warning("Audit log write failed (non-blocking): %s", e)
