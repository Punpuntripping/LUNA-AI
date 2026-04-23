"""Async embedding utility for query-time vector generation."""
from __future__ import annotations

from openai import AsyncOpenAI
from shared.config import get_settings

# ── OpenAI embeddings (1536-dim, for app-side / planner) ─────────────────────

_client: AsyncOpenAI | None = None

MODEL = "text-embedding-3-small"
DIMENSIONS = 1536


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=get_settings().OPENAI_API_KEY)
    return _client


async def embed_text(text: str) -> list[float]:
    client = _get_client()
    response = await client.embeddings.create(
        input=text, model=MODEL, dimensions=DIMENSIONS
    )
    return response.data[0].embedding


async def embed_texts(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    response = await client.embeddings.create(
        input=texts, model=MODEL, dimensions=DIMENSIONS
    )
    return [item.embedding for item in response.data]


# ── Alibaba DashScope embeddings (1024-dim, DEFAULT for regulation executor) ──
#
# Uses text-embedding-v4 via DashScope's OpenAI-compatible endpoint.
# International endpoint: dashscope-intl.aliyuncs.com/compatible-mode/v1
# Supports dimensions: 1024 (default), 512, 256, 128, 64.
# To switch provider, change the alias at the bottom of this file.

ALIBABA_EMBEDDING_MODEL = "text-embedding-v4"
ALIBABA_EMBEDDING_DIMS = 1024

_alibaba_client: AsyncOpenAI | None = None


def _get_alibaba_client() -> AsyncOpenAI:
    global _alibaba_client
    if _alibaba_client is None:
        settings = get_settings()
        if not settings.ALIBABA_API_KEY:
            raise RuntimeError("ALIBABA_API_KEY not set")
        _alibaba_client = AsyncOpenAI(
            api_key=settings.ALIBABA_API_KEY,
            base_url=settings.ALIBABA_BASE_URL,
        )
    return _alibaba_client


async def embed_regulation_query_alibaba(text: str) -> list[float]:
    """Generate 1024-dim embedding using Alibaba text-embedding-v4 via DashScope."""
    client = _get_alibaba_client()
    response = await client.embeddings.create(
        input=text,
        model=ALIBABA_EMBEDDING_MODEL,
        dimensions=ALIBABA_EMBEDDING_DIMS,
    )
    return response.data[0].embedding


async def embed_regulation_queries_alibaba(texts: list[str]) -> list[list[float]]:
    """Batch embed multiple texts using Alibaba text-embedding-v4 (max 25 per call)."""
    client = _get_alibaba_client()
    response = await client.embeddings.create(
        input=texts,
        model=ALIBABA_EMBEDDING_MODEL,
        dimensions=ALIBABA_EMBEDDING_DIMS,
    )
    return [item.embedding for item in response.data]


# ── Qwen3 embeddings (1024-dim, backup via OpenRouter) ────────────────────────
#
# Uses Qwen3-Embedding-4B via OpenRouter (OpenAI-compatible API).
# Kept as a drop-in backup — same 1024 dimensions as Alibaba provider.

QWEN3_EMBEDDING_MODEL = "qwen/qwen3-embedding-4b"
QWEN3_EMBEDDING_DIMS = 1024

_qwen3_client: AsyncOpenAI | None = None


def _get_qwen3_client() -> AsyncOpenAI:
    global _qwen3_client
    if _qwen3_client is None:
        settings = get_settings()
        _qwen3_client = AsyncOpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
        )
    return _qwen3_client


async def embed_regulation_query_qwen3(text: str) -> list[float]:
    """Generate 1024-dim embedding using Qwen3-Embedding-4B via OpenRouter (backup)."""
    client = _get_qwen3_client()
    response = await client.embeddings.create(
        input=text, model=QWEN3_EMBEDDING_MODEL,
        dimensions=QWEN3_EMBEDDING_DIMS,
    )
    return response.data[0].embedding


# ── Gemini embeddings (768-dim, legacy — kept for fallback) ───────────────────
#
# Matches the ingestion pipeline at agentic_for_ministry/ingestion/embedding.py
# which uses the same REST endpoint, model, and outputDimensionality=768.

import httpx as _httpx

GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
GEMINI_EMBEDDING_DIMS = 768

_gemini_embed_client: _httpx.AsyncClient | None = None


def _get_gemini_embed_client() -> _httpx.AsyncClient:
    global _gemini_embed_client
    if _gemini_embed_client is None:
        _gemini_embed_client = _httpx.AsyncClient(timeout=30.0)
    return _gemini_embed_client


async def embed_regulation_query_gemini(text: str) -> list[float]:
    """Generate 768-dim embedding using Gemini REST API for regulation search."""
    api_key = get_settings().GOOGLE_API_KEY
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_EMBEDDING_MODEL}:embedContent?key={api_key}"
    )
    payload = {
        "model": f"models/{GEMINI_EMBEDDING_MODEL}",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": GEMINI_EMBEDDING_DIMS,
    }

    client = _get_gemini_embed_client()
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data["embedding"]["values"]


# ── Default regulation embedding (swap alias to change provider) ──────────────
# Priority: alibaba (default) → qwen3 (backup) → gemini (legacy)
embed_regulation_query = embed_regulation_query_alibaba
