"""Tests for the DSML pseudo-tool-call stripper.

DeepSeek-V4-Flash (and similar reasoning models served via SiliconFlow
or the official DeepSeek API) sometimes leak ``<tools>...</tools>`` or
``[DSML | tool_calls>...]`` blocks into ``reasoning_content`` /
``content`` to *express intent* to call a tool. The real tool call
travels through the structured ``delta.tool_calls`` path, so these
in-text blocks are pure UI noise.

Coverage:
* :class:`_dsml_patterns.strip_dsml_text` — regex behaviour.
* :class:`SiliconFlowAdapter` / :class:`DeepSeekAdapter` — override is
  wired and cleans both ``delta.reasoning_content`` and
  ``delta.content``.
* Other providers (``openai`` / ``qwen`` / ``kimi`` / ``minimax``) —
  default base passthrough is preserved (no accidental stripping of
  legitimate model output).
"""
from __future__ import annotations

import pytest

from strategy_research.foundation.llm.provider import (
    DeepSeekAdapter,
    MiniMaxAdapter,
    OpenAIAdapter,
    QwenAdapter,
    SiliconFlowAdapter,
)
from strategy_research.foundation.llm.provider._dsml_patterns import (
    _DSML_BLOCK_RE,
    _DSML_OPEN_RE,
    _DSML_TOKEN_RE,
    strip_dsml_text,
)

# ── strip_dsml_text regex coverage ────────────────────────────────


class TestStripDsmlText:
    def test_empty_string_passthrough(self):
        assert strip_dsml_text("") == ""

    def test_none_or_undefined_inputs_are_safe(self):
        # Defensive — callers (parser.py, siliconflow.py) only feed us
        # strings, but a None-equivalent should still behave well.
        for val in (None, ""):
            assert strip_dsml_text(val or "") == ""

    def test_plain_text_passthrough(self):
        text = "你好！欢迎来到 QuantNodes-Research。"
        assert strip_dsml_text(text) == text

    def test_closed_tools_block_removed(self):
        text = (
            "先看看工作区。\n"
            "<tools>\n"
            "  <invoke name=\"list_files\">\n"
            "    <parameter name=\"workspace\" string=\"true\">/home/ll/qn-research</parameter>\n"
            "  </invoke>\n"
            "</tools>\n"
            "继续。"
        )
        result = strip_dsml_text(text)
        assert "<tools>" not in result
        assert "<invoke" not in result
        assert "</tools>" not in result
        assert "继续。" in result
        assert "先看看工作区。" in result

    def test_tool_singular_block_also_removed(self):
        # Some DeepSeek variants emit <tool>...</tool> (singular)
        text = "before <tool><invoke name=\"x\"/></tool> after"
        result = strip_dsml_text(text)
        assert result == "before  after"

    def test_dsml_single_token_block_removed(self):
        # [DSML | tool_calls>...<] — single bracket form
        text = "先 [DSML | tool_calls>...</tool_call>"
        result = strip_dsml_text(text)
        assert "[DSML" not in result
        assert "先" in result

    def test_dsml_invoke_single_token(self):
        text = "begin [DSML | invoke name=\"x\">content<] end"
        result = strip_dsml_text(text)
        assert "[DSML" not in result
        assert "begin" in result
        assert "end" in result

    def test_unclosed_tools_truncated_at_opening(self):
        # Model started emitting a tools block but stream ended — the
        # block is unfinished. Drop everything from the opening tag.
        text = "thinking prefix <tools><invoke name=\"half"
        result = strip_dsml_text(text)
        assert "<tools>" not in result
        assert "<invoke" not in result
        assert result == "thinking prefix"

    def test_unclosed_dsml_truncated(self):
        text = "before [DSML | tool_calls>"
        result = strip_dsml_text(text)
        assert "[DSML" not in result
        assert result == "before"

    def test_mixed_closed_and_open_block(self):
        # The rare case where a *closed* block sits in front of an
        # unclosed one. Both must be cleaned.
        text = "a <tools>x</tools> b <tools><invoke name=\"y"
        result = strip_dsml_text(text)
        assert "<tools>" not in result
        assert "<invoke" not in result
        assert "a" in result and "b" in result

    def test_case_insensitive(self):
        # DeepSeek has been seen emitting <TOOLS> (uppercase) and
        # <Tools> (titlecase) in different sessions.
        for variant in ("<TOOLS>x</TOOLS>", "<Tools>x</Tools>", "<tools>X</tools>"):
            assert "<" not in strip_dsml_text(f"pre {variant} post").lower().replace(
                "pre ", "").replace(" post", ""), (
                f"variant {variant!r} not stripped"
            )

    def test_idempotent(self):
        # Running twice should have no additional effect.
        text = "a <tools>x</tools> b"
        once = strip_dsml_text(text)
        twice = strip_dsml_text(once)
        assert once == twice

    def test_real_world_snippet_from_user_screenshot(self):
        # Reproduces the actual shape we saw leaked into user UI.
        text = (
            "你好！我是 QuantNodes-Research 的量化金融助手。很高兴为你服务。"
            "让我先探索一下工作区的当前状态，看看有哪些可用的策略、工具和数据。"
            "<|DSML|tool_calls><|DSML|invoke name=\"list_files\"><|DSML|parameter name=\"workspace\""
            "string=\"true\">/home/ll/Public/qn-research</|DSML|parameter><|DSML|invoke><|DSML|invoke "
            "name=\"list_skills\"><|DSML|parameter name=\"workspace\"string=\"true\">"
            "/home/ll/Public/qn-research</|DSML|parameter><|DSML|invoke><|DSML|tool_calls>"
        )
        result = strip_dsml_text(text)
        # Both `<|DSML|...<` (single-token variant) and the block form
        # must be gone.
        assert "<|DSML|" not in result
        assert "DSML" not in result
        # The user-visible greeting and intent text is preserved.
        assert "QuantNodes-Research" in result
        assert "策略、工具和数据" in result

    def test_full_width_pipes_observed_in_production(self):
        # Verified in event_log — d8d58926-... session:
        #   thinking_delta seq=147 delta='｜DSML｜'
        # These are emitted as individual full-width tokens during
        # streaming; the stripper must drop every occurrence even
        # when no enclosing block exists.
        text = "先看看工作区。｜DSML｜继续。"
        result = strip_dsml_text(text)
        assert "DSML" not in result
        assert result == "先看看工作区。继续。"

    def test_mixed_full_width_and_half_width_pipes(self):
        # In one reasoning block the model may emit both variants.
        text = "a ｜DSML｜ b <|DSML|tool_calls> c |DSML| d"
        result = strip_dsml_text(text)
        assert "DSML" not in result
        # Whitespace between tokens should collapse naturally via strip().
        for fragment in ("a", "b", "c", "d"):
            assert fragment in result


# ── Provider override coverage ─────────────────────────────────────


class TestSiliconFlowDsml:
    def test_reasoning_content_stripped(self):
        a = SiliconFlowAdapter()
        # reasoning_content DSML is cleaned inside extract_thinking_from_delta
        # (pipeline Step 3), not sanitize_delta.
        out = a.extract_thinking_from_delta(
            {"reasoning_content": "pre <tools>x</tools> post", "content": "x"}
        )
        assert "<tools>" not in out
        assert out == "pre post"  # normalize compresses leftover double space
        # sanitize_delta only touches the delivered `content` field.
        out2 = a.sanitize_delta(
            {"reasoning_content": "pre <tools>x</tools> post", "content": "x"}
        )
        assert out2["reasoning_content"] == "pre <tools>x</tools> post"
        assert out2["content"] == "x"

    def test_content_stripped(self):
        a = SiliconFlowAdapter()
        out = a.sanitize_delta(
            {"content": "hello [DSML | tool_calls>x<] world"}
        )
        assert "[DSML" not in out["content"]
        assert "hello" in out["content"]
        assert "world" in out["content"]

    def test_returns_new_dict(self):
        # Implementation contract: must NOT mutate the input delta.
        a = SiliconFlowAdapter()
        original = {"reasoning_content": "pre <tools>x</tools> post"}
        a.sanitize_delta(original)
        assert original["reasoning_content"] == "pre <tools>x</tools> post"

    def test_no_dsml_passthrough(self):
        a = SiliconFlowAdapter()
        out = a.sanitize_delta({"reasoning_content": "clean text", "content": "also clean"})
        assert out == {"reasoning_content": "clean text", "content": "also clean"}

    def test_message_level(self):
        a = SiliconFlowAdapter()
        # reasoning_content DSML cleaned in extract_thinking_from_message.
        out = a.extract_thinking_from_message(
            {"reasoning_content": "x <tools>y</tools> z", "content": "plain"}
        )
        assert "<tools>" not in out
        out2 = a.sanitize_message(
            {"reasoning_content": "x <tools>y</tools> z", "content": "plain"}
        )
        assert out2["reasoning_content"] == "x <tools>y</tools> z"
        assert out2["content"] == "plain"

    def test_non_string_fields_pass_through(self):
        a = SiliconFlowAdapter()
        out = a.sanitize_delta({"tool_calls": [{"id": "x"}], "role": "assistant"})
        # Non-text fields untouched, identity preserved.
        assert out["tool_calls"] == [{"id": "x"}]
        assert out["role"] == "assistant"


class TestDeepSeekDsml:
    def test_reasoning_content_stripped(self):
        a = DeepSeekAdapter()
        out = a.extract_thinking_from_delta(
            {"reasoning_content": "thinking <tools><invoke name=\"x\"/></tools> done"}
        )
        assert "<tools>" not in out
        assert "thinking" in out
        assert "done" in out

    def test_message_level(self):
        a = DeepSeekAdapter()
        out = a.extract_thinking_from_message(
            {"reasoning_content": "a [DSML | tool_calls>inner<] b"}
        )
        assert "[DSML" not in out


class TestNonDeepSeekProvidersAreNotAffected:
    """Critical regression test: other providers must NOT strip DSML.

    The DSML leakage is a DeepSeek reasoning-model quirk. OpenAI /
    Qwen / Kimi / MiniMax don't emit these blocks; if their
    ``reasoning_content`` happens to contain the literal string
    ``<tools>`` for any other reason, we must NOT touch it.
    """

    @pytest.mark.parametrize(
        "adapter_cls",
        [OpenAIAdapter, QwenAdapter, MiniMaxAdapter],
    )
    def test_passthrough_on_plain_text(self, adapter_cls):
        a = adapter_cls()
        text = "I will use <some_tag> here"
        out = a.sanitize_delta({"reasoning_content": text})
        # Base default is a defensive copy — the field value is
        # preserved unchanged (not the same object, but equal).
        assert out["reasoning_content"] == text

    def test_minimax_still_strips_its_own_think_tags_via_default_passthrough(
        self,
    ):
        # MiniMax's <think> handling is a separate concern; the DSML
        # default passthrough must not interfere with that.
        a = MiniMaxAdapter()
        text = "<think>plan</think>answer"
        out = a.sanitize_delta({"reasoning_content": text})
        # The DSML stripper (passthrough) leaves it alone — MiniMax's
        # own ``strip_thinking_from_delta`` is a separate hook.
        assert out["reasoning_content"] == text


# ── Regex sanity (spot-check the compiled patterns directly) ────────


class TestStreamDsmlFixer:
    """Cross-chunk DSML state machine (fix_delta hook).

    DeepSeek streams one BPE token per SSE chunk; markup tags arrive
    split across chunks (``<`` + ``tool`` + ``_c`` + ``alls``), which
    per-chunk regex can never match. The fixer buffers partial markers
    and drops whole blocks across chunk boundaries.
    """

    @staticmethod
    def _run(tokens: list[str]) -> str:
        from strategy_research.foundation.llm.provider._dsml_patterns import (
            StreamDsmlFixer,
        )

        fixer = StreamDsmlFixer()
        return "".join(fixer.fix(t) for t in tokens)

    def test_tokenized_full_block_dropped(self) -> None:
        tokens = [
            "<", "tool", "_c", "alls", "> ", "<", "inv", "oke", " name",
            '="', "list", "_files", '"> ', "<", "parameter", " name",
            '="', "works", "pace", '">', "/", "</", "invoke", "> ",
            "</", "tool", "_calls", ">",
        ]
        assert self._run(tokens) == ""

    def test_half_marker_not_swallowed(self) -> None:
        # "<" + "the" is NOT a marker prefix (only "<"-leading markers
        # buffer); "<the" must be emitted once it can't grow.
        assert self._run(["<", "the", " tool is great"]) == "<the tool is great"
        assert self._run(["use", " the", " <", "the", " tool"]) == "use the <the tool"

    def test_nested_closes_do_not_exit(self) -> None:
        tokens = [
            "<", "tool_calls", ">", "<parameter", ">", "x", "</parameter>",
            " ", "<", "/", "invoke", ">", " ", "<", "/", "tool", "_calls", ">",
        ]
        assert self._run(tokens) == ""

    def test_recovery_after_close(self) -> None:
        tokens = [
            "<", "tool", "_calls", ">", "DROP", "</", "tool", "_calls",
            ">", " Let", " me", " explore",
        ]
        assert self._run(tokens) == " Let me explore"

    def test_single_chunk_self_closed_block(self) -> None:
        assert self._run(["<tool_calls>x</tool_calls> rest"]) == " rest"
        # Text before a block in the same chunk survives.
        assert self._run(["pre text <tools>a</tools> post"]) == "pre text  post"

    def test_unclosed_marker_drops_to_end(self) -> None:
        # <|DSML| … < unclosed to stream end: trailing "<" held back,
        # never leaked.
        assert self._run(["a", "<|DSML|", "tool_calls", ">", "leak", "<"]) == "a"

    def test_multiple_blocks_keep_middle_text(self) -> None:
        assert self._run(
            ["<tools>a</tools>", " mid ", "<tool>b</tool>"],
        ) == " mid "

    def test_plain_thinking_unaffected(self) -> None:
        assert self._run(["Let", " me", " explore"]) == "Let me explore"

    def test_non_dsml_tag_not_matched(self) -> None:
        # <think> is a MiniMax concern, not DSML — never buffered/dropped.
        assert self._run(["<t", "hink>", "x"]) == "<think>x"

    def test_short_marker_wait_for_longer(self) -> None:
        # "<tool" could grow into "<tool_calls" — must NOT open discard
        # mode until confirmed.
        assert self._run(["<", "tool", "ing guide"]) == "<tooling guide"

    def test_adapter_fix_delta_per_field(self) -> None:
        from strategy_research.foundation.llm.provider import DeepSeekAdapter

        a = DeepSeekAdapter()
        # reasoning_content with a tokenized block; content with plain text.
        out = a.fix_delta({
            "reasoning_content": "think <tool_calls>x</tool_calls> end",
            "content": "answer",
        })
        assert out["reasoning_content"] == "think  end"
        assert out["content"] == "answer"

    def test_default_passthrough_for_other_providers(self) -> None:
        from strategy_research.foundation.llm.provider import MiniMaxAdapter

        a = MiniMaxAdapter()
        delta = {"content": " x ", "reasoning_content": " y "}
        assert a.fix_delta(delta) == delta


class TestRawFieldsPersistOriginal:
    """P3: StreamChunk carries raw (pre-cleanup) text for event_log."""

    def test_raw_thinking_preserved(self) -> None:
        from strategy_research.foundation.llm.parser import _chunk_from_dict
        from strategy_research.foundation.llm.provider import DeepSeekAdapter

        payload = {
            "choices": [{
                "delta": {
                    "reasoning_content": "think <tools>x</tools> end",
                    "content": "answer",
                },
            }],
        }
        chunk = _chunk_from_dict(payload, adapter=DeepSeekAdapter())
        assert chunk is not None
        # delta is cleaned; raw keeps the original model output.
        assert "<tools>" not in chunk.delta_thinking
        assert chunk.raw_thinking == "think <tools>x</tools> end"
        assert chunk.raw_content == "answer"

    def test_raw_equals_cleaned_when_no_markup(self) -> None:
        from strategy_research.foundation.llm.parser import _chunk_from_dict
        from strategy_research.foundation.llm.provider import DeepSeekAdapter

        payload = {
            "choices": [{"delta": {"reasoning_content": " me", "content": " x"}}],
        }
        chunk = _chunk_from_dict(payload, adapter=DeepSeekAdapter())
        assert chunk is not None
        assert chunk.raw_thinking == " me"
        assert chunk.delta_thinking == " me"
        assert chunk.raw_content == " x"
        assert chunk.delta_content == " x"


class TestCompiledPatterns:
    def test_block_re_matches_known_shapes(self):
        for text in [
            "<tools><invoke name=\"x\"/></tools>",
            "<TOOLS><invoke name=\"x\"/></TOOLS>",
            "<tool><invoke name=\"x\"/></tool>",
        ]:
            assert _DSML_BLOCK_RE.search(text), f"failed: {text!r}"

    def test_token_re_matches_all_known_dsml_variants(self):
        for text in [
            "<|DSML|tool_calls>",
            "<|DSML|invoke name=\"x\">",
            "<|DSML|parameter name=\"workspace\">",
            "｜DSML｜",
            "|DSML|",
            "[DSML | tool_calls>x<]",
        ]:
            assert _DSML_TOKEN_RE.search(text), f"failed: {text!r}"

    def test_open_re_finds_opening_tokens(self):
        for text in [
            "before <tools>unclosed",
            "before <|DSML|tool_calls>unclosed",
            "before [DSML | tool_calls>unclosed",
            "before ｜DSML｜unclosed",
            "before <TOOLS>UPPERCASE",
        ]:
            assert _DSML_OPEN_RE.search(text), f"failed: {text!r}"
