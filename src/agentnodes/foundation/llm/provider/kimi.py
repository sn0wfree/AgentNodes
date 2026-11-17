"""Kimi (Moonshot) provider adapter（批次 14 · V5：配置驱动）。

OpenAI 兼容 API。**只剩渠道身份**——思考字段来自
``data/providers/moonshotai/kimi-k2.5.toml`` 的 ``[channel]`` 段。

**行为变更说明**（2026-11-10）：改造前本 adapter 硬编码"无原生思考
支持"（两个 ``return None``），而 K2.5 的 toml 里写着
``[interleaved].field = "reasoning_content"`` —— 两处自相矛盾。
现在以 toml 为准：K2.5 会提取 ``reasoning_content``。
"""

from __future__ import annotations

from agentnodes.foundation.llm.provider.channel import ChannelAdapter, ChannelSpec


class KimiAdapter(ChannelAdapter):
    """Moonshot；思考字段见 toml ``[channel]``（未配置 = 不提取）。"""

    #: 渠道默认（未绑定模型时的旧行为：**不提取思考**）。
    #:
    #: 注意这与 K2.5 的 toml（``thinking_fields = ["reasoning_content"]``）
    #: 不同——那是**模型级**事实，绑定模型后以 toml 为准（K2.5 会提取）。
    fallback_spec = ChannelSpec(configured=True)

    @property
    def name(self) -> str:
        return "kimi"

    @property
    def default_base_url(self) -> str:
        return "https://api.moonshot.cn/v1"

    @property
    def default_model(self) -> str:
        return "moonshot-v1-8k"

    #: 模型 toml 缺失时的渠道级默认。
    fallback_max_tokens = 8192
