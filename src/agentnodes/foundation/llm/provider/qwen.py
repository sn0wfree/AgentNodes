"""Qwen (Aliyun DashScope) provider adapter（批次 14 · V5：配置驱动）。

本文件**只剩渠道身份**（name / base_url / default_model）——所有行为
（思考字段 ``reasoning_content``、``stream_options``）来自
``data/providers/alibaba/qwen-plus.toml`` 的 ``[channel]`` 段，由
``ChannelAdapter`` 统一实现。

改前 24 行，继承 ``OpenAIReasoningFieldAdapter``（字段名硬编码在基类）；
改后行为按模型写在 toml 里，换模型/加模型都不必改 Python。
"""

from __future__ import annotations

from agentnodes.foundation.llm.provider.channel import ChannelAdapter, ChannelSpec


class QwenAdapter(ChannelAdapter):
    """DashScope OpenAI 兼容模式；思考字段见 toml ``[channel]``。"""

    #: 渠道默认（未绑定模型时的旧行为）。
    fallback_spec = ChannelSpec(
        thinking_fields=("reasoning_content",),
        stream_options={"include_usage": True},
        configured=True,
    )

    @property
    def name(self) -> str:
        return "qwen"

    @property
    def default_base_url(self) -> str:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def default_model(self) -> str:
        return "qwen-plus"
