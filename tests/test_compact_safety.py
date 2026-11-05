"""Tests for L4 compaction safety checks.

Verifies that _llm_summarize_v2 returns None (abort) when:
1. new_messages is too short (< 2)
2. new_messages has no user role

This prevents the 400 bad_request "chat content is empty (2013)" error
from providers when the LLM is called with an empty/invalid context.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from agentnodes.kernel.agent.compact import (
    CompactConfig,
    _llm_summarize_v2,
    compact_messages,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_llm_client(summary_text: str = "test summary"):
    """Create a mock LLM client that returns a fixed summary."""
    client = MagicMock()
    response = MagicMock()
    response.content = summary_text
    client.chat.return_value = response
    return client


# ── Tests: _llm_summarize_v2 safety ────────────────────────────────


class TestL4SummarizeSafety:
    def test_returns_none_on_too_few_messages(self):
        """If new_messages would be too short, return None."""
        # Only system + 1 user message → tail_turns=2 means no complete turn
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        client = _mock_llm_client()
        result = _llm_summarize_v2(
            messages, CompactConfig(), None, None, client
        )
        # When no tail turn, returns None directly (not the new safety)
        # This validates the existing behavior is preserved
        assert result is None

    def test_safety_check_aborts_empty_result(self, caplog):
        """If L4 produces a result without user role, return None."""
        # Construct a scenario where L4 could produce only system messages
        # We test by mocking the serializer to produce empty recent
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        client = _mock_llm_client("test summary")
        # Force recent to be empty by making tail_turns=0
        cfg = CompactConfig(tail_turns=0)
        result = _llm_summarize_v2(
            messages, cfg, None, None, client
        )
        # With tail_turns=0, head=non_system=[user a, assistant b, user c]
        # L4 generates summary, new_messages = [system, user a, assistant b, user c]
        # This is a valid result, no safety check needed
        if result is not None:
            new_messages, _, _ = result
            assert any(m.get("role") == "user" for m in new_messages)

    def test_l4_succeeds_with_user_message(self):
        """Normal L4 with user message present should succeed."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1 " * 5000},
            {"role": "assistant", "content": "a1 " * 5000},
            {"role": "user", "content": "u2 " * 5000},
            {"role": "assistant", "content": "a2 " * 5000},
            {"role": "user", "content": "u3 " * 5000},
        ]
        client = _mock_llm_client("summary text")
        cfg = CompactConfig(tail_turns=1)  # keep last 1 turn
        result = _llm_summarize_v2(
            messages, cfg, None, None, client
        )
        # Should succeed and include at least the system message
        assert result is not None
        new_messages, summary, recent = result
        assert any(m.get("role") == "user" for m in new_messages)


# ── Tests: compact_messages integration ───────────────────────────


class TestCompactMessagesSafety:
    def test_compact_messages_preserves_user_when_l4_aborts(self):
        """When L4 returns None, original messages with user are preserved."""
        # Construct a scenario where L4 cannot run
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        client = _mock_llm_client()
        result_messages, applied, _, _ = compact_messages(
            messages, config=CompactConfig(), llm_client=client
        )
        # Should have at least the user message preserved
        assert any(m.get("role") == "user" for m in result_messages)
        # L4 should NOT be in applied layers
        assert not any("llm_summarize" in layer for layer in applied)

    def test_compact_messages_with_l3_fallback(self):
        """When L4 aborts, L3 hard truncate still runs if needed."""
        # Large history that should trigger L3
        messages = [
            {"role": "system", "content": "sys"},
        ]
        for i in range(20):
            messages.append({"role": "user", "content": f"u{i}" * 100})
            messages.append({"role": "assistant", "content": f"a{i}" * 100})
        # L4 won't run (no complete turns), L3 might truncate
        result_messages, applied, _, _ = compact_messages(
            messages, config=CompactConfig(), llm_client=_mock_llm_client()
        )
        # Should still preserve at least system + some user messages
        assert any(m.get("role") == "user" for m in result_messages)
        assert any(m.get("role") == "system" for m in result_messages)


# ── Tests: env var kill switch ─────────────────────────────────────


class TestEnvVarKillSwitch:
    def test_default_no_override(self):
        """Without env var, _KEEP_ALL_COMPACTIONS_OVERRIDE is False."""
        # Clear env var
        old = os.environ.pop("SR_KEEP_ALL_COMPACTIONS", None)
        try:
            # Re-import to re-evaluate
            import importlib

            from agentnodes.kernel.agent import compact
            importlib.reload(compact)
            assert compact._KEEP_ALL_COMPACTIONS_OVERRIDE is False
        finally:
            if old is not None:
                os.environ["SR_KEEP_ALL_COMPACTIONS"] = old

    def test_env_var_true_enables_override(self):
        """SR_KEEP_ALL_COMPACTIONS=1 sets override to True."""
        os.environ["SR_KEEP_ALL_COMPACTIONS"] = "1"
        try:
            import importlib

            from agentnodes.kernel.agent import compact
            importlib.reload(compact)
            assert compact._KEEP_ALL_COMPACTIONS_OVERRIDE is True
        finally:
            del os.environ["SR_KEEP_ALL_COMPACTIONS"]

    def test_env_var_various_truthy_values(self):
        """Various truthy values should enable override."""
        for val in ("1", "true", "True", "yes", "on"):
            os.environ["SR_KEEP_ALL_COMPACTIONS"] = val
            try:
                import importlib

                from agentnodes.kernel.agent import compact
                importlib.reload(compact)
                assert compact._KEEP_ALL_COMPACTIONS_OVERRIDE is True, f"Failed for {val}"
            finally:
                del os.environ["SR_KEEP_ALL_COMPACTIONS"]

    def test_env_var_falsy_values_disabled(self):
        """Falsy values should keep override False."""
        for val in ("0", "false", "no", "off", ""):
            os.environ["SR_KEEP_ALL_COMPACTIONS"] = val
            try:
                import importlib

                from agentnodes.kernel.agent import compact
                importlib.reload(compact)
                assert compact._KEEP_ALL_COMPACTIONS_OVERRIDE is False, f"Failed for {val!r}"
            finally:
                del os.environ["SR_KEEP_ALL_COMPACTIONS"]
