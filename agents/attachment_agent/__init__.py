"""Attachment agent — the router's way into the user's uploaded files.

Plan: .claude/plans/attachment_agent.md. Layer 3 (answers only to the router),
slot ``attachment_agent`` (Flash). Flag: ``ATTACHMENT_AGENT_ENABLED`` /
``ATTACHMENT_AGENT_USERS`` (``agents.utils.attachment_agent_flags``).
"""
from agents.attachment_agent.models import (
    AttachmentAgentCall,
    AttachmentAgentResult,
    PointOut,
    QuoteOut,
)
from agents.attachment_agent.runner import run_attachment_agent

__all__ = [
    "AttachmentAgentCall",
    "AttachmentAgentResult",
    "PointOut",
    "QuoteOut",
    "run_attachment_agent",
]
