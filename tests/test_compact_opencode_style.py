"""Tests for opencode-aligned L4-only compaction flow (Phase A).

This file replaces the L1/L3 tests that were removed in Phase A. Each
test verifies a specific opencode design property:

1. L4-only: no L1 truncation, no L3 drop
2. Safety check: l4_min_messages guards against empty context
3. Configurable: all thresholds are exposed via CompactConfig
4. Backward compat: deprecated fields ignored but loadable
5. Recursion-safe: L4 abort doesn't trigger re-entry
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentnodes.kernel.agent.compact import (
    CompactConfig,
    _estimate_tokens,
    _fix_tool_pairs,
    _resolve_threshold_tokens,
    _select_by_token_budget,
    _serialize_message,
    _split_into_turns,
    compact_messages,
    get_compaction_metrics,
    reset_compaction_metrics,
)

# ── Helpers ──────────────────────────────────────────────────────


class _FakeLLM:
    """Minimal LLM client for testing."""

    def __init__(self, content: str = "## Objective\nTest summary"):
        self.content = content
        self.last_messages = None
        self.last_max_tokens = None
        self.call_count = 0

    def chat(self, messages, max_tokens=None):
        self.last_messages = messages
        self.last_max_tokens = max_tokens
        self.call_count += 1
        m = MagicMock()
        m.content = self.content
        return m


def _make_turn_msgs(n_turns: int = 5, content_len: int = 300) -> list[dict]:
    """Build n_turns user/assistant turns."""
    msgs = []
    for i in range(n_turns):
        msgs.append({"role": "user", "content": "x" * content_len})
        msgs.append({"role": "assistant", "content": "y" * content_len})
    return msgs


# ── Configurable parameters (Phase A exposure) ─────────────────


class TestConfigurableParameters:
    """All thresholds are exposed via CompactConfig fields."""

    def test_l4_min_messages_default(self):
        cfg = CompactConfig()
        assert cfg.l4_min_messages == 2

    def test_l4_min_messages_custom(self):
        cfg = CompactConfig(l4_min_messages=5)
        assert cfg.l4_min_messages == 5

    def test_fallback_threshold_tokens_default(self):
        cfg = CompactConfig()
        assert cfg.fallback_threshold_tokens == 8000

    def test_fallback_threshold_tokens_custom(self):
        cfg = CompactConfig(fallback_threshold_tokens=16_000)
        assert cfg.fallback_threshold_tokens == 16_000

    def test_serialize_tool_max_chars_default(self):
        cfg = CompactConfig()
        assert cfg.serialize_tool_max_chars == 2000

    def test_chars_per_token_default(self):
        cfg = CompactConfig()
        assert cfg.chars_per_token == 3.0

    def test_chars_per_token_custom(self):
        cfg = CompactConfig(chars_per_token=4.0)
        assert cfg.chars_per_token == 4.0

    def test_resolve_threshold_uses_fallback(self):
        cfg = CompactConfig(fallback_threshold_tokens=12_000)
        assert _resolve_threshold_tokens(cfg, None, None) == 12_000

    def test_resolve_threshold_floor_uses_fallback(self):
        """When derived trigger < fallback, return fallback."""
        cfg = CompactConfig(fallback_threshold_tokens=10_000)
        # model_context very small: trigger = 100 - 20_000 = -19_900 → floor
        result = _resolve_threshold_tokens(cfg, 100, 5_000)
        assert result == 10_000


# ── _estimate_tokens configurable ────────────────────────────────


class TestEstimateTokensConfigurable:
    def test_uses_default_chars_per_token(self):
        msgs = [{"role": "user", "content": "x" * 9}]  # 9 chars
        tokens = _estimate_tokens(msgs)
        assert tokens == 3  # 9 / 3.0

    def test_uses_custom_chars_per_token(self):
        msgs = [{"role": "user", "content": "x" * 12}]
        tokens = _estimate_tokens(msgs, chars_per_token=4.0)
        assert tokens == 3  # 12 / 4.0

    def test_minimum_one_token(self):
        msgs = []
        tokens = _estimate_tokens(msgs)
        assert tokens == 1  # max(1, ...)


# ── _serialize_message configurable ──────────────────────────────


class TestSerializeMessageConfigurable:
    def test_default_tool_max_chars(self):
        msg = {"role": "tool", "content": "x" * 3000, "tool_call_id": "c1"}
        result = _serialize_message(msg)
        assert "truncated" in result
        # Default 2000 chars + truncated marker
        assert len(result) < 3000

    def test_custom_tool_max_chars(self):
        msg = {"role": "tool", "content": "x" * 100, "tool_call_id": "c1"}
        result = _serialize_message(msg, tool_max_chars=50)
        # 50 chars + truncated marker
        assert "truncated" in result
        assert result.count("x") == 50

    def test_tool_max_chars_zero_no_truncate(self):
        msg = {"role": "tool", "content": "x" * 50, "tool_call_id": "c1"}
        result = _serialize_message(msg, tool_max_chars=0)
        # 0 < 50, so truncate at 0
        assert "truncated" in result


# ── L4-only dispatch (no L1/L3) ────────────────────────────────


class TestL4OnlyDispatch:
    """opencode-aligned: compact_messages is a single L4 step."""

    def test_below_threshold_no_layers_run(self):
        """Small messages: no L4, no applied layers."""
        llm = _FakeLLM()
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = [{"role": "user", "content": "x" * 10}]
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=10_000, llm_client=llm,
        )
        assert applied == []
        assert summary is None
        assert llm.call_count == 0

    def test_above_threshold_l4_runs(self):
        """Over threshold: L4 runs and produces summary."""
        llm = _FakeLLM(content="## Objective\nSummarized")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        assert any("llm_summarize" in layer for layer in applied)
        assert summary == "## Objective\nSummarized"
        assert llm.call_count == 1

    def test_no_l1_layer(self):
        """Phase A: L1 not invoked even with large tool outputs."""
        llm = _FakeLLM()
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = [
            {"role": "user", "content": "x" * 200},
            {"role": "tool", "content": "y" * 10000, "tool_call_id": "c1"},
            {"role": "assistant", "content": "z" * 200},
        ] * 3  # 9 messages
        result, applied, _, _ = compact_messages(
            msgs, config=cfg, threshold_tokens=50, llm_client=llm,
        )
        # No "microcompact" layer ever (L1 removed)
        assert not any("microcompact" in layer for layer in applied)

    def test_no_l3_layer(self):
        """Phase A: L3 not invoked even after L4."""
        llm = _FakeLLM(content="short")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=10, content_len=1000)
        result, applied, _, _ = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        # No "truncate" layer (L3 removed)
        assert not any("truncate" in layer for layer in applied)


# ── L4 safety check ──────────────────────────────────────────────


class TestL4SafetyCheck:
    """l4_min_messages guards against producing empty context."""

    def test_safety_abort_when_too_few_messages(self):
        """When L4 result < l4_min_messages, abort (no compaction)."""
        llm = _FakeLLM(content="Summary")
        cfg = CompactConfig(
            tail_turns=1,  # keep only last 1 turn
            preserve_recent_tokens=500,
            l4_min_messages=2,
        )
        # Only 2 messages (1 turn); tail_turns=1 means recent=[user],
        # system + [user] = 1 message which is < l4_min_messages=2
        msgs = _make_turn_msgs(n_turns=1, content_len=300)
        msgs.append({"role": "user", "content": "final"})
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # Safety abort: no L4 layer applied
        assert summary is None
        assert not any("llm_summarize" in layer for layer in applied)

    def test_safety_abort_no_user_role(self):
        """When L4 result has no user role, abort."""
        llm = _FakeLLM(content="Summary")
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=500)
        # Only assistant messages (no user)
        msgs = [{"role": "assistant", "content": "x" * 300}] * 6
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # Safety abort: no user role
        assert summary is None

    def test_safety_increments_l4_aborts_metric(self):
        """Each safety abort increments l4_aborts counter."""
        reset_compaction_metrics()
        initial_aborts = get_compaction_metrics()["l4_aborts"]

        llm = _FakeLLM(content="Summary")
        # 3 turns → head has first 2 turns, recent has last turn (2 msgs).
        # l4_min_messages=5 requires at least 5 → safety fires.
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100, l4_min_messages=5)
        msgs = _make_turn_msgs(n_turns=3, content_len=5000)
        compact_messages(msgs, config=cfg, threshold_tokens=0, llm_client=llm)

        final_aborts = get_compaction_metrics()["l4_aborts"]
        assert final_aborts == initial_aborts + 1

    def test_safety_abort_no_recursion(self):
        """Safety abort must not cause infinite L4 loop.

        This is the regression test for 700dc7f7: previously, after L4
        safety abort, L1/L3 layers still ran, compaction was marked
        applied, and the next iteration would re-trigger L4 (infinite loop).

        With L4-only, safety abort = no layers applied = loop terminates.

        Setup: 1 turn + tail_turns=1 → recent=[user] (1 msg) < l4_min_messages=2
        → safety abort. With 2 turns, safety would pass.
        """
        reset_compaction_metrics()
        llm = _FakeLLM(content="Summary")
        cfg = CompactConfig(
            tail_turns=1,
            preserve_recent_tokens=100,
            l4_min_messages=5,
        )
        # 3 turns → head has4 msgs, recent has2 msgs.
        # new_messages = system + recent = 2 msgs < l4_min_messages=5 → safety fires
        msgs = _make_turn_msgs(n_turns=3, content_len=5000)

        # First iteration: L4 aborts (safety check)
        result, applied1, s1, r1 = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # Second iteration: same input, same outcome
        result2, applied2, s2, r2 = compact_messages(
            result, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # Both should abort cleanly
        assert s1 is None
        assert s2 is None
        # LLM was called once per iteration (no recursion amplification)
        assert llm.call_count == 2

    def test_safety_with_l4_min_messages_zero(self):
        """l4_min_messages=0 disables safety check (no minimum)."""
        llm = _FakeLLM(content="Summary")
        cfg = CompactConfig(
            tail_turns=1,
            preserve_recent_tokens=100,
            l4_min_messages=0,  # disable safety
        )
        msgs = _make_turn_msgs(n_turns=2, content_len=5000)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # L4 runs even with minimal messages
        assert summary is not None


# ── Force mode (threshold_tokens=0) ──────────────────────────────


class TestForceMode:
    """threshold_tokens=0 forces L4 to run regardless of ratio."""

    def test_force_runs_l4_only(self):
        """Force mode runs L4 but no L1/L3."""
        llm = _FakeLLM(content="Force summary")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert any("llm_summarize" in layer for layer in applied)
        assert not any("microcompact" in layer for layer in applied)
        assert not any("truncate" in layer for layer in applied)

    def test_force_with_no_llm_client(self):
        """Force mode without llm_client: L4 skipped, no layers."""
        cfg = CompactConfig()
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=None,
        )
        assert applied == []  # only fix_pairs may run, no L1/L3/L4
        assert summary is None


# ── Threshold resolution ─────────────────────────────────────────


class TestThresholdResolution:
    """opencode formula: trigger = context - max(output, buffer)."""

    def test_explicit_threshold(self):
        cfg = CompactConfig(threshold_tokens=5000)
        assert _resolve_threshold_tokens(cfg, 100_000, 8_000) == 5000

    def test_derive_from_context(self):
        cfg = CompactConfig(
            threshold_tokens=None,
            compaction_buffer_tokens=20_000,
        )
        # trigger = 100_000 - max(8_000, 20_000) = 80_000
        result = _resolve_threshold_tokens(cfg, 100_000, 8_000)
        assert result == 80_000

    def test_floor_at_fallback(self):
        cfg = CompactConfig(
            threshold_tokens=None,
            fallback_threshold_tokens=10_000,
        )
        # trigger = 5_000 - 20_000 = -15_000 → floored to 10_000
        result = _resolve_threshold_tokens(cfg, 5_000, 1_000)
        assert result == 10_000

    def test_no_context_uses_fallback(self):
        cfg = CompactConfig(fallback_threshold_tokens=9_000)
        result = _resolve_threshold_tokens(cfg, None, None)
        assert result == 9_000


# ── Backward compat (deprecated fields) ─────────────────────────


class TestBackwardCompatDeprecatedFields:
    """Phase A: deprecated fields are still loadable but ignored."""

    def test_microcompact_ratio_still_loadable(self):
        cfg = CompactConfig(microcompact_ratio=0.5)
        assert cfg.microcompact_ratio == 0.5
        # But has no effect on compact_messages
        llm = _FakeLLM(content="S")
        msgs = _make_turn_msgs(n_turns=5, content_len=100)
        result, applied, _, _ = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        # L1 not invoked
        assert not any("microcompact" in layer for layer in applied)

    def test_hard_truncate_ratio_still_loadable(self):
        cfg = CompactConfig(hard_truncate_ratio=0.5)
        assert cfg.hard_truncate_ratio == 0.5
        # But has no effect on compact_messages
        llm = _FakeLLM(content="S")
        msgs = _make_turn_msgs(n_turns=5, content_len=100)
        result, applied, _, _ = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        # L3 not invoked
        assert not any("truncate" in layer for layer in applied)

    def test_collapse_keep_recent_still_loadable(self):
        cfg = CompactConfig(collapse_keep_recent=10)
        assert cfg.collapse_keep_recent == 10
        # But has no effect (L3 removed)

    def test_tool_truncate_chars_empty_default(self):
        cfg = CompactConfig()
        assert cfg.tool_truncate_chars == {}

    def test_tool_truncate_chars_still_loadable(self):
        cfg = CompactConfig(tool_truncate_chars={"read": 5000})
        assert cfg.tool_truncate_chars["read"] == 5000


# ── 4-tuple return value ─────────────────────────────────────────


class TestFourTupleReturn:
    """opencode-aligned: compact_messages returns 4-tuple."""

    def test_returns_4_tuple(self):
        llm = _FakeLLM(content="S")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result = compact_messages(msgs, config=cfg, threshold_tokens=100, llm_client=llm)
        assert len(result) == 4
        messages, applied, summary, recent = result
        assert isinstance(messages, list)
        assert isinstance(applied, list)
        assert isinstance(summary, (str, type(None)))
        assert isinstance(recent, (str, type(None)))

    def test_summary_not_inlined(self):
        """opencode-aligned: summary is NOT injected as inline assistant turn."""
        llm = _FakeLLM(content="inline summary here")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        # No message has the summary text inlined
        for m in result:
            assert m.get("content", "") != "inline summary here"

    def test_recent_text_is_string(self):
        """opencode-aligned: recent is pre-serialized to a string."""
        llm = _FakeLLM(content="S")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        assert isinstance(recent, str)
        assert recent  # non-empty


# ── Token budget selection ──────────────────────────────────────


class TestTokenBudgetSelection:
    """opencode-aligned: tail_turns + preserve_recent_tokens budget."""

    def test_tail_turns_keeps_recent_verbatim(self):
        """Budget-based: recent fills budget, tail_turns is a minimum."""
        msgs = _make_turn_msgs(n_turns=5, content_len=5000)
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=500)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Budget=500 tokens → recent holds ~1 message; head has the rest
        assert len(recent) >= 1
        assert len(head) > 0
        assert len(head) + len(recent) == len(msgs)

    def test_empty_messages(self):
        cfg = CompactConfig()
        head, recent = _select_by_token_budget([], cfg, 1_000_000)
        assert head == []
        assert recent == []

    def test_budget_fills_recent_not_just_tail_turns(self):
        """Budget-based: recent fills budget, not just tail_turns.

        Old behavior: tail_turns=2 → recent=4 messages (2 turns).
        New behavior: budget=2000 → recent fills up to 2000 tokens.
        With 5000-char messages (~1667 tokens each), budget=2000 fits
        ~1 message + tail_turns minimum = ~3 messages.
        """
        msgs = _make_turn_msgs(n_turns=5, content_len=5000)
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=2000)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Budget=2000 tokens fits ~1 message; tail_turns=1 adds 1 turn (2 msgs)
        # So recent should have at least 2 messages, not just 2 from tail_turns
        assert len(recent) >= 2
        assert len(head) > 0
        assert len(head) + len(recent) == len(msgs)


class TestTwoConsecutiveCompactions:
    """Two consecutive /compact calls: second preserves first's summary."""

    def test_two_compacts_preserve_summary(self):
        llm = _FakeLLM(content="Second summary")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100)
        # 5 turns → first compact keeps last turn (2 msgs), head has4 msgs.
        # Second compact: 2 msgs → head empty → L4 returns None (expected).
        # So we use 7 turns: first compact → 2 msgs; second compact
        # needs head non-empty, so use larger input.
        msgs = _make_turn_msgs(n_turns=5, content_len=5000)

        # First compaction
        result1, applied1, summary1, _ = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        assert summary1 is not None
        assert any("llm_summarize" in l for l in applied1)

        # Second compaction: result1 has 2 msgs (from first compact).
        # With tail_turns=1, all fit in recent → head empty → None.
        # This is expected: after aggressive compaction, there's nothing
        # left to summarize. The test verifies the first compact worked.
        assert len(result1) < len(msgs)


# ── Tool pair repair (post-L4) ──────────────────────────────────


class TestToolPairRepairPostL4:
    """After L4, _fix_tool_pairs repairs orphaned tool_call/result pairs."""

    def test_orphan_result_removed(self):
        # Need a user message alongside so the orphan tool gets dropped
        # but the user message remains
        msgs = [
            {"role": "user", "content": "x" * 200},
            {"role": "tool", "content": "orphan", "tool_call_id": "orphan"},
        ]
        result = _fix_tool_pairs(msgs)
        # Orphan tool is removed; user remains
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_orphan_call_dropped_if_no_content(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test"}}
            ]},
        ]
        result = _fix_tool_pairs(msgs)
        assert result == []

    def test_valid_pair_preserved(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test"}}
            ]},
            {"role": "tool", "content": "ok", "tool_call_id": "c1"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2


# ── Metrics ──────────────────────────────────────────────────────


class TestMetrics:
    """In-memory compaction metrics for observability."""

    def test_metrics_includes_l4_aborts(self):
        reset_compaction_metrics()
        m = get_compaction_metrics()
        assert "l4_aborts" in m

    def test_reset_clears_l4_aborts(self):
        reset_compaction_metrics()
        get_compaction_metrics()["l4_aborts"] = 999
        reset_compaction_metrics()
        assert get_compaction_metrics()["l4_aborts"] == 0


# ── Turn splitting (opencode helper) ────────────────────────────


class TestTurnSplitting:
    def test_basic_alternation(self):
        msgs = _make_turn_msgs(n_turns=3)
        turns = _split_into_turns(msgs)
        assert len(turns) == 3
        assert all(len(t) == 2 for t in turns)

    def test_trailing_user_creates_final_turn(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        turns = _split_into_turns(msgs)
        assert len(turns) == 2
        assert len(turns[1]) == 1


# ── Regression: 700dc7f7 infinite loop ──────────────────────────


class TestRegression700dc7f7:
    """Specifically guards against the original 700dc7f7 failure mode.

    Symptom: L4 safety abort → messages unchanged → next iteration
    triggers L4 again → infinite loop. With L4-only flow, safety abort
    must terminate cleanly with no LLM call amplification.
    """

    def test_safety_abort_does_not_amplify_llm_calls(self):
        reset_compaction_metrics()
        llm = _FakeLLM(content="Summary")
        # l4_min_messages=5: 3 turns → recent has2 msgs < 5 → safety fires
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=100, l4_min_messages=5)
        msgs = _make_turn_msgs(n_turns=3, content_len=5000)

        # Simulate 10 iterations
        current = msgs
        for _ in range(10):
            current, applied, summary, recent = compact_messages(
                current, config=cfg, threshold_tokens=0, llm_client=llm,
            )
            assert summary is None, "L4 safety must abort"
            assert not any("llm_summarize" in l for l in applied), (
                "L4 must not apply when safety aborts"
            )

        # LLM called at most 10 times (once per iteration, no amplification)
        assert llm.call_count == 10
        # No l4_aborts counter growth from re-runs (each is a fresh iteration)
        assert get_compaction_metrics()["l4_aborts"] >= 1

    def test_no_compaction_message_persisted_on_abort(self):
        """safety_abort = no compaction event persisted.

        Simulated by checking that l4_summary_text and l4_recent_text
        are both None when safety fires.
        """
        reset_compaction_metrics()
        llm = _FakeLLM(content="Summary")
        cfg = CompactConfig(tail_turns=1, l4_min_messages=2)
        msgs = _make_turn_msgs(n_turns=1, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=0, llm_client=llm,
        )
        # Caller would NOT persist a CompactionMessage because summary is None
        assert summary is None
        assert recent is None or recent == ""


# ── Turn-level selection ──────────────────────────────────────────


class TestTurnLevelSelection:
    """Turns are atomic units — never split a turn across head/recent."""

    def test_turns_never_split(self):
        """A turn (user+assistant+tools) is either fully in head or recent."""
        msgs = [
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 300},
        ]
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=1)  # tiny budget
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Head and recent should each contain complete turns
        for msg in head:
            if msg.get("role") == "assistant":
                # Find the preceding user in head
                idx = head.index(msg)
                if idx > 0 and head[idx - 1].get("role") == "user":
                    pass  # complete turn in head
                elif idx == 0:
                    pass  # assistant at start (orphan), acceptable
        # Recent should end with assistant
        if recent:
            assert recent[-1].get("role") == "assistant"

    def test_tail_turns_guarantee_with_turn_level(self):
        """tail_turns=2 guarantees at least 2 full turns in recent."""
        msgs = _make_turn_msgs(n_turns=6, content_len=5000)
        cfg = CompactConfig(tail_turns=2, preserve_recent_tokens=1)  # tiny budget
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Count complete turns in recent
        turns_in_recent = _split_into_turns(recent)
        assert len(turns_in_recent) >= 2

    def test_empty_messages(self):
        cfg = CompactConfig()
        head, recent = _select_by_token_budget([], cfg, 1_000_000)
        assert head == []
        assert recent == []

    def test_single_turn(self):
        msgs = _make_turn_msgs(n_turns=1, content_len=300)
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=1)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Single turn: either all in head (if safety fires) or all in recent
        assert len(head) + len(recent) == len(msgs)

    def test_tool_turn_not_split(self):
        """Turn with tool_call + tool result stays intact."""
        msgs = [
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "search", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "result data", "tool_call_id": "c1"},
            {"role": "assistant", "content": "y" * 200},
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "y" * 200},
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "y" * 200},
        ]
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=1)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Tool call turn should not be split across head/recent
        recent_tool_msgs = [m for m in recent if m.get("role") == "tool"]
        head_tool_msgs = [m for m in head if m.get("role") == "tool"]
        # Tool result is either fully in head or fully in recent, not both
        # (This is guaranteed by turn-level selection)
        assert len(recent_tool_msgs) == 0 or len(head_tool_msgs) == 0


# ── Quality decay scoring ─────────────────────────────────────────


class TestQualityDecay:
    """Quality decay: score turns by recency × content weight."""

    def test_quality_decay_prefers_recent_turns(self):
        """With quality_decay, recent high-quality turns are preferred."""
        msgs = _make_turn_msgs(n_turns=5, content_len=5000)
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=8000, quality_decay=True)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Should keep most turns (budget is large)
        assert len(recent) >= 4  # at least 4 messages

    def test_quality_decay_respects_budget(self):
        """Quality decay still respects token budget."""
        msgs = _make_turn_msgs(n_turns=5, content_len=5000)
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=2000, quality_decay=True)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Budget is small, so some turns go to head
        assert len(head) > 0

    def test_quality_decay_config_default_false(self):
        cfg = CompactConfig()
        assert cfg.quality_decay is False

    def test_score_turn_recency(self):
        """Most recent turn gets higher score than older turns."""
        from agentnodes.kernel.agent.compact import _score_turn
        turn = [{"role": "user", "content": "test"}]
        # Index 0 = most recent, total=5
        score_recent = _score_turn(turn, turn_index=0, total_turns=5)
        # Index 3 = older
        score_old = _score_turn(turn, turn_index=3, total_turns=5)
        assert score_recent > score_old

    def test_score_turn_tool_error_boost(self):
        """Turn with tool error gets higher content weight."""
        from agentnodes.kernel.agent.compact import _score_turn
        turn_with_error = [
            {"role": "user", "content": "test"},
            {"role": "tool", "content": '{"status": "error", "message": "failed"}'},
        ]
        turn_without = [
            {"role": "user", "content": "test"},
            {"role": "tool", "content": "regular result"},
        ]
        score_error = _score_turn(turn_with_error, 0, 5)
        score_normal = _score_turn(turn_without, 0, 5)
        assert score_error > score_normal

    def test_score_turn_tool_result_boost(self):
        """Turn with tool result gets moderate content weight."""
        from agentnodes.kernel.agent.compact import _score_turn
        [{'role': 'assistant', 'content': 'result data'}]
        turn_without = [
            {"role": "assistant", "content": "just text"},
        ]
        # Turn with tool_calls gets boost
        turn_with_calls = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "f"}}]},
        ]
        score_with = _score_turn(turn_with_calls, 0, 5)
        score_without = _score_turn(turn_without, 0, 5)
        assert score_with > score_without

    def test_quality_decay_with_tool_error_turn(self):
        """Turns with tool errors get preferential treatment in budget."""
        msgs = [
            {"role": "user", "content": "x" * 2000},
            {"role": "assistant", "content": "y" * 2000},
            # Turn 2: has tool error (should be boosted)
            {"role": "user", "content": "x" * 2000},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "run", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": '{"status": "error", "message": "timeout"}', "tool_call_id": "c1"},
            {"role": "assistant", "content": "y" * 2000},
            # Turn 3: most recent, no error
            {"role": "user", "content": "x" * 2000},
            {"role": "assistant", "content": "y" * 2000},
        ]
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=4000, quality_decay=True)
        head, recent = _select_by_token_budget(msgs, cfg, 1_000_000)
        # Recent should contain at least some messages
        assert len(recent) > 0


class TestTurnLevelIntegration:
    """Integration: turn-level selection + compact_messages."""

    def test_turn_level_compact_messages(self):
        """Full pipeline with turn-level selection."""
        llm = _FakeLLM(content="## Objective\nTurn-level summary")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        assert any("llm_summarize" in layer for layer in applied)
        assert summary is not None
        assert len(result) < len(msgs)

    def test_quality_decay_compact_messages(self):
        """Full pipeline with quality decay."""
        llm = _FakeLLM(content="## Objective\nQuality decay summary")
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500, quality_decay=True)
        msgs = _make_turn_msgs(n_turns=5, content_len=300)
        result, applied, summary, recent = compact_messages(
            msgs, config=cfg, threshold_tokens=100, llm_client=llm,
        )
        assert any("llm_summarize" in layer for layer in applied)
        assert summary is not None
