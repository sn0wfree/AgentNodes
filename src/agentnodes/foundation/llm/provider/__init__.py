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

from agentnodes.foundation.llm.provider._reasoning_field import OpenAIReasoningFieldAdapter
from agentnodes.foundation.llm.provider.base import ProviderAdapter
from agentnodes.foundation.llm.provider.deepseek import DeepSeekAdapter
from agentnodes.foundation.llm.provider.fallback import FallbackAdapter
from agentnodes.foundation.llm.provider.kimi import KimiAdapter
from agentnodes.foundation.llm.provider.minimax import MiniMaxAdapter
from agentnodes.foundation.llm.provider.nvidia import NvidiaAdapter
from agentnodes.foundation.llm.provider.openai import OpenAIAdapter
from agentnodes.foundation.llm.provider.qwen import QwenAdapter
from agentnodes.foundation.llm.provider.siliconflow import SiliconFlowAdapter

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
# See docs/guides/model-catalog-design.md.
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


def get_provider(name: str | None, model: str | None = None) -> ProviderAdapter:
    """Get provider adapter by name. Unknown providers get fallback.

    The 'auto' / None / empty name resolves to FallbackAdapter.

    :param model: **可选**模型名。给出时，若该 adapter 支持配置驱动
        （即 ``ChannelAdapter`` 子类），会按 ``data/providers/<id>/<model>.toml``
        的 ``[channel]`` 段装载该模型的行为（思考字段/标签/错误映射）。
        不给出时用**该渠道的默认模型**去装载（这样
        ``get_provider("nvidia").default_context_tokens`` 能从 toml 读到
        真实窗口，而不是退化成 8192）；toml 也没有时用渠道 fallback_spec。
    """
    if not name or name == "auto":
        return FallbackAdapter()
    cls = _REGISTRY.get(name, FallbackAdapter)
    adapter = cls()
    attach = getattr(adapter, "attach_model", None)
    if callable(attach):
        attach(model or getattr(adapter, "default_model", None))
    return adapter


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

    **不绑定模型**（V5）：这里问的是**渠道级**默认（未指定模型时该用
    什么），所以取 adapter 的 fallback 值而非某个具体模型的 toml——
    否则渠道默认会随"该渠道默认模型的 toml"漂移（实测：minimax 默认
    模型 M3 的 output=128000，而渠道实际支持 524288）。

    指定了模型时用 ``get_provider(name, model)``（读该模型的 toml）。
    """
    adapter = get_provider(name)
    # 解绑：渠道默认 = fallback（不跟默认模型的 toml 走）。
    detach = getattr(adapter, "detach_model", None)
    if callable(detach):
        detach()
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
