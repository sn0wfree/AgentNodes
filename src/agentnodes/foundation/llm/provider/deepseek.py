"""DeepSeek provider adapter（批次 14 · V5：配置驱动 + 保留 DSML 状态机）。

**渠道专属的 Python 部分只有 DSML 过滤器**（``StreamDsmlFixer`` /
``strip_dsml_text``）——DeepSeek 推理模型会把 ``<tools>...</tools>`` /
``[DSML | ...<]`` 伪工具调用泄漏进正文/思考里，而 SSE 按 BPE token
切分会让标签跨 chunk，单 chunk 正则匹配不上。这是真正的行为，必须留
Python。

其余全部配置化（``data/providers/deepseek/deepseek-chat.toml`` 的
``[channel]``）：思考字段 ``reasoning_content``、``include_usage``、
输出预算下限 4096（思考 token 先于正文）。
"""

from __future__ import annotations

from typing import Any

from ._dsml_patterns import StreamDsmlFixer, strip_dsml_text
from .channel import ChannelAdapter, ChannelSpec


class DeepSeekAdapter(ChannelAdapter):
    """DeepSeek；思考字段见 toml ``[channel]``，DSML 过滤见下。"""

    #: 渠道默认（未绑定模型时的旧行为）。
    fallback_spec = ChannelSpec(
        thinking_fields=("reasoning_content",),
        stream_options={"include_usage": True},
        min_output_tokens=4096,
        configured=True,
    )

    def __init__(self) -> None:
        super().__init__()
        # Per-field cross-chunk DSML state machines (fix_delta hook).
        self._reasoning_fixer = StreamDsmlFixer()
        self._content_fixer = StreamDsmlFixer()

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def default_base_url(self) -> str:
        return "https://api.deepseek.com/v1"

    @property
    def default_model(self) -> str:
        return "deepseek-chat"

    # ── DSML filter（DeepSeek reasoning_content 泄漏）──────────────

    def fix_delta(self, delta):
        """Pipeline Step 1：跨 chunk 的 DSML 块移除。

        DeepSeek 每 SSE chunk 一个 BPE token，标签会被切开，单 chunk 正则
        （sanitize_delta）永远匹配不上——这个状态机负责缓冲半个标记、
        跨 chunk 丢弃整块。
        """
        out = dict(delta)
        v = out.get("reasoning_content")
        if isinstance(v, str) and v:
            out["reasoning_content"] = self._reasoning_fixer.fix(v)
        v = out.get("content")
        if isinstance(v, str) and v:
            out["content"] = self._content_fixer.fix(v)
        return out

    def extract_thinking_from_delta(self, delta):
        """思考提取——先剥 DSML 噪声（提取必须读干净文本）。"""
        content = delta.get("reasoning_content")
        if content:
            return self.normalize_thinking(
                strip_dsml_text(content, strip_edges=False),
                strip_edges=False,
            )
        return super().extract_thinking_from_delta(delta)

    def extract_thinking_from_message(self, message):
        content = message.get("reasoning_content")
        if content:
            return self.normalize_thinking(strip_dsml_text(content))
        return super().extract_thinking_from_message(message)

    def sanitize_delta(self, delta):
        out = dict(delta)
        v = out.get("content")
        if isinstance(v, str) and v:
            out["content"] = strip_dsml_text(v, strip_edges=False)
        return out

    def sanitize_message(self, message):
        out = dict(message)
        v = out.get("content")
        if isinstance(v, str) and v:
            out["content"] = strip_dsml_text(v)
        return out
