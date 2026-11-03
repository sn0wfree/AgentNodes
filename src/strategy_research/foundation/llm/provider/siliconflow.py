"""SiliconFlow (硅基流动) provider adapter.

https://platform.siliconflow.cn — OpenAI Chat Completions-compatible
endpoint. Most reasoning models served here (DeepSeek-R1 / V4-Flash,
QwQ-32B, GLM-Z1) emit thinking via ``reasoning_content``, so this
adapter inherits OpenAIReasoningFieldAdapter. See
docs/llm-provider-setup.md for onboarding params.

Also overrides ``sanitize_delta`` / ``sanitize_message``
to remove the DeepSeek-V4-Flash ``<tools>...</tools>`` /
``[DSML | ...<]`` pseudo-tool-call leakage that the model emits inside
``reasoning_content`` / ``content``. Real tool calls still travel
through the structured ``delta.tool_calls`` path; the in-text blocks
are pure UI noise.
"""

from __future__ import annotations

from ._dsml_patterns import StreamDsmlFixer, strip_dsml_text
from ._reasoning_field import OpenAIReasoningFieldAdapter


class SiliconFlowAdapter(OpenAIReasoningFieldAdapter):
    def __init__(self) -> None:
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

    @property
    def default_max_tokens(self) -> int:
        return 16384

    @property
    def min_output_tokens(self) -> int:
        # DeepSeek-V4-Flash reasons via reasoning_content first.
        return 4096

    @property
    def default_context_tokens(self) -> int:
        # DeepSeek-V4-Flash serves a 128K context window (DeepSeek
        # lineage). Without this override the conservative 8192 base
        # default would trigger compaction far too early.
        return 131072

    # ── DSML filter (DeepSeek-V4-Flash reasoning_content leakage) ──

    def fix_delta(self, delta):
        """Pipeline Step 1: cross-chunk DSML block removal.

        DeepSeek streams one BPE token per SSE chunk, so markup tags
        arrive split across chunks and per-chunk regex (sanitize_delta)
        can never match them. This stateful hook buffers partial
        markers and drops whole blocks across chunk boundaries.
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
        content = delta.get("reasoning_content")
        if content:
            # Strip DSML leakage first — extract must read clean text.
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
