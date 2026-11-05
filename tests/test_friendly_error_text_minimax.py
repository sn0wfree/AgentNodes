"""Tests for _friendly_error_text covering MiniMax 2013 and 400 invalid params.

These friendly error messages are shown to users in the error bubble
when LLM calls fail. They map raw provider error details to short
Chinese messages that explain what went wrong and what to do.
"""

from __future__ import annotations

from agentnodes.interfaces.api.session.service import _friendly_error_text


class TestFriendlyErrorTextMinimax:
    """MiniMax-specific error mappings."""

    def test_minimax_2013_chat_content_empty(self):
        """MiniMax 2013 'chat content is empty' → clear new-session hint."""
        result = _friendly_error_text(
            "LLMError: empty chat content (MiniMax 2013): "
            "{'type': 'bad_request_error', 'message': "
            "'invalid params, chat content is empty (2013)'}"
        )
        assert "会话内容已压缩为空" in result
        assert "新建会话" in result or "发送新消息" in result

    def test_400_invalid_params(self):
        """400 + invalid params → suggest retry or new session."""
        result = _friendly_error_text(
            "400 Bad Request: invalid params, malformed request"
        )
        assert "请求参数无效" in result
        assert "稍后重试" in result or "新建会话" in result

    def test_2013_code_in_error(self):
        """Just the code 2013 (without full message) → still recognized."""
        result = _friendly_error_text("Error code 2013 occurred")
        assert "会话内容已压缩为空" in result


class TestFriendlyErrorTextExisting:
    """Verify existing error mappings still work."""

    def test_rate_limit_429(self):
        result = _friendly_error_text("LLMRateLimitError: rate limited (429)")
        assert "频率过高" in result

    def test_timeout(self):
        result = _friendly_error_text("LLMTimeoutError: timed out after 60s")
        assert "超时" in result

    def test_auth(self):
        result = _friendly_error_text("LLMAuthError: auth failed (401)")
        assert "鉴权" in result

    def test_quota(self):
        result = _friendly_error_text("LLMQuotaError: quota exceeded")
        assert "配额" in result

    def test_server_error(self):
        result = _friendly_error_text("LLMServerError: 500 internal")
        assert "不可用" in result

    def test_unknown_error(self):
        result = _friendly_error_text("some random error")
        assert result.startswith("⚠️")

    def test_empty_input(self):
        result = _friendly_error_text("")
        assert result.startswith("⚠️")

    def test_none_input(self):
        result = _friendly_error_text(None)  # type: ignore
        assert result.startswith("⚠️")


class TestFriendlyErrorTextPriority:
    """Verify MiniMax 2013 takes priority over generic 400."""

    def test_2013_takes_priority_over_rate(self):
        """Even with 'rate' in body, 2013 pattern wins."""
        result = _friendly_error_text(
            "rate limited (429) and 2013 chat content is empty"
        )
        # 2013 check happens first per the code
        assert "会话内容已压缩为空" in result

    def test_400_with_2013_recognized(self):
        result = _friendly_error_text(
            "400 bad_request_error code 2013 message: chat content is empty"
        )
        assert "会话内容已压缩为空" in result
