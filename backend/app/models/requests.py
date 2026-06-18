"""
Pydantic request models for API endpoints.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


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
    """PATCH /api/v1/conversations/{conversation_id}

    Rename (``title_ar``) and star toggle (``starred``) share the same PATCH.
    At least one of the two fields must be supplied.
    """
    title_ar: Optional[str] = Field(None, min_length=1, max_length=500)
    starred: Optional[bool] = None

    @field_validator("title_ar", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def at_least_one_field(self) -> "UpdateConversationRequest":
        if self.title_ar is None and self.starred is None:
            raise ValueError("يجب تحديد العنوان أو حالة التمييز")
        return self


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


# ── Templates (قوالبي — per-user markdown templates) ────

class CreateTemplateRequest(BaseModel):
    """POST /api/v1/templates"""
    title: str = Field(..., min_length=1, max_length=500)
    content_md: str = Field(default="", max_length=200_000)

    @field_validator("title", "content_md", mode="before")
    @classmethod
    def check_template_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        # min_length=1 still admits whitespace-only — the title is mandatory.
        if not v.strip():
            raise ValueError("عنوان القالب مطلوب")
        return v.strip()


class UpdateTemplateRequest(BaseModel):
    """PATCH /api/v1/templates/{template_id}"""
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    content_md: Optional[str] = Field(default=None, max_length=200_000)

    @field_validator("title", "content_md", mode="before")
    @classmethod
    def check_template_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: Optional[str]) -> Optional[str]:
        # When provided, the title cannot be blanked out — it stays mandatory.
        if v is None:
            return v
        if not v.strip():
            raise ValueError("عنوان القالب مطلوب")
        return v.strip()


class IngestTemplateRequest(BaseModel):
    """POST /api/v1/templates/ingest

    Asks the ingester agent to clean ONE attached workspace item into a
    reusable template and save it to قوالبي. Only the item_id is supplied —
    ownership + provenance are set server-side.
    """
    item_id: str = Field(..., min_length=1, max_length=100)


# ── Plan activation codes ──────────────────────────────

class RedeemCodeRequest(BaseModel):
    """POST /api/v1/plans/redeem

    The user-typed activation code. Normalization (uppercase, strip separators)
    happens server-side in the redeem RPC, so we accept dashes/spaces/lowercase
    here. min_length is lenient (3) — anything shorter is obviously not a code
    and is rejected before it reaches the brute-force counter.
    """
    code: str = Field(..., min_length=3, max_length=64)

    @field_validator("code", mode="before")
    @classmethod
    def check_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v


# ── Resumable uploads (TUS) ─────────────────────────────

class UploadInitRequest(BaseModel):
    """POST /api/v1/cases/{case_id}/documents/init
       POST /api/v1/conversations/{conversation_id}/workspace/attachments/init

    Body the client sends BEFORE pushing bytes via TUS. The server reserves
    the storage path, creates a placeholder row, and returns the URL+expiry
    the browser uses for the resumable upload.
    """
    filename: str = Field(..., min_length=1, max_length=500)
    mime_type: str = Field(..., min_length=1, max_length=100)
    size_bytes: int = Field(..., gt=0, le=50 * 1024 * 1024)
    # Client-reported page count (PDF parsed in-browser; images → 1). Feeds the
    # OCR quota gate's upfront projection so a multi-page document is counted
    # before OCR runs. An ESTIMATE only — the post-OCR settle (real Mistral page
    # count) stays authoritative — so it is never trusted for billing. Clamped
    # server-side; None when the client couldn't determine it. Ignored by the
    # case-documents init flow.
    page_count: Optional[int] = Field(None, ge=1, le=10_000)

    @field_validator("filename", mode="before")
    @classmethod
    def check_filename_null_bytes(cls, v):
        return _reject_null_bytes(v) if isinstance(v, str) else v
