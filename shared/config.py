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
    OPENROUTER_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias="OPEN_ROUTER",
    )
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_DEFAULT_MODEL: str = "anthropic/claude-sonnet-4"

    # Mistral (document extraction)
    MISTRAL_API_KEY: Optional[str] = None
    MISTRAL_MODEL: str = "pixtral-large-latest"

    # OpenAI (embeddings + agents)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBEDDING_DIMENSIONS: int = 1536

    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None

    # Google (Gemini)
    GOOGLE_API_KEY: Optional[str] = None

    # Jina Reranker
    JINA_RERANKER_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias="JINA_RERANKER_API",
    )

    # DeepSeek
    DEEPSEEK_API_KEY: Optional[str] = None

    # MiniMax
    MINIMAX_API_KEY: Optional[str] = None

    # Alibaba DashScope (Qwen models + embeddings)
    ALIBABA_API_KEY: Optional[str] = None
    ALIBABA_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    ALIBABA_EMBEDDING_MODEL: str = "text-embedding-v4"
    ALIBABA_EMBEDDING_DIMENSIONS: int = 1024

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
