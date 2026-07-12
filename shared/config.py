"""
Centralized configuration for the Legal AI RAG application.
All environment variables are defined, validated, and documented here.
Uses Pydantic Settings for type-safe configuration.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    All config for backend, agents, and shared utilities.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore unknown env vars
    )

    # ========================================
    # APP SETTINGS
    # ========================================
    APP_NAME: str = "Legal AI RAG"
    APP_ENV: str = "development"     # development | staging | production
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"          # DEBUG | INFO | WARNING | ERROR
    PORT: int = 8000

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"  # Comma-separated origins

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ORIGINS into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    # Public-facing web (frontend) origin. Used to build absolute share URLs
    # (e.g. the مدونة / public share-by-link page at /blog/{token}). Defaults
    # to the prod frontend; override to http://localhost:3000 for local dev.
    # No trailing slash (the validator strips it).
    PUBLIC_WEB_URL: str = "https://rayhanai.com"

    @field_validator("PUBLIC_WEB_URL")
    @classmethod
    def validate_public_web_url(cls, v: str) -> str:
        return (v or "").rstrip("/")

    # ========================================
    # SUPABASE
    # ========================================
    SUPABASE_URL: str                        # https://xxx.supabase.co
    SUPABASE_ANON_KEY: str                   # Public anon key
    SUPABASE_SERVICE_KEY: str                # Service role key (secret!)
    SUPABASE_JWT_SECRET: str                 # JWT secret for token verification
    SUPABASE_DB_URL: Optional[str] = None    # Direct Postgres URL (for migrations)

    @field_validator("SUPABASE_URL")
    @classmethod
    def validate_supabase_url(cls, v: str) -> str:
        if not v.startswith("https://") and not v.startswith("http://localhost"):
            raise ValueError("SUPABASE_URL must start with https:// or http://localhost")
        return v.rstrip("/")

    # ========================================
    # REDIS / UPSTASH
    # ========================================
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: Optional[str] = None

    # ========================================
    # AI / LLM PROVIDERS
    # ========================================

    # OpenRouter (primary LLM gateway)
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_DEFAULT_MODEL: str = "anthropic/claude-sonnet-4"

    # Mistral (document extraction)
    MISTRAL_API_KEY: Optional[str] = None
    MISTRAL_MODEL: str = "pixtral-large-latest"
    MISTRAL_OCR_MODEL: str = "mistral-ocr-latest"

    # OpenAI (embeddings + agents)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536

    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None

    # Google (Gemini)
    GOOGLE_API_KEY: Optional[str] = None

    # Jina Reranker
    JINA_RERANKER_API_KEY: Optional[str] = None

    # DeepSeek
    DEEPSEEK_API_KEY: Optional[str] = None

    # MiniMax
    MINIMAX_API_KEY: Optional[str] = None

    # Alibaba DashScope — EMBEDDINGS ONLY (text-embedding-v4). The chat/LLM
    # agents moved to the ALIBABA_*_GLOBAL pair below; this key/base now serve
    # only agents/utils/embeddings.py.
    ALIBABA_API_KEY: Optional[str] = None
    ALIBABA_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    ALIBABA_EMBEDDING_MODEL: str = "text-embedding-v4"
    ALIBABA_EMBEDDING_DIMENSIONS: int = 1024

    # Alibaba "global" workspace key + dedicated MaaS endpoint (eu-central-1).
    # ALL chat/LLM agents (Qwen AND DeepSeek-on-Alibaba, via model_registry
    # create_model) use this pair. Embeddings stay on ALIBABA_API_KEY above.
    # When the global key is unset the chat path transparently falls back to the
    # embeddings pair. See agents/model_registry.py create_model() alibaba branch.
    ALIBABA_API_KEY_GLOBAL: Optional[str] = None
    ALIBABA_BASE_URL_GLOBAL: str = "https://ws-pz0iv9oq6gq1mhjy.eu-central-1.maas.aliyuncs.com/compatible-mode/v1"

    # ========================================
    # AGENT FRAMEWORK
    # ========================================
    AGENT_AUTO_ROUTE_MODEL: str = "anthropic/claude-haiku-4-5-20251001"
    AGENT_DEFAULT_MODEL: str = "anthropic/claude-sonnet-4"

    # ========================================
    # FEATURE FLAGS
    # ========================================
    FEATURE_MEMORY_EXTRACTION: bool = True
    FEATURE_DOCUMENT_OCR: bool = True
    FEATURE_COST_TRACKING: bool = True
    FEATURE_AUDIT_LOGGING: bool = True
    FEATURE_RATE_LIMITING: bool = True

    # Global server-side kill-switch for identifier masking (وضع السرية). When
    # False, the codec encode path is a byte-identical passthrough regardless of
    # the per-user preference. Default True (privacy-by-default; the emergency
    # brake if decode misbehaves in prod). Env var name == field name (no
    # validation_alias), so set PRIVACY_MASKING_ENABLED=false to disable.
    PRIVACY_MASKING_ENABLED: bool = True

    # Overall pipeline timeout (seconds) for a single message turn. Bounds the
    # whole handle_message run inside pipeline_producer — even when the client
    # disconnects and the pipeline is detached to the background. 7 min = ~1.75×
    # the ~4-min worst-case legitimate path (OCR + memory + router + deep_search
    # + aggregator + publish). Env-overridable for tests.
    LUNA_PIPELINE_TIMEOUT_S: float = 420.0

    # ========================================
    # RATE LIMITING
    # ========================================
    RATE_LIMIT_MESSAGES_PER_MINUTE: int = 20
    RATE_LIMIT_UPLOADS_PER_HOUR: int = 50

    # ========================================
    # STORAGE
    # ========================================
    MAX_UPLOAD_SIZE_MB: int = 50
    STORAGE_BUCKET_DOCUMENTS: str = "documents"

    # ========================================
    # INTERNAL WEBHOOKS (Supabase trigger → backend)
    # ========================================
    # Shared secret that Postgres triggers attach as ``X-Webhook-Secret`` when
    # POSTing to internal endpoints (e.g. /internal/summarize-workspace-item).
    # MUST be configured both here and via ``ALTER DATABASE ... SET app.webhook_secret = ...``
    # on the Supabase side for the trigger to fire.
    INTERNAL_WEBHOOK_SECRET: Optional[str] = None

    # ========================================
    # EDITORIAL / BLOG-POST GENERATION API
    # ========================================
    # Internal blog-post-jobs API (marketing content generation). Fail-closed on
    # auth like the webhook secret above: if EDITORIAL_SERVICE_KEY is unset,
    # every call is rejected 401. All five are set as Railway env vars on the
    # backend service; the endpoint boots cleanly with them unset.
    EDITORIAL_SERVICE_KEY: Optional[str] = None   # Bearer key for the blog-post-jobs API (fail-closed: unset => all calls 401)
    EDITORIAL_BOT_USER_ID: Optional[str] = None   # public.users.user_id of the editorial bot that owns generated posts
    EDITORIAL_MAX_CONCURRENT_JOBS: int = 2        # in-flight generation cap (protects the single-worker backend)
    EDITORIAL_RATE_LIMIT_PER_HOUR: int = 100      # blog-post submissions per rolling hour
    EDITORIAL_RATE_LIMIT_PER_DAY: int = 300       # blog-post submissions per rolling day

    # ========================================
    # ENVIRONMENT
    # ========================================
    ENVIRONMENT: str = "development"  # Alias for APP_ENV used by Railway

    # ========================================
    # DERIVED PROPERTIES
    # ========================================

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production" or self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Call this instead of Settings() directly to avoid re-reading .env.
    """
    return Settings()
