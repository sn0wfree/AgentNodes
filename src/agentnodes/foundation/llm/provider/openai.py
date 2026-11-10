"""OpenAI provider adapter（批次 14 · V5：配置驱动）。

标准 OpenAI Chat Completions API。**只剩渠道身份**——行为（推理字段
``reasoning``、``include_usage``）来自
``data/providers/openai/gpt-4o-mini.toml`` 的 ``[channel]`` 段。

改前 44 行（硬编码 ``reasoning`` 字段 + include_usage + max_tokens）。
"""

from __future__ import annotations

from .channel import ChannelAdapter, ChannelSpec


class OpenAIAdapter(ChannelAdapter):
    """OpenAI 官方；推理字段与流式选项见 toml ``[channel]``。"""

    #: 渠道默认（未绑定模型时的旧行为；绑定后以 toml 为准）。
    fallback_spec = ChannelSpec(
        thinking_fields=("reasoning",),
        stream_options={"include_usage": True},
        configured=True,
    )

    @property
    def name(self) -> str:
        return "openai"

    @property
    def default_base_url(self) -> str:
        return "https://api.openai.com/v1"

    @property
    def default_model(self) -> str:
        return "gpt-4o-mini"

    #: 模型 toml 缺失时的渠道级默认（gpt-4o-mini 的窗口）。
    fallback_max_tokens = 16384
