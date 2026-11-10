"""MiniMax provider adapter（批次 14 · V5：配置驱动 + 保留状态机）。

**渠道专属的 Python 部分只有一处**：``<think>`` 标签的**跨 chunk 缓冲**
（``ThinkBlockBuffer``）——SSE 按 BPE token 切分，标签会被拆到多个
chunk，单 chunk 正则永远匹配不上。这是真正的行为，必须留在 Python。

其余全部配置化（见 ``data/providers/minimax/*.toml`` 的 ``[channel]``）：

- 思考字段：M3.1-Flash 用独立字段 ``reasoning_content``；老 M3 用
  content 内嵌 ``<think>`` 标签 —— **两个模型的差异现在可 diff**；
- 错误映射：2013（空 chat content）→ 配置错误；403/429 → 配额；
- 窗口：context 1M / output 524288（M3.1-Flash 实测上限）。

**事故记录**（2026-11-10）：旧实现只查 content 里的 ``<think>`` 标签，
不查 ``reasoning_content`` —— M3.1-Flash 的思考被整体丢弃（8 个 SSE 帧
里 5 帧思考消失）。现在两个通道都由 toml 声明，adapter 通用实现认两者。
"""

from __future__ import annotations

from typing import Any

from ._think_stream import ThinkBlockBuffer
from .channel import ChannelAdapter, ChannelSpec, ErrorRule


class MiniMaxAdapter(ChannelAdapter):
    """MiniMax；思考通道/错误映射见 toml ``[channel]``，标签缓冲见下。"""

    #: 渠道默认（未绑定模型时的旧行为）。
    #:
    #: 旧实现只认 content 内嵌的 ``<think>`` 标签；这里**同时**认
    #: ``reasoning_content``（M3.1-Flash 的实际通道）——这是 2026-11-10
    #: 修复的"思考全丢"事故的兜底（即使模型 toml 缺失也不丢）。
    #:
    #: 错误规则与 ``data/providers/minimax/_channel.toml`` 保持一致
    #: （2013 → 配置错误；403/429 含 quota/billing → 配额；否则 429 → 限流）。
    fallback_spec = ChannelSpec(
        thinking_fields=("reasoning_content",),
        thinking_tags=("<think>", "</think>"),
        stream_options={"include_usage": True},
        # 思考 token 先于正文，且 max_tokens **包含**思考（M3.1 官方文档
        # 明确）——预算下限要给两者都留余量，否则正文为空。
        min_output_tokens=4096,
        error_rules=(
            ErrorRule(status=(400,), contains=("2013", "chat content is empty"),
                      kind="config",
                      message="MiniMax: empty chat content — 通常表示会话历史"
                              "被过度压缩，请开新会话"),
            ErrorRule(status=(401,), kind="auth"),
            ErrorRule(status=(403,), contains=("quota", "billing"),
                      kind="quota", message="MiniMax 配额已用完（5 小时滚动限额）"),
            ErrorRule(status=(429,), contains=("quota", "billing"),
                      kind="quota", message="MiniMax 配额已用完（5 小时滚动限额）"),
            # **不设"429 → rate"规则**：契约要求 429 且无 quota 关键字时
            # 返回 None（交给默认映射 → LLMRateLimitError）。
        ),
        configured=True,
    )

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def default_base_url(self) -> str:
        return "https://api.minimaxi.com/v1"

    @property
    def default_model(self) -> str:
        return "minimax-M3"

    #: 模型 toml 缺失时的渠道级默认（M3.1-Flash 实测上限 524288）。
    fallback_max_tokens = 524_288

    def __init__(self) -> None:
        super().__init__()
        # 流式跨 chunk 的 <think> 块缓冲器：content / reasoning_content
        # 各一个实例（同一 adapter 只服务一个请求，状态天然请求内）。
        self._think_buffers: dict[str, ThinkBlockBuffer] = {}

    def _buffer_for(self, field: str) -> ThinkBlockBuffer:
        buf = self._think_buffers.get(field)
        if buf is None:
            buf = ThinkBlockBuffer()
            self._think_buffers[field] = buf
        return buf

    def fix_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        """Pipeline Step 1：把跨 chunk 的 ``<think>`` 块缓冲成完整块交出。

        为什么必需：MiniMax 的 SSE 按 BPE token 切分，``<think>`` / ``</think>``
        会被拆到多个 chunk。下游（``extract_thinking_from_delta`` /
        ``sanitize_delta``）用的都是**单 chunk 正则** THINK_PATTERN，所以这里
        必须保证交出的每个 chunk 中的标签是完整的——否则思考内容漏进 UI 且
        ``delta_thinking`` 为空（实测 chunk[1] = ``'<think>We need answe'``）。

        **只对配了 thinking_tags 的模型生效**：M3.1-Flash 走独立字段
        ``reasoning_content``（无标签），它的 reasoning_content 不需要
        缓冲——但仍走同一缓冲器以防万一（无标签时缓冲器是恒等变换）。
        """
        out = dict(delta)
        for field_name in ("content", "reasoning_content"):
            value = out.get(field_name)
            if isinstance(value, str) and value:
                out[field_name] = self._buffer_for(field_name).fix(value)
        return out

    def quota_error_message(self) -> str:
        return "MiniMax 配额已用完（5小时限额）"
