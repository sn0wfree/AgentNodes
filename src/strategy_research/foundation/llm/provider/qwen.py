"""Qwen (Aliyun DashScope) provider adapter.

Uses OpenAI-compatible mode. Thinking tokens come via `reasoning_content`
when enable_thinking is set on the model, inherited from
OpenAIReasoningFieldAdapter.
"""

from __future__ import annotations

from ._reasoning_field import OpenAIReasoningFieldAdapter


class QwenAdapter(OpenAIReasoningFieldAdapter):
    @property
    def name(self) -> str:
        return "qwen"

    @property
    def default_base_url(self) -> str:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def default_model(self) -> str:
        return "qwen-plus"
