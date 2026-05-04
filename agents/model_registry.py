"""
Model Registry - Central configuration for all LLM models.

Add new models here and the system will auto-configure them.
No need to change code elsewhere - just update this registry.

Usage:
    from agents.model_registry import get_model_config, create_model

    # Get config for a model
    config = get_model_config("gpt-5.4-mini")

    # Create Pydantic AI model instance
    model = create_model("gpt-5.4-mini")
    model = create_model("claude-sonnet-4-6")
    model = create_model("gemini-2.5-flash")

    # Use with Agent
    from pydantic_ai import Agent
    agent = Agent(model)
"""

from typing import Dict, Any, Optional, Literal
from dataclasses import dataclass, field

from shared.config import get_settings

settings = get_settings()


# =============================================================================
# MODEL CONFIGURATION
# =============================================================================

@dataclass
class ModelConfig:
    """Configuration for a specific LLM model."""

    # Model identification
    model_id: str                    # Full model ID (e.g., "gpt-5.4-mini")
    provider: str                    # Provider name: openai, anthropic, google, etc.
    display_name: str                # Human-readable name

    # Capabilities
    supports_temperature: bool = True
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_json_mode: bool = True

    # Defaults
    default_temperature: float = 0.0
    max_tokens: int = 4096
    context_length: Optional[int] = None      # Total context window size

    # Pricing (USD per 1M tokens)
    input_price: Optional[float] = None    # Input cost per 1M tokens
    output_price: Optional[float] = None   # Output cost per 1M tokens
    cached_input_price: Optional[float] = None  # Cached/hit input cost per 1M tokens

    # Speed (measured via provider's own API, source: artificialanalysis.ai, March 2026)
    output_speed_tps: Optional[float] = None  # Output tokens per second (median P50)

    # Constraints
    temperature_range: tuple = (0.0, 2.0)
    fixed_temperature: Optional[float] = None  # If model only supports one value

    # Extra kwargs for the provider
    extra_kwargs: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# MODEL REGISTRY - ADD NEW MODELS HERE
# Last updated: 2026-03-22
# =============================================================================

MODEL_REGISTRY: Dict[str, ModelConfig] = {
    # =========================================================================
    # OPENAI MODELS
    # Docs: https://developers.openai.com/api/docs/models
    # =========================================================================

    # --- GPT-5.4 series (Latest flagship - March 2026) ---
    "gpt-5.4": ModelConfig(
        model_id="gpt-5.4",
        provider="openai",
        display_name="GPT-5.4",
        supports_vision=True,
        max_tokens=128000,
        input_price=2.50,
        output_price=15.00,
        cached_input_price=0.25,
        output_speed_tps=86.0,
    ),
    "gpt-5.4-mini": ModelConfig(
        model_id="gpt-5.4-mini",
        provider="openai",
        display_name="GPT-5.4 Mini",
        supports_vision=True,
        max_tokens=128000,
        input_price=0.75,
        output_price=4.50,
        cached_input_price=0.075,
        output_speed_tps=220.0,
    ),
    "gpt-5.4-nano": ModelConfig(
        model_id="gpt-5.4-nano",
        provider="openai",
        display_name="GPT-5.4 Nano",
        supports_vision=True,
        max_tokens=128000,
        input_price=0.20,
        output_price=1.25,
        cached_input_price=0.02,
        output_speed_tps=205.0,
    ),
    "gpt-5.4-pro": ModelConfig(
        model_id="gpt-5.4-pro",
        provider="openai",
        display_name="GPT-5.4 Pro",
        supports_vision=True,
        supports_temperature=False,
        max_tokens=128000,
        input_price=30.00,
        output_price=180.00,
        output_speed_tps=82.0,
    ),

    # --- GPT-5.2 series (December 2025) ---
    "gpt-5.2": ModelConfig(
        model_id="gpt-5.2",
        provider="openai",
        display_name="GPT-5.2",
        supports_vision=True,
        max_tokens=128000,
        input_price=0.875,
        output_price=7.00,
        cached_input_price=0.175,
        output_speed_tps=81.0,
    ),
    "gpt-5.2-pro": ModelConfig(
        model_id="gpt-5.2-pro",
        provider="openai",
        display_name="GPT-5.2 Pro",
        supports_vision=True,
        supports_temperature=False,
        max_tokens=128000,
        input_price=10.50,
        output_price=84.00,
        output_speed_tps=63.0,
    ),

    # --- GPT-5.1 series (November 2025) ---
    "gpt-5.1": ModelConfig(
        model_id="gpt-5.1",
        provider="openai",
        display_name="GPT-5.1",
        supports_vision=True,
        max_tokens=128000,
        input_price=0.625,
        output_price=5.00,
        cached_input_price=0.125,
        output_speed_tps=65.0,
    ),

    # --- GPT-5 series (August 2025) ---
    "gpt-5": ModelConfig(
        model_id="gpt-5",
        provider="openai",
        display_name="GPT-5",
        supports_vision=True,
        max_tokens=128000,
        input_price=0.625,
        output_price=5.00,
        cached_input_price=0.125,
        output_speed_tps=64.0,
    ),
    "gpt-5-pro": ModelConfig(
        model_id="gpt-5-pro",
        provider="openai",
        display_name="GPT-5 Pro",
        supports_vision=True,
        supports_temperature=False,
        max_tokens=128000,
        input_price=15.00,
        output_price=120.00,
        output_speed_tps=58.0,
    ),

    # --- o-series (Reasoning models) - NO temperature support ---
    # Temperature must be omitted entirely for these models.
    "o3": ModelConfig(
        model_id="o3",
        provider="openai",
        display_name="o3",
        supports_temperature=False,
        supports_vision=True,
        max_tokens=100000,
        input_price=2.00,
        output_price=8.00,
        cached_input_price=0.50,
        output_speed_tps=55.0,
    ),
    "o4-mini": ModelConfig(
        model_id="o4-mini",
        provider="openai",
        display_name="o4 Mini",
        supports_temperature=False,
        supports_vision=True,
        max_tokens=100000,
        input_price=1.10,
        output_price=4.40,
        cached_input_price=0.275,
        output_speed_tps=123.0,
    ),
    "o3-mini": ModelConfig(
        model_id="o3-mini",
        provider="openai",
        display_name="o3 Mini",
        supports_temperature=False,
        max_tokens=100000,
        input_price=1.10,
        output_price=4.40,
        cached_input_price=0.55,
        output_speed_tps=152.0,
    ),

    # =========================================================================
    # ANTHROPIC MODELS
    # Docs: https://platform.claude.com/docs/en/about-claude/models/overview
    # =========================================================================

    # --- Claude 4.6 series (Latest - February 2026) ---
    "claude-opus-4-6": ModelConfig(
        model_id="claude-opus-4-6",
        provider="anthropic",
        display_name="Claude Opus 4.6",
        max_tokens=128000,
        supports_vision=True,
        input_price=5.00,
        output_price=25.00,
        cached_input_price=0.50,
        output_speed_tps=54.0,
    ),
    "claude-sonnet-4-6": ModelConfig(
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        display_name="Claude Sonnet 4.6",
        max_tokens=64000,
        supports_vision=True,
        input_price=3.00,
        output_price=15.00,
        cached_input_price=0.30,
        output_speed_tps=63.0,
    ),

    # --- Claude 4.5 series (September-November 2025) ---
    "claude-opus-4-5": ModelConfig(
        model_id="claude-opus-4-5-20251101",
        provider="anthropic",
        display_name="Claude Opus 4.5",
        max_tokens=64000,
        supports_vision=True,
        input_price=5.00,
        output_price=25.00,
        cached_input_price=0.50,
        output_speed_tps=52.0,
    ),
    "claude-sonnet-4-5": ModelConfig(
        model_id="claude-sonnet-4-5-20250929",
        provider="anthropic",
        display_name="Claude Sonnet 4.5",
        max_tokens=64000,
        supports_vision=True,
        input_price=3.00,
        output_price=15.00,
        cached_input_price=0.30,
        output_speed_tps=54.0,
    ),
    "claude-haiku-4-5": ModelConfig(
        model_id="claude-haiku-4-5-20251001",
        provider="anthropic",
        display_name="Claude Haiku 4.5",
        max_tokens=64000,
        supports_vision=True,
        input_price=1.00,
        output_price=5.00,
        cached_input_price=0.10,
        output_speed_tps=106.0,
    ),

    # --- Claude 4.1 (August 2025) ---
    "claude-opus-4-1": ModelConfig(
        model_id="claude-opus-4-1-20250805",
        provider="anthropic",
        display_name="Claude Opus 4.1",
        max_tokens=32000,
        supports_vision=True,
        input_price=15.00,
        output_price=75.00,
        cached_input_price=1.50,
        output_speed_tps=52.0,
    ),

    # --- Claude 4 series (May 2025) ---
    "claude-sonnet-4": ModelConfig(
        model_id="claude-sonnet-4-20250514",
        provider="anthropic",
        display_name="Claude Sonnet 4",
        max_tokens=64000,
        supports_vision=True,
        input_price=3.00,
        output_price=15.00,
        cached_input_price=0.30,
        output_speed_tps=54.0,
    ),
    "claude-opus-4": ModelConfig(
        model_id="claude-opus-4-20250514",
        provider="anthropic",
        display_name="Claude Opus 4",
        max_tokens=32000,
        supports_vision=True,
        input_price=15.00,
        output_price=75.00,
        cached_input_price=1.50,
        output_speed_tps=54.0,
    ),

    # =========================================================================
    # GOOGLE MODELS
    # Docs: https://ai.google.dev/gemini-api/docs/models
    # =========================================================================

    # --- Gemini 3 series (Latest - Preview) ---
    "gemini-3.1-pro": ModelConfig(
        model_id="gemini-3.1-pro-preview",
        provider="google",
        display_name="Gemini 3.1 Pro",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=56000,
        input_price=2.00,
        output_price=12.00,
        output_speed_tps=120.0,
    ),
    "gemini-3-flash": ModelConfig(
        model_id="gemini-3-flash-preview",
        provider="google",
        display_name="Gemini 3 Flash",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=56000,
        input_price=0.50,
        output_price=3.00,
        output_speed_tps=218.0,
    ),
    "gemini-3.1-flash-lite": ModelConfig(
        model_id="gemini-3.1-flash-lite-preview",
        provider="google",
        display_name="Gemini 3.1 Flash Lite",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=56000,
        input_price=0.25,
        output_price=1.50,
        output_speed_tps=382.0,
    ),

    # --- Gemini Deep Research (Long-running agent, minutes per query) ---
    "gemini-deep-research": ModelConfig(
        model_id="deep-research-pro-preview-12-2025",
        provider="google",
        display_name="Gemini Deep Research",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=65536,
        input_price=2.00,   # Charged at Gemini 3 Pro rates
        output_price=12.00,
        output_speed_tps=None,  # Long-running agent, not measured in t/s
    ),

    # --- Gemini 2.5 series (Stable) ---
    "gemini-2.5-pro": ModelConfig(
        model_id="gemini-2.5-pro",
        provider="google",
        display_name="Gemini 2.5 Pro",
        supports_vision=True,
        max_tokens=65536,
        input_price=1.25,
        output_price=10.00,
        output_speed_tps=133.0,
    ),
    "gemini-2.5-flash": ModelConfig(
        model_id="gemini-2.5-flash",
        provider="google",
        display_name="Gemini 2.5 Flash",
        supports_vision=True,
        max_tokens=65536,
        input_price=0.30,
        output_price=2.50,
        output_speed_tps=216.0,
    ),
    "gemini-2.5-flash-lite": ModelConfig(
        model_id="gemini-2.5-flash-lite",
        provider="google",
        display_name="Gemini 2.5 Flash Lite",
        supports_vision=True,
        max_tokens=65536,
        input_price=0.10,
        output_price=0.40,
        output_speed_tps=393.0,
    ),

    # =========================================================================
    # DEEPSEEK MODELS
    # Docs: https://api-docs.deepseek.com/quick_start/pricing
    # Note: DeepSeek uses OpenAI-compatible API (base_url: https://api.deepseek.com)
    # Both models run DeepSeek-V3.2 (128K context)
    # =========================================================================

    "deepseek-chat": ModelConfig(
        model_id="deepseek-chat",
        provider="deepseek",
        display_name="DeepSeek Chat (V3.2)",
        max_tokens=8192,
        supports_vision=False,
        input_price=0.28,
        output_price=0.42,
        cached_input_price=0.028,
        output_speed_tps=80.0,
    ),
    "deepseek-reasoner": ModelConfig(
        model_id="deepseek-reasoner",
        provider="deepseek",
        display_name="DeepSeek Reasoner (V3.2)",
        max_tokens=64000,
        supports_vision=False,
        supports_temperature=False,
        input_price=0.28,
        output_price=0.42,
        cached_input_price=0.028,
        output_speed_tps=34.0,
    ),

    # =========================================================================
    # MINIMAX MODELS
    # Docs: https://platform.minimax.io/docs/guides/models-intro
    # OpenAI-compatible API (base_url: https://api.minimax.io/v1)
    # Temperature range: (0.0, 1.0] — values outside return error
    # =========================================================================

    # --- OpenRouter models ---
    "or-minimax-m2.7": ModelConfig(
        model_id="minimax/minimax-m2.7",
        provider="openrouter",
        display_name="MiniMax M2.7 (OpenRouter)",
        supports_vision=True,
        supports_tools=True,
        max_tokens=131072,
        context_length=204800,
        temperature_range=(0.0, 1.0),
        default_temperature=1.0,
        input_price=0.30,
        output_price=1.20,
        output_speed_tps=48.0,
        extra_kwargs={"no_tool_choice_required": True},  # only supports auto/none
    ),
    # Note: OpenRouter only has minimax/minimax-m2.7 (no highspeed/fp8 variants).
    # For direct MiniMax API highspeed, use "minimax-m2.7-highspeed" key above.
    "or-gemini-3.1-pro": ModelConfig(
        model_id="google/gemini-3.1-pro-preview",
        provider="openrouter",
        display_name="Gemini 3.1 Pro Preview (OpenRouter)",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=56000,
        context_length=1048576,
        input_price=2.00,
        output_price=12.00,
        output_speed_tps=120.0,
    ),
    "or-gemini-3.1-pro-tools": ModelConfig(
        model_id="google/gemini-3.1-pro-preview-customtools",
        provider="openrouter",
        display_name="Gemini 3.1 Pro Preview Custom Tools (OpenRouter)",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=56000,
        context_length=1048576,
        input_price=2.00,
        output_price=12.00,
        output_speed_tps=120.0,
    ),
    "or-gemini-2.5-pro": ModelConfig(
        model_id="google/gemini-2.5-pro",
        provider="openrouter",
        display_name="Gemini 2.5 Pro (OpenRouter)",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=65536,
        context_length=1048576,
        input_price=1.25,
        output_price=10.00,
        output_speed_tps=133.0,
    ),
    "or-gemini-2.5-flash": ModelConfig(
        model_id="google/gemini-2.5-flash",
        provider="openrouter",
        display_name="Gemini 2.5 Flash (OpenRouter)",
        supports_vision=True,
        default_temperature=1.0,
        max_tokens=65536,
        context_length=1048576,
        input_price=0.30,
        output_price=2.50,
        output_speed_tps=216.0,
    ),
    "or-deepseek-chat": ModelConfig(
        model_id="deepseek/deepseek-chat-v3-0324",
        provider="openrouter",
        display_name="DeepSeek Chat V3 (OpenRouter)",
        max_tokens=8192,
        context_length=163840,
        input_price=0.28,
        output_price=0.42,
        output_speed_tps=80.0,
    ),
    "or-qwen3.5-397b": ModelConfig(
        model_id="qwen/qwen3.5-397b-a17b",
        provider="openrouter",
        display_name="Qwen 3.5 397B A17B (OpenRouter)",
        supports_vision=True,
        max_tokens=65536,
        context_length=262144,
        input_price=0.39,
        output_price=2.34,
    ),
    "or-deepseek-v3.2": ModelConfig(
        model_id="deepseek/deepseek-v3.2",
        provider="openrouter",
        display_name="DeepSeek V3.2 (OpenRouter)",
        supports_vision=False,
        max_tokens=8192,
        context_length=163840,
        input_price=0.26,
        output_price=0.38,
    ),
    "or-mimo-v2-pro": ModelConfig(
        model_id="xiaomi/mimo-v2-pro",
        provider="openrouter",
        display_name="MiMo V2 Pro (OpenRouter)",
        supports_vision=False,
        max_tokens=131072,
        context_length=1048576,
        input_price=1.00,
        output_price=3.00,
    ),
    "or-glm-5-turbo": ModelConfig(
        model_id="z-ai/glm-5-turbo",
        provider="openrouter",
        display_name="GLM-5 Turbo (OpenRouter)",
        supports_vision=False,
        max_tokens=131072,
        context_length=202752,
        input_price=1.20,
        output_price=4.00,
    ),
    "or-gemma-4-31b": ModelConfig(
        model_id="google/gemma-4-31b-it",
        provider="openrouter",
        display_name="Gemma 4 31B IT (OpenRouter)",
        supports_vision=False,
        supports_tools=True,
        max_tokens=131072,
        context_length=262144,
        input_price=0.15,
        output_price=0.30,
    ),

    # --- MiniMax M2.7 (Latest - March 2026) ---
    "minimax-m2.7": ModelConfig(
        model_id="MiniMax-M2.7",
        provider="minimax",
        display_name="MiniMax M2.7",
        supports_vision=True,
        max_tokens=65536,
        temperature_range=(0.0, 1.0),
        default_temperature=1.0,
        input_price=0.30,
        output_price=1.20,
        output_speed_tps=48.0,
    ),
    "minimax-m2.7-highspeed": ModelConfig(
        model_id="MiniMax-M2.7-highspeed",
        provider="minimax",
        display_name="MiniMax M2.7 Highspeed",
        supports_vision=True,
        max_tokens=65536,
        temperature_range=(0.0, 1.0),
        default_temperature=1.0,
        input_price=0.30,
        output_price=1.20,
        output_speed_tps=72.0,
    ),

    # --- MiniMax M2.5 (February 2026) ---
    "minimax-m2.5": ModelConfig(
        model_id="MiniMax-M2.5",
        provider="minimax",
        display_name="MiniMax M2.5",
        supports_vision=True,
        max_tokens=65536,
        temperature_range=(0.0, 1.0),
        default_temperature=1.0,
        input_price=0.30,
        output_price=1.20,
        output_speed_tps=45.0,
    ),
    "minimax-m2.5-highspeed": ModelConfig(
        model_id="MiniMax-M2.5-highspeed",
        provider="minimax",
        display_name="MiniMax M2.5 Highspeed",
        supports_vision=True,
        max_tokens=65536,
        temperature_range=(0.0, 1.0),
        default_temperature=1.0,
        input_price=0.30,
        output_price=1.20,
        output_speed_tps=68.0,
    ),

    # =========================================================================
    # ALIBABA (QWEN) MODELS
    # Docs: https://www.alibabacloud.com/help/en/model-studio/models
    # OpenAI-compatible API (base_url: https://dashscope-intl.aliyuncs.com/compatible-mode/v1)
    # Pricing: International region, base tier (USD per 1M tokens)
    # =========================================================================

    # --- Qwen3.6 series (Latest - April 2026) ---
    "qwen3.6-plus": ModelConfig(
        model_id="qwen3.6-plus",
        provider="alibaba",
        display_name="Qwen3.6 Plus",
        supports_vision=True,
        max_tokens=65536,
        context_length=1000000,
        input_price=0.57,
        output_price=3.44,
    ),

    # --- Qwen3.5 series (February 2026) ---
    "qwen3.5-plus": ModelConfig(
        model_id="qwen3.5-plus",
        provider="alibaba",
        display_name="Qwen3.5 Plus",
        supports_vision=True,
        max_tokens=65536,
        context_length=1000000,
        input_price=0.57,
        output_price=3.44,
        output_speed_tps=58.0,
    ),
    "qwen3.5-flash": ModelConfig(
        model_id="qwen3.5-flash",
        provider="alibaba",
        display_name="Qwen3.5 Flash",
        supports_vision=True,
        max_tokens=65536,
        context_length=1000000,
        input_price=0.17,
        output_price=1.72,
        output_speed_tps=120.0,
    ),
    # Alias used by the v4 planner default (V4_PLANNER_DESIGN.md §4.2). Points
    # at the same Alibaba flash model as ``qwen3.5-flash``.
    "qwen3-flash": ModelConfig(
        model_id="qwen3.5-flash",
        provider="alibaba",
        display_name="Qwen3 Flash (alias of qwen3.5-flash)",
        supports_vision=True,
        max_tokens=65536,
        context_length=1000000,
        input_price=0.17,
        output_price=1.72,
        output_speed_tps=120.0,
    ),

    # --- Qwen3 series ---
    "qwen3-max": ModelConfig(
        model_id="qwen3-max",
        provider="alibaba",
        display_name="Qwen3 Max",
        supports_vision=True,
        max_tokens=65536,
        context_length=262144,
        input_price=1.00,
        output_price=4.01,
        output_speed_tps=32.0,
    ),
    "qwen3-coder-plus": ModelConfig(
        model_id="qwen3-coder-plus",
        provider="alibaba",
        display_name="Qwen3 Coder Plus",
        supports_vision=False,
        max_tokens=65536,
        context_length=1000000,
        input_price=1.00,
        output_price=5.00,
        output_speed_tps=64.0,
    ),
    "qwen3-coder-flash": ModelConfig(
        model_id="qwen3-coder-flash",
        provider="alibaba",
        display_name="Qwen3 Coder Flash",
        supports_vision=False,
        max_tokens=65536,
        context_length=1000000,
        input_price=0.30,
        output_price=1.50,
        output_speed_tps=110.0,
    ),

    # --- Qwen3 Vision ---
    "qwen3-vl-plus": ModelConfig(
        model_id="qwen3-vl-plus",
        provider="alibaba",
        display_name="Qwen3 VL Plus",
        supports_vision=True,
        max_tokens=32768,
        context_length=262144,
        input_price=0.43,
        output_price=4.30,
    ),
    "qwen3-vl-flash": ModelConfig(
        model_id="qwen3-vl-flash",
        provider="alibaba",
        display_name="Qwen3 VL Flash",
        supports_vision=True,
        max_tokens=32768,
        context_length=262144,
        input_price=0.086,
        output_price=0.86,
    ),

    # --- Qwen reasoning ---
    "qwq-plus": ModelConfig(
        model_id="qwq-plus",
        provider="alibaba",
        display_name="QwQ Plus (Reasoning)",
        supports_vision=False,
        supports_temperature=False,
        max_tokens=8192,
        context_length=131072,
        input_price=0.80,
        output_price=2.40,
    ),
    "qvq-max": ModelConfig(
        model_id="qvq-max",
        provider="alibaba",
        display_name="QVQ Max (Vision Reasoning)",
        supports_vision=True,
        supports_temperature=False,
        max_tokens=8192,
        context_length=131072,
        input_price=1.20,
        output_price=4.80,
    ),

    # --- Qwen legacy / utility ---
    "qwen-plus": ModelConfig(
        model_id="qwen-plus",
        provider="alibaba",
        display_name="Qwen Plus",
        supports_vision=False,
        max_tokens=32768,
        context_length=1000000,
        input_price=0.69,
        output_price=6.88,
    ),
    "qwen-long": ModelConfig(
        model_id="qwen-long",
        provider="alibaba",
        display_name="Qwen Long (10M context)",
        supports_vision=False,
        max_tokens=32768,
        context_length=10000000,
        input_price=0.072,
        output_price=0.287,
    ),
    "qwen-vl-ocr": ModelConfig(
        model_id="qwen-vl-ocr",
        provider="alibaba",
        display_name="Qwen VL OCR",
        supports_vision=True,
        max_tokens=8192,
        context_length=38192,
        input_price=0.07,
        output_price=0.16,
    ),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_model_config(model_name: str) -> ModelConfig:
    """
    Get configuration for a model by name or ID.

    Args:
        model_name: Short name (e.g., "gpt-5.4-mini") or full ID

    Returns:
        ModelConfig for the model

    Raises:
        ValueError: If model not found in registry
    """
    # Try direct lookup first
    if model_name in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_name]

    # Try to find by model_id
    for config in MODEL_REGISTRY.values():
        if config.model_id == model_name:
            return config

    # Try partial match (for versioned model IDs)
    for name, config in MODEL_REGISTRY.items():
        if model_name.startswith(name) or config.model_id.startswith(model_name):
            return config

    raise ValueError(
        f"Model '{model_name}' not found in registry. "
        f"Available models: {list(MODEL_REGISTRY.keys())}"
    )


def get_api_key(provider: str) -> str:
    """Get API key for a provider from settings."""
    key_map = {
        "openai": settings.OPENAI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "google": getattr(settings, "GOOGLE_API_KEY", ""),
        "deepseek": getattr(settings, "DEEPSEEK_API_KEY", ""),
        "minimax": getattr(settings, "MINIMAX_API_KEY", ""),
        "alibaba": getattr(settings, "ALIBABA_API_KEY", ""),
        "openrouter": getattr(settings, "OPENROUTER_API_KEY", ""),
    }
    return key_map.get(provider, "")


def create_model(model_name: str):
    """
    Create a Pydantic AI model instance with auto-configuration.

    Args:
        model_name: Model name from registry (e.g., "gpt-5.4-mini", "claude-sonnet-4-6")

    Returns:
        Pydantic AI Model instance (OpenAIChatModel, AnthropicModel, GoogleModel)

    Example:
        from pydantic_ai import Agent

        model = create_model("gpt-5.4-mini")
        agent = Agent(model)

        model = create_model("claude-sonnet-4-6")
        agent = Agent(model)

        model = create_model("deepseek-chat")
        agent = Agent(model)
    """
    config = get_model_config(model_name)
    api_key = get_api_key(config.provider)

    if not api_key:
        raise ValueError(f"API key not configured for provider: {config.provider}")

    if config.provider == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(
            config.model_id,
            provider=OpenAIProvider(api_key=api_key),
        )

    elif config.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        return AnthropicModel(
            config.model_id,
            provider=AnthropicProvider(api_key=api_key),
        )

    elif config.provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        return GoogleModel(
            config.model_id,
            provider=GoogleProvider(api_key=api_key),
        )

    elif config.provider == "deepseek":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.deepseek import DeepSeekProvider
        return OpenAIChatModel(
            config.model_id,
            provider=DeepSeekProvider(api_key=api_key),
        )

    elif config.provider == "minimax":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(
            config.model_id,
            provider=OpenAIProvider(
                base_url="https://api.minimax.io/v1",
                api_key=api_key,
            ),
        )

    elif config.provider == "openrouter":
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        kwargs: Dict[str, Any] = {
            "provider": OpenRouterProvider(api_key=api_key),
        }

        # Models that don't support tool_choice="required" (only auto/none)
        if config.extra_kwargs.get("no_tool_choice_required"):
            from pydantic_ai.models.openai import OpenAIModelProfile
            kwargs["profile"] = OpenAIModelProfile(
                openai_supports_tool_choice_required=False,
            )

        return OpenRouterModel(config.model_id, **kwargs)

    elif config.provider == "alibaba":
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(
            config.model_id,
            provider=OpenAIProvider(
                base_url=settings.ALIBABA_BASE_URL or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                api_key=api_key,
            ),
            profile=OpenAIModelProfile(
                openai_supports_tool_choice_required=False,
            ),
        )

    else:
        raise ValueError(f"Unknown provider: {config.provider}")


def get_model_settings(model_name: str, temperature: Optional[float] = None):
    """
    Build Pydantic AI ModelSettings for a model, respecting its constraints.

    Args:
        model_name: Model name from registry
        temperature: Override temperature (ignored if model doesn't support it)

    Returns:
        ModelSettings dict suitable for Agent(model_settings=...) or agent.run(model_settings=...)
    """
    from pydantic_ai import ModelSettings

    config = get_model_config(model_name)
    settings_kwargs: Dict[str, Any] = {}

    if config.supports_temperature:
        temp = temperature if temperature is not None else config.default_temperature
        temp = max(config.temperature_range[0], min(temp, config.temperature_range[1]))
        settings_kwargs["temperature"] = temp
    elif config.fixed_temperature is not None:
        settings_kwargs["temperature"] = config.fixed_temperature

    settings_kwargs["max_tokens"] = config.max_tokens

    return ModelSettings(**settings_kwargs)


def list_models(provider: Optional[str] = None) -> Dict[str, ModelConfig]:
    """List all available models, optionally filtered by provider."""
    if provider:
        return {k: v for k, v in MODEL_REGISTRY.items() if v.provider == provider}
    return MODEL_REGISTRY


def get_default_model() -> str:
    """Get the default model from settings."""
    # Check if current model is in registry
    current = settings.OPENAI_MODEL
    try:
        get_model_config(current)
        return current
    except ValueError:
        # Fallback to gpt-5.4-mini
        return "gpt-5.4-mini"
