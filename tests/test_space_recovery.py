"""Space-recovery regression tests (docs/streaming-space-recovery.md).

Root cause: DeepSeek-family `reasoning_content` streams one BPE token
per SSE chunk; the leading space is encoded *inside* the token
(``" me"``). The old per-chunk ``.strip()`` in ``normalize_thinking``
/ ``strip_dsml_text`` deleted those leading spaces, concatenating
``Let`` + `` me`` + `` explore`` into ``Letmeexplore``.

Fix: streaming delta paths pass ``strip_edges=False`` so chunk boundary
whitespace survives; the ``fix_delta`` hook stays a no-op placeholder.
"""

from __future__ import annotations

import pytest

from strategy_research.foundation.llm.parser import (
    _chunk_from_dict,
    parse_stream_chunk,
)
from strategy_research.foundation.llm.provider import (
    DeepSeekAdapter,
    MiniMaxAdapter,
    OpenAIAdapter,
    SiliconFlowAdapter,
)
from strategy_research.foundation.llm.provider._dsml_patterns import strip_dsml_text

# ── BPE chunk boundary: the core regression ─────────────────────────


def _bpe_chunks(words: list[str]) -> list[dict[str, str]]:
    """Simulate DeepSeek-style BPE token stream.

    First token has no leading space; every subsequent token carries its
    leading space inside the token (standard BPE encoding).
    """
    chunks = []
    for i, w in enumerate(words):
        if i == 0:
            chunks.append({"reasoning_content": w})
        else:
            chunks.append({"reasoning_content": " " + w})
    return chunks


class TestBpeChunkBoundarySpace:
    """The bug this whole change fixes: 'Let me explore' must survive."""

    @pytest.mark.parametrize(
        "adapter_cls", [DeepSeekAdapter, SiliconFlowAdapter],
    )
    def test_thinking_concatenation_keeps_spaces(self, adapter_cls):
        a = adapter_cls()
        words = ["Let", "me", "explore", "the", "workspace"]
        parts = []
        for chunk in _bpe_chunks(words):
            thinking = a.extract_thinking_from_delta(chunk)
            assert thinking is not None
            parts.append(thinking)
        joined = "".join(parts)
        assert joined == "Let me explore the workspace"

    def test_openai_path_unchanged(self):
        # OpenAI has no reasoning_content → extraction returns None.
        a = OpenAIAdapter()
        assert a.extract_thinking_from_delta({"reasoning_content": " me"}) is None

    def test_message_path_still_strips_edges(self):
        # Full-message path keeps strip_edges=True semantics.
        a = DeepSeekAdapter()
        out = a.extract_thinking_from_message({"reasoning_content": "  think  "})
        assert out == "think"

    def test_delta_path_keeps_edges(self):
        a = DeepSeekAdapter()
        out = a.extract_thinking_from_delta({"reasoning_content": "  think  "})
        assert out == " think "  # internal compression, edges preserved


class TestStreamPipelineSpace:
    """End-to-end through parse_stream_chunk with a single adapter."""

    def test_full_sse_stream(self):
        lines = []
        for chunk in _bpe_chunks(["Let", "me", "explore"]):
            import json

            lines.append("data: " + json.dumps({
                "choices": [{"delta": {"reasoning_content": chunk["reasoning_content"]}}],
            }))
        lines.append("data: [DONE]")

        adapter = DeepSeekAdapter()  # same instance across the stream
        thinking = ""
        for line in lines:
            sc = parse_stream_chunk(line, adapter=adapter)
            if sc is not None:
                thinking += sc.delta_thinking
        assert thinking == "Let me explore"

    def test_sanitize_preserves_content_boundary(self):
        adapter = SiliconFlowAdapter()
        delta = {"content": " hello", "reasoning_content": " me"}
        out = adapter.sanitize_delta(delta)
        assert out["content"] == " hello"


# ── strip_dsml_text strip_edges parameter ───────────────────────────


class TestStripDsmlStripEdges:
    def test_default_strips_edges(self):
        assert strip_dsml_text(" pre <tools>x</tools> post ") == "pre  post"

    def test_strip_edges_false_keeps_boundary(self):
        out = strip_dsml_text(" pre <tools>x</tools> post ", strip_edges=False)
        assert out == " pre  post "
        assert out[0] == " "
        assert out[-1] == " "

    def test_no_dsml_keeps_text(self):
        assert strip_dsml_text("clean text", strip_edges=False) == "clean text"

    def test_unclosed_block_truncation_no_strip(self):
        out = strip_dsml_text("prefix <tools>half", strip_edges=False)
        assert out == "prefix "


# ── MiniMax sanitize migration ──────────────────────────────────────


class TestMiniMaxSanitize:
    def test_extract_uses_raw_tags(self):
        a = MiniMaxAdapter()
        out = a.extract_thinking_from_delta({"content": "<think> my plan </think>answer"})
        assert out == " my plan "  # BPE edges preserved

    def test_sanitize_delta_removes_tags_keeps_boundary(self):
        a = MiniMaxAdapter()
        out = a.sanitize_delta({"content": "<think>plan</think> response"})
        assert out["content"] == " response"

    def test_sanitize_message_removes_tags_and_strips(self):
        a = MiniMaxAdapter()
        out = a.sanitize_message({"content": "<think>plan</think> response "})
        assert out["content"] == "response"

    def test_pipeline_minimax_both_fields(self):
        payload = {
            "choices": [{
                "delta": {"content": "<think>my plan</think>response text"},
            }]
        }
        chunk = _chunk_from_dict(payload, adapter=MiniMaxAdapter())
        assert chunk is not None
        assert chunk.delta_content == "response text"
        assert chunk.delta_thinking == "my plan"


# ── fix_delta reserved hook ─────────────────────────────────────────


class TestFixDeltaPlaceholder:
    def test_default_passthrough(self):
        a = OpenAIAdapter()
        delta = {"content": " x "}
        assert a.fix_delta(delta) == delta

    @pytest.mark.parametrize("adapter_cls", [DeepSeekAdapter, SiliconFlowAdapter, MiniMaxAdapter])
    def test_noop_across_adapters(self, adapter_cls):
        a = adapter_cls()
        delta = {"content": " x ", "reasoning_content": " y "}
        out = a.fix_delta(delta)
        assert out == delta


# ── Pipeline order regression: extract must run before sanitize ─────


class TestPipelineOrder:
    def test_deepseek_reasoning_cleaned_in_pipeline(self):
        payload = {
            "choices": [{
                "delta": {"reasoning_content": "think <tools>x</tools> end",
                          "content": "answer"},
            }]
        }
        chunk = _chunk_from_dict(payload, adapter=DeepSeekAdapter())
        assert chunk is not None
        assert "<tools>" not in chunk.delta_thinking
        assert chunk.delta_content == "answer"

    def test_plain_provider_passthrough(self):
        payload = {
            "choices": [{"delta": {"content": "answer"}}]
        }
        chunk = _chunk_from_dict(payload, adapter=OpenAIAdapter())
        assert chunk is not None
        assert chunk.delta_content == "answer"
        assert chunk.delta_thinking == ""
