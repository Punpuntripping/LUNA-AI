"""
Shared types and enums for the Legal AI RAG application.
These mirror the PostgreSQL enums in 002_enums.sql.
Used by both backend and agents.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional
from uuid import UUID
from dataclasses import dataclass, field
from datetime import datetime


# ============================================
# ENUMS (mirror PostgreSQL enum types)
# ============================================

class CaseType(str, Enum):
    """Case type — Saudi legal system categories."""
    REAL_ESTATE = "عقاري"
    COMMERCIAL = "تجاري"
    LABOR = "عمالي"
    CRIMINAL = "جنائي"
    PERSONAL_STATUS = "أحوال_شخصية"
    ADMINISTRATIVE = "إداري"
    ENFORCEMENT = "تنفيذ"
    GENERAL = "عام"


class CaseStatus(str, Enum):
    """Case lifecycle status."""
    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class CasePriority(str, Enum):
    """Case priority level."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DocumentType(str, Enum):
    """Document file type — mirrors document_type_enum in 002_enums.sql."""
    PDF = "pdf"
    IMAGE = "image"
    DOCX = "docx"
    OTHER = "other"


class MemoryType(str, Enum):
    """Type of case memory/fact."""
    FACT = "fact"
    DOCUMENT_REFERENCE = "document_reference"
    STRATEGY = "strategy"
    DEADLINE = "deadline"
    PARTY_INFO = "party_info"


class ExtractionStatus(str, Enum):
    """Document text extraction pipeline status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageRole(str, Enum):
    """Chat message sender role."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class FinishReason(str, Enum):
    """LLM generation stop reason."""
    STOP = "stop"
    LENGTH = "length"
    ERROR = "error"


class SubscriptionTier(str, Enum):
    """User subscription level."""
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    """Subscription payment status."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    TRIAL = "trial"


class AttachmentType(str, Enum):
    """Type of file attached to a message."""
    IMAGE = "image"
    PDF = "pdf"
    FILE = "file"


class FeedbackRating(str, Enum):
    """User feedback on AI responses."""
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"


class AuditAction(str, Enum):
    """Audit log action types."""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    UPLOAD = "upload"
    DOWNLOAD = "download"


class AgentFamily(str, Enum):
    """Agent family — mirrors agent_family_enum in 018_enums_agent.sql."""
    DEEP_SEARCH = "deep_search"
    SIMPLE_SEARCH = "simple_search"
    END_SERVICES = "end_services"
    EXTRACTION = "extraction"
    MEMORY = "memory"


class ArtifactType(str, Enum):
    """Artifact type — mirrors artifact_type_enum in 018_enums_agent.sql."""
    REPORT = "report"
    CONTRACT = "contract"
    MEMO = "memo"
    SUMMARY = "summary"
    MEMORY_FILE = "memory_file"
    LEGAL_OPINION = "legal_opinion"


# ============================================
# TYPE ALIASES
# ============================================

# Embedding vector (list of floats, 1536 dimensions for OpenAI)
EmbeddingVector = list[float]

# JSONB parties structure for lawyer_cases
PartiesDict = dict  # {"plaintiff": str, "defendant": str, "judge": str, "witnesses": list[str]}

# Extracted document data structure
ExtractedData = dict  # {"parties": [...], "dates": [...], "amounts": [...], "clauses": [...]}


# ============================================
# SHARED DATA CLASSES
# ============================================

@dataclass
class ChatMessage:
    """A single chat message (for session/context building)."""
    role: MessageRole
    content: str
    message_id: Optional[UUID] = None
    model: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class RetrievedContext:
    """A piece of context retrieved by the RAG pipeline."""
    content: str
    source_type: str          # 'article', 'memory', 'document'
    source_id: Optional[UUID] = None
    relevance_score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class LLMUsage:
    """Token usage and cost tracking for a single LLM call."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    finish_reason: FinishReason = FinishReason.STOP


@dataclass
class AgentContext:
    """Context passed to agent families for processing a user request."""
    question: str
    conversation_id: str
    user_id: str
    case_id: Optional[str] = None
    memory_md: Optional[str] = None
    conversation_history: list[ChatMessage] = field(default_factory=list)
    case_metadata: Optional[dict] = None
    user_preferences: Optional[dict] = None
    user_templates: Optional[list[dict]] = None
    document_summaries: Optional[list[dict]] = None
    modifiers: list[str] = field(default_factory=list)
