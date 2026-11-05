"""DeepSeek provider adapter.

Uses OpenAI-compatible API. Thinking tokens come via ``reasoning_content``,
inherited from :class:`OpenAIReasoningFieldAdapter`.

Also overrides ``sanitize_delta`` / ``sanitize_message``
to remove the ``<tools>...</tools>`` / ``[DSML | ...<]`` pseudo-tool-call
leakage that DeepSeek reasoning models emit inside
``reasoning_content`` / ``content``. Real tool calls still travel
through the structured ``delta.tool_calls`` path.
"""

from __future__ import annotations

from typing import Any

from ._dsml_patterns import StreamDsmlFixer, strip_dsml_text
from ._reasoning_field import OpenAIReasoningFieldAdapter


class DeepSeekAdapter(OpenAIReasoningFieldAdapter):
    def __init__(self) -> None:
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

    @property
    def min_output_tokens(self) -> int:
        # reasoning_content precedes the answer; keep room for both.
        return 4096

    def custom_stream_options(self) -> dict[str, Any] | None:
        return {"include_usage": True}

    # ── DSML filter (DeepSeek reasoning_content leakage) ──

    def fix_delta(self, delta):
        """Pipeline Step 1: cross-chunk DSML block removal."""
        out = dict(delta)
        v = out.get("reasoning_content")
        if isinstance(v, str) and v:
            out["reasoning_content"] = self._reasoning_fixer.fix(v)
        v = out.get("content")
        if isinstance(v, str) and v:
            out["content"] = self._content_fixer.fix(v)
        return out

    def extract_thinking_from_delta(self, delta):
        content = delta.get("reasoning_content")
        if content:
            return self.normalize_thinking(
                strip_dsml_text(content, strip_edges=False),
                strip_edges=False,
            )
        return None

    def extract_thinking_from_message(self, message):
        content = message.get("reasoning_content")
        if content:
            return self.normalize_thinking(strip_dsml_text(content))
        return None

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
