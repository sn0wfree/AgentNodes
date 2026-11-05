"""LLM client and configuration for strategy-research.

Public API:
    LLMConfig           - immutable config dataclass with 4-layer merge
    OpenAICompatClient  - OpenAI/DeepSeek/Kimi/Qwen unified client
    LLMResponse         - parsed non-streaming response
    StreamChunk         - one streaming chunk
    ToolCall            - one tool invocation from LLM
    Errors              - LLMAuthError / LLMRateLimitError / LLMQuotaError
                          / LLMTimeoutError / LLMServerError
                          / LLMMalformedResponseError / LLMConfigError

Layered configuration (higher overrides lower):
    1. Code defaults        (LLMConfig field defaults)
    2. YAML profile file    (~/.quantnodes-research/llm.yaml)
    3. Environment variables (OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
                             STRATEGY_RESEARCH_LLM_PROFILE)
    4. CLI overrides        (parsed argparse namespace)

Typical usage:

    from agentnodes.foundation.llm import LLMConfig, OpenAICompatClient

    cfg = LLMConfig.load(profile="deepseek")
    client = OpenAICompatClient(cfg)
    resp = client.chat([{"role": "user", "content": "hi"}])
    print(resp.content)
"""

from __future__ import annotations

from .config import LLMConfig
from .errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMMalformedResponseError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)
from .openai_client import OpenAICompatClient
from .parser import LLMResponse, StreamChunk, ToolCall

__all__ = [
    "LLMConfig",
    "OpenAICompatClient",
    "LLMResponse",
    "StreamChunk",
    "ToolCall",
    "LLMError",
    "LLMAuthError",
    "LLMConfigError",
    "LLMMalformedResponseError",
    "LLMQuotaError",
    "LLMRateLimitError",
    "LLMServerError",
    "LLMTimeoutError",
]
