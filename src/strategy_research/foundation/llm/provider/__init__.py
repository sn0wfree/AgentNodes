"""Provider adapter registry.

Each provider encapsulates its own:
- Default endpoint, model, max_tokens
- Thinking/reasoning token extraction
- HTTP headers / payload customization
- Error code parsing and exception mapping
- User-friendly error messages

Adding a new provider:
    1. Create provider/new_provider.py with NewProviderAdapter class
    2. Register it in _REGISTRY below

No core files (parser.py, openai_client.py, loop.py) need modification.
"""

from __future__ import annotations

from typing import Any

from ._reasoning_field import OpenAIReasoningFieldAdapter
from .base import ProviderAdapter
from .deepseek import DeepSeekAdapter
from .fallback import FallbackAdapter
from .kimi import KimiAdapter
from .minimax import MiniMaxAdapter
from .nvidia import NvidiaAdapter
from .openai import OpenAIAdapter
from .qwen import QwenAdapter
from .siliconflow import SiliconFlowAdapter

_REGISTRY: dict[str, type[ProviderAdapter]] = {
    "openai": OpenAIAdapter,
    "deepseek": DeepSeekAdapter,
    "minimax": MiniMaxAdapter,
    "qwen": QwenAdapter,
    "kimi": KimiAdapter,
    "nvidia": NvidiaAdapter,
    "siliconflow": SiliconFlowAdapter,
}


# Map internal provider names to models.dev provider_ids used in URL paths.
# See docs/model-catalog-design.md.
MODELS_DEV_ID: dict[str, str] = {
    "minimax": "minimax-cn-coding-plan",
    "minimax-cn": "minimax-cn",
    "minimax-cn-coding-plan": "minimax-cn-coding-plan",
    "minimax-coding-plan": "minimax-coding-plan",
    "openai": "openai",
    "deepseek": "deepseek",
    "qwen": "alibaba",
    "kimi": "moonshotai",
    "nvidia": "nvidia",
    "siliconflow": "siliconflow",
}


def models_dev_id(provider: str) -> str:
    """Map internal provider name to models.dev provider_id.

    Unknown providers return the lowercase input unchanged.
    """
    return MODELS_DEV_ID.get(provider, provider.lower())


def get_provider(name: str | None) -> ProviderAdapter:
    """Get provider adapter by name. Unknown providers get fallback.

    The 'auto' / None / empty name resolves to FallbackAdapter.
    """
    if not name or name == "auto":
        return FallbackAdapter()
    cls = _REGISTRY.get(name, FallbackAdapter)
    return cls()


def register_provider(name: str, cls: type[ProviderAdapter]) -> None:
    """Register a new provider adapter.

    Args:
        name: provider identifier
        cls: ProviderAdapter subclass
    """
    _REGISTRY[name] = cls


def get_provider_defaults(name: str | None) -> dict[str, Any]:
    """Get default config values (base_url, model, max_tokens) for a provider.

    Falls back to empty dict if the provider is unknown. ``context_tokens``
    is the model's context window (input + output), used by config.py as
    the ``model_context_tokens`` fallback when a profile omits it.
    """
    adapter = get_provider(name)
    return {
        "base_url": adapter.default_base_url,
        "model": adapter.default_model,
        "max_tokens": adapter.default_max_tokens,
        "context_tokens": adapter.default_context_tokens,
    }


__all__ = [
    "ProviderAdapter",
    "OpenAIReasoningFieldAdapter",
    "OpenAIAdapter",
    "DeepSeekAdapter",
    "MiniMaxAdapter",
    "NvidiaAdapter",
    "QwenAdapter",
    "KimiAdapter",
    "SiliconFlowAdapter",
    "FallbackAdapter",
    "get_provider",
    "register_provider",
    "get_provider_defaults",
]
