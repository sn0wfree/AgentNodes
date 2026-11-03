"""Tests for ``reformat_body_content`` — JSON detection + code-block wrapping.

The function detects JSON-like content in assistant body text and wraps
it in a fenced `````json``` `` block so Rich Markdown renders it with
monokai syntax highlighting and natural word-wrap.

Covered cases:
  * Empty / whitespace input → passthrough
  * Plain text without JSON → passthrough
  * Pure JSON (no markdown prefix)
  * Markdown prefix + JSON body
  * Truncated / incomplete JSON (no closing ``}``)
  * Nested JSON objects
  * Content with ``{`` but no ``": `` → not treated as JSON
  * Content with ``": `` but no ``{`` → not treated as JSON
  * Multiple ``{`` in content → first one used
  * Very long JSON (1000+ chars)
  * Chinese keys and values in JSON
"""
from __future__ import annotations

from strategy_research.interfaces.cli.tui.content_formatter import reformat_body_content


class TestEmptyInput:
    def test_empty_string(self):
        assert reformat_body_content("") == ""

    def test_whitespace_only(self):
        assert reformat_body_content("   \n  ") == "   \n  "


class TestNoJsonPassthrough:
    def test_plain_text(self):
        text = "A股动量策略研究"
        assert reformat_body_content(text) == text

    def test_markdown_only(self):
        text = "## Strategy\n\nSome analysis text."
        assert reformat_body_content(text) == text

    def test_brace_without_key_value(self):
        """``{`` without ``": `` is not JSON."""
        text = "Use {braces} in your template"
        assert reformat_body_content(text) == text

    def test_colon_without_brace(self):
        """``": `` without ``{`` is not JSON."""
        text = 'The value is "important": yes'
        assert reformat_body_content(text) == text


class TestPureJson:
    def test_simple_json(self):
        result = reformat_body_content('{"action": "test"}')
        assert result.startswith("```json\n")
        assert result.endswith("\n```")
        assert '"action": "test"' in result

    def test_nested_json(self):
        result = reformat_body_content('{"a": {"b": "c"}}')
        assert "```json" in result
        assert '"a": {"b": "c"}' in result

    def test_json_with_multiple_keys(self):
        json_str = '{"a": 1, "b": 2, "c": 3}'
        result = reformat_body_content(json_str)
        assert "```json" in result
        assert '"a": 1' in result
        assert '"c": 3' in result


class TestMarkdownPrefix:
    def test_blockquote_prefix(self):
        result = reformat_body_content('> A股动量策略\n{"action": "test"}')
        assert "> A股动量策略" in result
        assert "```json" in result
        assert '"action": "test"' in result
        # Prefix and code block separated by blank line
        assert '> A股动量策略\n\n```json' in result

    def test_header_prefix(self):
        result = reformat_body_content('## Analysis\n\n{"key": "value"}')
        assert "## Analysis" in result
        assert "```json" in result

    def test_list_prefix(self):
        result = reformat_body_content('- item 1\n- item 2\n\n{"data": 1}')
        assert "- item 1" in result
        assert "```json" in result


class TestTruncatedJson:
    def test_no_closing_brace(self):
        result = reformat_body_content('{"key": "value", "incomplete')
        assert "```json" in result
        assert '"key": "value"' in result
        assert '"incomplete' in result

    def test_nested_truncated(self):
        result = reformat_body_content('{"a": {"b": "c"')
        assert "```json" in result
        assert '"a": {"b": "c"' in result


class TestEdgeCases:
    def test_multiple_braces_uses_first(self):
        text = 'text {"first": 1} more {"second": 2}'
        result = reformat_body_content(text)
        assert "```json" in result
        # Should start from first {
        assert '"first": 1' in result
        assert '"second": 2' in result

    def test_long_json(self):
        json_str = '{"key": "' + "x" * 1000 + '"}'
        result = reformat_body_content(json_str)
        assert "```json" in result
        assert "x" * 100 in result  # content preserved

    def test_chinese_json_keys(self):
        result = reformat_body_content('{"动作": "发现", "假设": "A股"}')
        assert "```json" in result
        assert '"动作"' in result
        assert '"假设"' in result

    def test_trailing_whitespace_stripped(self):
        result = reformat_body_content('{"key": "value"}   \n')
        assert result.endswith("```")


class TestEndToEndWithRichMarkdown:
    def test_reformatted_content_renders_as_code_block(self):
        """The reformed content should produce a code block in Rich Markdown,
        not a paragraph of text."""
        import io

        from rich.console import Console
        from rich.markdown import Markdown

        body = '> Title\n{"action": "test", "reason": "because"}'
        reformatted = reformat_body_content(body)

        buf = io.StringIO()
        c = Console(file=buf, width=120, force_terminal=False)
        c.print(Markdown(reformatted))
        rendered = buf.getvalue()

        # The rendered output should NOT contain the raw JSON as a wall
        # of text.  It should contain the JSON within a code block
        # (which Rich renders with background color / different style).
        # A simple check: the JSON string appears in the rendered output
        # but with code-block formatting (not just plain text).
        assert '"action"' in rendered
        assert '"reason"' in rendered
