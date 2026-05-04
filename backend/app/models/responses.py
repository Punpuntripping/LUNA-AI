"""
Pydantic response models for API endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


from pydantic import BaseModel


# ── Shared ────────────────────────────────────────────

class SuccessResponse(BaseModel):
    """Generic success envelope."""
    success: bool = True


# ── Auth ──────────────────────────────────────────────

class UserProfile(BaseModel):
    """Embedded user profile in auth responses."""
    user_id: str
    email: str
    full_name_ar: Optional[str] = None
    subscription_tier: Optional[str] = None
    created_at: Optional[datetime] = None


class LoginResponse(BaseModel):
    """POST /api/v1/auth/login"""
    access_token: str
    refresh_token: str
    user: UserProfile


class RegisterResponse(BaseModel):
    """POST /api/v1/auth/register"""
    user: UserProfile
    verification_sent: bool = True


class TokenResponse(BaseModel):
    """POST /api/v1/auth/refresh"""
    access_token: str
    refresh_token: str


class UserProfileResponse(BaseModel):
    """GET /api/v1/auth/me — full profile from users table."""
    user_id: str
    email: str
    full_name_ar: Optional[str] = None
    subscription_tier: Optional[str] = None
    created_at: Optional[datetime] = None


# ── Conversations ──────────────────────────────────────
# NOTE: Defined before Cases because CaseDetailResponse references ConversationSummary.

class ConversationSummary(BaseModel):
    """Conversation list item."""
    conversation_id: str
    case_id: Optional[str] = None
    title_ar: Optional[str] = None
    message_count: int = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    """Full conversation details."""
    model_name: Optional[str] = None


class ConversationListResponse(BaseModel):
    """GET /api/v1/conversations"""
    conversations: list[ConversationSummary]
    total: int
    has_more: bool


class ConversationResponse(BaseModel):
    """Single conversation envelope."""
    conversation: ConversationDetail


# ── Cases ──────────────────────────────────────────────

class CaseSummary(BaseModel):
    """Case list item."""
    case_id: str
    case_name: str
    case_type: str
    status: str
    priority: str
    description: Optional[str] = None
    case_number: Optional[str] = None
    court_name: Optional[str] = None
    conversation_count: int = 0
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class CaseDetail(CaseSummary):
    """Full case details."""
    parties: Optional[dict] = None


class CaseStats(BaseModel):
    """Aggregated statistics for a case."""
    total_conversations: int = 0
    total_documents: int = 0
    total_memories: int = 0


class CaseListResponse(BaseModel):
    """GET /api/v1/cases"""
    cases: list[CaseSummary]
    total: int
    page: int
    per_page: int


class CreateCaseResponse(BaseModel):
    """POST /api/v1/cases — returns case + first conversation."""
    case: CaseDetail
    first_conversation_id: str


class CaseResponse(BaseModel):
    """Single case envelope (for update/archive)."""
    case: CaseDetail


class CaseDetailResponse(BaseModel):
    """GET /api/v1/cases/{case_id} — case + conversations + stats."""
    case: CaseDetail
    conversations: list[ConversationSummary]
    stats: CaseStats


# ── Messages ──────────────────────────────────────────

class AttachmentResponse(BaseModel):
    """Attachment metadata within a message."""
    id: str
    document_id: str
    attachment_type: str
    filename: str
    file_size: Optional[int] = None


class MessageResponse(BaseModel):
    """Single message in a conversation."""
    message_id: str
    conversation_id: str
    role: str
    content: str
    model: Optional[str] = None
    attachments: list[AttachmentResponse] = []
    metadata: dict = {}
    created_at: str


class MessageListResponse(BaseModel):
    """GET /api/v1/conversations/{conversation_id}/messages"""
    messages: list[MessageResponse]
    has_more: bool


# ── Documents ─────────────────────────────────────────

class DocumentResponse(BaseModel):
    """Single document metadata."""
    document_id: str
    case_id: str
    document_name: str
    mime_type: str
    file_size_bytes: int
    extraction_status: str
    created_at: str


class DocumentListResponse(BaseModel):
    """GET /api/v1/cases/{case_id}/documents"""
    documents: list[DocumentResponse]
    total: int


class DownloadResponse(BaseModel):
    """GET /api/v1/documents/{document_id}/download"""
    url: str
    expires_at: str


# ── Memories ──────────────────────────────────────────

class MemoryResponse(BaseModel):
    """Single memory entry."""
    memory_id: str
    case_id: str
    memory_type: str
    content_ar: str
    confidence_score: Optional[float] = None
    created_at: str
    updated_at: str


class MemoryListResponse(BaseModel):
    """GET /api/v1/cases/{case_id}/memories"""
    memories: list[MemoryResponse]
    total: int


# ── Workspace items (post-026) ───────────────────────────


class WorkspaceItemResponse(BaseModel):
    """Single workspace item.

    Replaces ArtifactResponse. ``artifact_type`` is gone -- subtype now lives
    in ``metadata.subtype`` and is treated as a free-form string by the
    backend.
    """
    item_id: str
    user_id: str
    conversation_id: Optional[str] = None
    case_id: Optional[str] = None
    message_id: Optional[str] = None
    agent_family: Optional[str] = None
    kind: str
    created_by: str
    title: str
    content_md: Optional[str] = None
    storage_path: Optional[str] = None
    document_id: Optional[str] = None
    is_visible: bool = True
    metadata: dict = {}
    created_at: str
    updated_at: str


class WorkspaceItemListResponse(BaseModel):
    """GET /api/v1/conversations/{id}/workspace or cases/{id}/workspace"""
    items: list[WorkspaceItemResponse]
    total: int


# ── Preferences ──────────────────────────────────────────

class PreferencesResponse(BaseModel):
    """GET/PATCH /api/v1/preferences"""
    user_id: str
    preferences: dict
