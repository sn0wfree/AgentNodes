"""MiniMax `<think>` 跨 chunk 重组测试（P5-1b 真实验证暴露的缺陷）。

实测背景：MiniMax-M3 把推理内联在 delta.content 的 `<think>...</think>` 里，
SSE 按 BPE token 切分 → 开标签被切成多 chunk → 下游单 chunk 正则
（THINK_PATTERN）匹配不到 → 思考内容漏进 UI 且 delta_thinking 为空。

修复遵循管线顺序 fix_delta → extract_thinking → sanitize_delta：
**fix_delta 只重组标记，不删除内容**，让 extract 能提取、sanitize 能剥离。
"""
from __future__ import annotations

import pytest

from strategy_research.foundation.llm.parser import _chunk_from_dict
from strategy_research.foundation.llm.provider._think_stream import (
    ThinkBlockBuffer,
    strip_think_blocks,
)
from strategy_research.foundation.llm.provider.minimax import MiniMaxAdapter


def feed(reflow: ThinkBlockBuffer, chunks: list[str]) -> str:
    return "".join(reflow.fix(c) for c in chunks)


# ── 标记重组 ─────────────────────────────────────────────


def test_plain_text_passes_through():
    assert feed(ThinkBlockBuffer(), ["hello", " world"]) == "hello world"


def test_complete_tag_in_one_chunk_unchanged():
    assert feed(ThinkBlockBuffer(), ["<think>x</think>y"]) == "<think>x</think>y"


def test_open_tag_split_across_three_chunks():
    out = feed(ThinkBlockBuffer(), ["<thi", "nk>hidden", "</think>visible"])
    assert out == "<think>hidden</think>visible"


def test_close_tag_split():
    out = feed(ThinkBlockBuffer(), ["<think>a</thi", "nk>b"])
    assert out == "<think>a</think>b"


def test_reproduce_real_minimax_chunking():
    """复现实测形态：标记跨 chunk，重组后下游正则可匹配。"""
    chunks = [
        "",
        "<think>We need answe",
        "r in Chinese?",
        " User asks ",
        "</think>",
        "1,2,3,4,5",
    ]
    out = feed(ThinkBlockBuffer(), chunks)
    assert "<think>" in out and "</think>" in out  # 标记已补全 → 下游可处理
    assert out.endswith("1,2,3,4,5")


def test_pending_released_when_not_a_tag():
    """以 < 开头但并非标记的文本不应永久滞留。"""
    out = feed(ThinkBlockBuffer(), ["a <", "b"])
    assert out == "a <b"


def test_angle_bracket_text_not_lost():
    out = feed(ThinkBlockBuffer(), ["compare a <", " b"])
    assert "<" in out and "b" in out


# ── 适配器集成：管线端到端 ───────────────────────────────


def test_adapter_pipeline_extracts_thinking_across_chunks():
    """跨 chunk 后 extract_thinking 仍能拿到推理内容。"""
    adapter = MiniMaxAdapter()
    payloads = [
        {"choices": [{"delta": {"content": ""}}]},
        {"choices": [{"delta": {"content": "<think>my "}}]},
        {"choices": [{"delta": {"content": "plan</think>"}}]},
        {"choices": [{"delta": {"content": "answer"}}]},
    ]
    chunks = [
        _chunk_from_dict(p, adapter=adapter) for p in payloads
    ]
    content = "".join(c.delta_content for c in chunks if c)
    thinking = "".join(c.delta_thinking for c in chunks if c)

    assert "<think>" not in content
    assert content.strip() == "answer"
    assert "my" in thinking and "plan" in thinking


def test_adapter_single_chunk_still_works():
    """完整标签在单 chunk 内：与改造前行为一致。"""
    adapter = MiniMaxAdapter()
    chunk = _chunk_from_dict(
        {"choices": [{"delta": {"content": "<think>plan</think>done"}}]},
        adapter=adapter,
    )
    assert chunk.delta_content == "done"
    assert chunk.delta_thinking == "plan"


def test_adapter_reflows_are_per_field():
    adapter = MiniMaxAdapter()
    adapter.fix_delta({"content": "<thi"})
    out = adapter.fix_delta({"reasoning_content": "plain"})
    assert out["reasoning_content"] == "plain"


def test_adapter_swallows_non_string_fields():
    adapter = MiniMaxAdapter()
    out = adapter.fix_delta({"content": None, "reasoning_content": 123})
    assert out["content"] is None
    assert out["reasoning_content"] == 123


def test_adapter_fresh_instance_is_stateless():
    a1 = MiniMaxAdapter()
    a1.fix_delta({"content": "<thi"})
    a2 = MiniMaxAdapter()
    assert a2.fix_delta({"content": "hello"})["content"] == "hello"


# ── 非流式整段剥离（保留给需要一次性清理的调用方）─────────


def test_strip_think_blocks_whole_text():
    assert strip_think_blocks("<think>x</think>answer") == "answer"
    assert strip_think_blocks("a<think>b</think>c") == "ac"
    assert strip_think_blocks("no tags") == "no tags"


@pytest.mark.parametrize("chunks,expected", [
    (["<think>a</think>b"], "<think>a</think>b"),
    (["<thi", "nk>a", "</think>b"], "<think>a</think>b"),
    (["plain"], "plain"),
])
def test_reflow_output_is_marker_intact(chunks, expected):
    assert feed(ThinkBlockBuffer(), chunks) == expected
