"""Tests for text_filters.strip_thinking_tags.

Covers all reasoning-tag patterns + edge cases.
"""
from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.tui.text_filters import strip_thinking_tags

# ── closed tags ──────────────────────────────────────────────────────


class TestClosedThinkTags:
    @pytest.mark.parametrize("tag", ["think", "thinking"])
    def test_simple_think_block(self, tag):
        text = f"before <{tag}>secret reasoning</{tag}> after"
        assert strip_thinking_tags(text) == "before  after"

    def test_think_block_with_code_inside(self):
        text = """<think>
def foo():
    return 42
</think>
# Answer

The result is 42."""
        result = strip_thinking_tags(text)
        assert "def foo" not in result
        assert "Answer" in result
        assert "result is 42" in result

    def test_reasoning_block(self):
        text = "before <reasoning>secret</reasoning> after"
        assert strip_thinking_tags(text) == "before  after"

    def test_thinking_block(self):
        text = "before <thinking>secret</thinking> after"
        assert strip_thinking_tags(text) == "before  after"

    def test_qwen_reasoning_tags(self):
        text = "before <|reasoning|>secret<|/reasoning|> after"
        assert strip_thinking_tags(text) == "before  after"

    def test_think_self_closing(self):
        text = "before <think/> after"
        assert strip_thinking_tags(text) == "before  after"

    def test_multiple_blocks(self):
        text = "<think>1</think>mid<reasoning>2</reasoning>end"
        result = strip_thinking_tags(text)
        assert "1" not in result
        assert "2" not in result
        assert "mid" in result
        assert "end" in result


# ── unclosed tags (streaming truncation) ────────────────────────────


class TestUnclosedThinkTags:
    def test_unclosed_think_at_end(self):
        text = "Answer here.\n<think>still reasoning..."
        result = strip_thinking_tags(text)
        assert "Answer here." in result
        assert "reasoning" not in result

    def test_unclosed_thinking_mid_text(self):
        text = "before<think>reasoning that never closes"
        result = strip_thinking_tags(text)
        assert "before" in result
        assert "reasoning" not in result

    def test_unclosed_reasoning_at_end(self):
        text = "visible<reasoning>internal reasoning"
        result = strip_thinking_tags(text)
        assert "visible" in result
        assert "internal" not in result


# ── mixed tags + nested cases ────────────────────────────────────────


class TestMixedTags:
    def test_closed_then_unclosed(self):
        text = "<think>first</think>middle<think>never closes"
        result = strip_thinking_tags(text)
        assert "first" not in result
        assert "middle" in result
        assert "never" not in result

    def test_only_thinking_content(self):
        """All content is thinking — should return empty string."""
        text = "<think>everything here is reasoning</think>"
        assert strip_thinking_tags(text) == ""


# ── edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_string(self):
        assert strip_thinking_tags("") == ""

    def test_none(self):
        """None input returns None (preserves None semantics)."""
        assert strip_thinking_tags(None) is None

    def test_plain_text_unchanged(self):
        text = "Just a normal answer with **bold** and `code`."
        assert strip_thinking_tags(text) == text

    def test_chinese_content_preserved(self):
        text = "<think>内部推理</think>\n# A股动量策略\n\n关键指标: 12.3%"
        result = strip_thinking_tags(text)
        assert "内部推理" not in result
        assert "A股动量策略" in result
        assert "12.3%" in result

    def test_whitespace_stripped(self):
        text = "<think>x</think>\n\n  \n\n  answer  \n\n"
        assert strip_thinking_tags(text) == "answer"

    def test_markdown_preserved(self):
        text = "<think>reasoning</think># Header\n\n- item 1\n- item 2"
        result = strip_thinking_tags(text)
        assert "# Header" in result
        assert "- item 1" in result

    def test_code_block_in_answer_preserved(self):
        text = """<think>reasoning</think>```python
def foo():
    return 42
```"""
        result = strip_thinking_tags(text)
        assert "def foo" in result
        assert "return 42" in result

    def test_inline_code_preserved(self):
        text = "<think>reasoning</think>Use `print()` to log."
        result = strip_thinking_tags(text)
        assert "`print()`" in result

    def test_bold_preserved(self):
        text = "<think>reasoning</think>This is **bold** text."
        result = strip_thinking_tags(text)
        assert "**bold**" in result


# ── multiple occurrences ─────────────────────────────────────────────


class TestRepeatedStrip:
    def test_think_tags_stripped_in_loop_until_clean(self):
        """strip_thinking_tags is idempotent."""
        text = "<think>a</think><think>b</think>visible<think>c</think>"
        result = strip_thinking_tags(text)
        assert result == "visible"

    def test_adjacent_think_blocks(self):
        text = "<think>1</think><think>2</think><think>3</think>answer"
        result = strip_thinking_tags(text)
        assert result == "answer"
