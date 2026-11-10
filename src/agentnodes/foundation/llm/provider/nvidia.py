"""NVIDIA NIM provider adapter（批次 14 · V5：配置驱动）。

NVIDIA NIM（https://integrate.api.nvidia.com）暴露 OpenAI Chat
Completions 兼容 API。**只剩渠道身份**——行为全部来自
``data/providers/nvidia/glm-5.2.toml`` 的 ``[channel]`` 段：

- 思考字段：``reasoning``（OpenAI 系）与 ``reasoning_content``
  （DeepSeek 系）都认，按序尝试；
- 错误映射：402/403 表示配额耗尽（NIM 特有，不是 OpenAI 的 429）；
- 窗口：context 131072 / output 16384。

改前 72 行（字段名与错误映射硬编码在 Python 里）。
"""

from __future__ import annotations

from .channel import ChannelAdapter, ChannelSpec, ErrorRule


class NvidiaAdapter(ChannelAdapter):
    """NIM；思考字段与配额错误映射见 toml ``[channel]``。"""

    #: 渠道默认（未绑定模型时的旧行为）。
    fallback_spec = ChannelSpec(
        thinking_fields=("reasoning", "reasoning_content"),
        error_rules=(
            ErrorRule(status=(402,), kind="quota",
                      message="NVIDIA NIM 配额不足"),
            ErrorRule(status=(403,), contains=("quota", "billing"),
                      kind="quota", message="NVIDIA NIM 配额不足"),
            ErrorRule(status=(429,), contains=("quota", "billing"),
                      kind="quota", message="NVIDIA NIM 配额不足"),
            # 刻意不设 "429 -> rate"：无 quota/billing 时交给默认映射。
        ),
        configured=True,
    )

    @property
    def name(self) -> str:
        return "nvidia"

    @property
    def default_base_url(self) -> str:
        return "https://integrate.api.nvidia.com/v1"

    @property
    def default_model(self) -> str:
        return "z-ai/glm-5.2"

    #: 模型 toml 缺失时的渠道级默认。
    fallback_max_tokens = 16384

    def quota_error_message(self) -> str:
        return "NVIDIA NIM 配额不足"
