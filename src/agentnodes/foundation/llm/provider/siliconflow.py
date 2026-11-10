"""SiliconFlow（硅基流动）provider adapter（批次 14 · V5：配置驱动 + DSML 状态机）。

https://platform.siliconflow.cn — OpenAI Chat Completions 兼容端点。

**渠道专属的 Python 部分只有 DSML 过滤器**：平台上多数推理模型
（DeepSeek-R1 / V4-Flash、QwQ-32B、GLM-Z1）会往 ``reasoning_content`` /
``content`` 里泄漏 ``<tools>...</tools>`` / ``[DSML | ...<]`` 伪工具调用，
而 SSE 按 BPE token 切分 → 标签跨 chunk → 单 chunk 正则匹配不上。
真实工具调用走结构化的 ``delta.tool_calls``，那些块纯属 UI 噪声。

其余全部配置化（``[channel]``）：思考字段 ``reasoning_content``、
``include_usage``、输出预算下限 4096（DeepSeek-V4-Flash 先思考后正文）。
"""

from __future__ import annotations

from ._dsml_patterns import StreamDsmlFixer, strip_dsml_text
from .channel import ChannelAdapter, ChannelSpec


class SiliconFlowAdapter(ChannelAdapter):
    """SiliconFlow；思考字段见 toml ``[channel]``，DSML 过滤见下。"""

    #: 渠道默认（未绑定模型时的旧行为；该平台暂无 bundled toml）。
    fallback_spec = ChannelSpec(
        thinking_fields=("reasoning_content",),
        stream_options={"include_usage": True},
        min_output_tokens=4096,
        configured=True,
    )

    def __init__(self) -> None:
        super().__init__()
        # Per-field cross-chunk DSML state machines (fix_delta hook).
        # The client creates a fresh adapter per request, so these
        # stream states are naturally request-scoped.
        self._reasoning_fixer = StreamDsmlFixer()
        self._content_fixer = StreamDsmlFixer()

    @property
    def name(self) -> str:
        return "siliconflow"

    @property
    def default_base_url(self) -> str:
        return "https://api.siliconflow.cn/v1"

    @property
    def default_model(self) -> str:
        return "deepseek-ai/DeepSeek-V4-Flash"

    #: 模型 toml 缺失时的渠道级默认。
    fallback_max_tokens = 16384

    # ── DSML filter（DeepSeek-V4-Flash reasoning_content 泄漏）────

    def fix_delta(self, delta):
        """Pipeline Step 1：跨 chunk 的 DSML 块移除。

        DeepSeek 每个 SSE chunk 一个 BPE token，标签被切开，单 chunk 正则
        永远匹配不上。这个状态机缓冲半个标记、跨 chunk 丢弃整块。
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
