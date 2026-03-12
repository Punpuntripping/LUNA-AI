"""
Pydantic request models for API endpoints.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


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
    refresh_token: str


# ── Cases ──────────────────────────────────────────────

class CreateCaseRequest(BaseModel):
    """POST /api/v1/cases"""
    case_name: str = Field(..., min_length=2, max_length=500)
    case_type: str = Field(default="عام")
    description: Optional[str] = Field(None, max_length=2000)
    case_number: Optional[str] = Field(None, max_length=100)
    court_name: Optional[str] = Field(None, max_length=255)
    priority: str = Field(default="medium")


class UpdateCaseRequest(BaseModel):
    """PUT /api/v1/cases/{case_id}"""
    case_name: Optional[str] = Field(None, min_length=2, max_length=500)
    case_type: Optional[str] = None
    description: Optional[str] = Field(None, max_length=2000)
    case_number: Optional[str] = Field(None, max_length=100)
    court_name: Optional[str] = Field(None, max_length=255)
    priority: Optional[str] = None


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
    agent_family: Optional[str] = None    # Explicit agent selection (skip classifier)
    modifiers: Optional[list[str]] = None  # ["plan", "reflect"]


# ── Memories ──────────────────────────────────────────

class CreateMemoryRequest(BaseModel):
    """POST /api/v1/cases/{case_id}/memories"""
    memory_type: str  # fact, document_reference, strategy, deadline, party_info
    content_ar: str = Field(..., min_length=1, max_length=5_000)


class UpdateMemoryRequest(BaseModel):
    """PATCH /api/v1/memories/{memory_id}"""
    content_ar: Optional[str] = Field(None, max_length=5_000)
    memory_type: Optional[str] = None


# ── Artifacts ──────────────────────────────────────────

class UpdateArtifactRequest(BaseModel):
    """PATCH /api/v1/artifacts/{artifact_id}"""
    title: Optional[str] = None
    content_md: Optional[str] = None


# ── Preferences ──────────────────────────────────────────

class UpdatePreferencesRequest(BaseModel):
    """PATCH /api/v1/preferences"""
    preferences: dict


# ── Templates ──────────────────────────────────────────

class CreateTemplateRequest(BaseModel):
    """POST /api/v1/templates"""
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""
    prompt_template: str = Field(..., min_length=1)
    agent_family: str = "end_services"


class UpdateTemplateRequest(BaseModel):
    """PATCH /api/v1/templates/{template_id}"""
    title: Optional[str] = None
    description: Optional[str] = None
    prompt_template: Optional[str] = None
    agent_family: Optional[str] = None
    is_active: Optional[bool] = None
