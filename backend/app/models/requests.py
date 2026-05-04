"""
Pydantic request models for API endpoints.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


def _reject_null_bytes(v: str) -> str:
    """Reject strings containing null bytes (PostgreSQL incompatible)."""
    if v and "\x00" in v:
        raise ValueError("يحتوي النص على أحرف غير مسموحة")
    return v


# ── Auth ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    """POST /api/v1/auth/login"""
    email: EmailStr
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    """POST /api/v1/auth/register"""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name_ar: str = Field(..., min_length=2, max_length=100)


class RefreshRequest(BaseModel):
    """POST /api/v1/auth/refresh"""
    refresh_token: str = Field(..., min_length=1)


# ── Cases ──────────────────────────────────────────────

class CreateCaseRequest(BaseModel):
    """POST /api/v1/cases"""
    case_name: str = Field(..., min_length=2, max_length=255)
    case_type: str = Field(default="عام")
    description: Optional[str] = Field(None, max_length=2000)
    case_number: Optional[str] = Field(None, max_length=100)
    court_name: Optional[str] = Field(None, max_length=255)
    priority: str = Field(default="medium")

    @field_validator("case_name", "description", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v


class UpdateCaseRequest(BaseModel):
    """PUT /api/v1/cases/{case_id}"""
    case_name: Optional[str] = Field(None, min_length=2, max_length=255)
    case_type: Optional[str] = None
    description: Optional[str] = Field(None, max_length=2000)
    case_number: Optional[str] = Field(None, max_length=100)
    court_name: Optional[str] = Field(None, max_length=255)
    priority: Optional[str] = None

    @field_validator("case_name", "description", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v


class UpdateCaseStatusRequest(BaseModel):
    """PATCH /api/v1/cases/{case_id}/status"""
    status: str = Field(...)


# ── Conversations ──────────────────────────────────────

class CreateConversationRequest(BaseModel):
    """POST /api/v1/conversations"""
    case_id: Optional[str] = None  # UUID string or null for general convo


class UpdateConversationRequest(BaseModel):
    """PUT /api/v1/conversations/{conversation_id}"""
    title_ar: str = Field(..., min_length=1, max_length=500)


# ── Messages ──────────────────────────────────────────

class SendMessageRequest(BaseModel):
    """POST /api/v1/conversations/{conversation_id}/messages"""
    content: str = Field(..., min_length=1, max_length=10_000)
    attachment_ids: Optional[list[str]] = None  # document_ids to attach to the message

    @field_validator("content", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v


# ── Memories ──────────────────────────────────────────

class CreateMemoryRequest(BaseModel):
    """POST /api/v1/cases/{case_id}/memories"""
    memory_type: str  # fact, document_reference, strategy, deadline, party_info
    content_ar: str = Field(..., min_length=1, max_length=5_000)

    @field_validator("content_ar", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v


class UpdateMemoryRequest(BaseModel):
    """PATCH /api/v1/memories/{memory_id}"""
    content_ar: Optional[str] = Field(None, max_length=5_000)
    memory_type: Optional[str] = None

    @field_validator("content_ar", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v


# ── Workspace items (post-026) ─────────────────────────

class UpdateWorkspaceItemRequest(BaseModel):
    """PATCH /api/v1/workspace/{item_id}

    Same shape as the legacy artifact update -- only title and content_md.
    """
    title: Optional[str] = None
    content_md: Optional[str] = None


# ── Preferences ──────────────────────────────────────────

class UpdatePreferencesRequest(BaseModel):
    """PATCH /api/v1/preferences"""
    preferences: dict
