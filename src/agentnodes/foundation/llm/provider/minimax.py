"""MiniMax provider adapter.

Special handling:
- Thinking tokens emitted as <think> tags in delta.content
- Uses 403 to signal quota exhaustion (5-hour rolling limit)
- Uses 429 to signal either quota or per-minute rate limit
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import LLMConfigError, LLMQuotaError
from ._think_stream import ThinkBlockBuffer
from .base import ProviderAdapter


class MiniMaxAdapter(ProviderAdapter):
    THINK_PATTERN = re.compile(r"<think>([\s\S]*?)<\/think>")
    THINK_OPEN = "<think>"
    THINK_CLOSE = "</think>"

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
        """
        out = dict(delta)
        for field_name in ("content", "reasoning_content"):
            value = out.get(field_name)
            if isinstance(value, str) and value:
                out[field_name] = self._buffer_for(field_name).fix(value)
        return out

    @property
    def name(self) -> str:
        return "minimax"

    @property
    def default_base_url(self) -> str:
        return "https://api.minimaxi.com/v1"

    @property
    def default_model(self) -> str:
        return "minimax-M3"

    @property
    def default_max_tokens(self) -> int:
        return 32000

    @property
    def min_output_tokens(self) -> int:
        # MiniMax-M3 emits <think>…</think> before the answer; tiny budgets yield empty content.
        return 4096

    def custom_stream_options(self) -> dict[str, Any] | None:
        # Ask the server for the final usage snapshot (A1 budget accounting).
        return {"include_usage": True}

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        content = delta.get("content", "")
        match = self.THINK_PATTERN.search(content)
        if match:
            # BPE chunk boundary: keep leading spaces inside the tags.
            return self.normalize_thinking(match.group(1), strip_edges=False)
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        content = message.get("content", "")
        match = self.THINK_PATTERN.search(content)
        if match:
            return self.normalize_thinking(match.group(1))
        return None

    def sanitize_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        content = delta.get("content", "")
        if self.THINK_PATTERN.search(content):
            cleaned = self.THINK_PATTERN.sub("", content)
            out = dict(delta)
            out["content"] = cleaned
            return out
        return dict(delta)

    def sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content", "")
        if self.THINK_PATTERN.search(content):
            cleaned = self.THINK_PATTERN.sub("", content).strip()
            out = dict(message)
            out["content"] = cleaned
            return out
        return dict(message)

    def handle_error(self, status: int, body: Any) -> Exception | None:
        # MiniMax uses 403 for quota exhaustion, and 429 can be either
        # quota or per-minute rate limit.
        if status in (401, 403):
            error_code = self.extract_error_code(body)
            if "quota" in error_code or "billing" in error_code:
                return LLMQuotaError(f"quota exceeded ({status}): {body}")
        if status == 429:
            error_code = self.extract_error_code(body)
            if "quota" in error_code or "billing" in error_code:
                return LLMQuotaError(f"quota exceeded (429): {body}")
        # MiniMax-specific: 400 with code 2013 means "chat content is empty"
        # This usually happens when over-compression leaves an empty context.
        # Map to LLMConfigError so:
        # 1. _is_stream_required_error returns True (no stream→achat fallback)
        #    — retrying via non-streaming won't help.
        # 2. The error is propagated as a user-visible configuration error
        #    with a clear "send new message or create new session" message.
        if status == 400:
            body_str = str(body)
            error_code = self.extract_error_code(body)
            if "2013" in body_str or "chat content is empty" in body_str.lower() \
                    or "2013" in error_code:
                return LLMConfigError(
                    f"empty chat content (MiniMax 2013): {body}. "
                    f"This usually means the conversation history was over-compressed. "
                    f"Please send a new message or create a new session."
                )
        return None

    def quota_error_message(self) -> str:
        return "MiniMax 配额已用完（5小时限额）"
