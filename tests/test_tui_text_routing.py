"""Tests for TUI native streaming (text.started/delta/ended).

PR2 (tui-text-routing): the TUI subscribes to the opencode-style 3-step
text protocol in addition to the legacy text_delta + assistant_message
fallback. These tests cover route_agent_event dispatch for all relevant
paths.

Reference: docs/archive/undated/tui-text-routing.md
"""
from __future__ import annotations

import pytest

from agentnodes.interfaces.cli.tui.app import ResearchApp
from agentnodes.interfaces.cli.tui.widgets import TranscriptView

# ``skip_resume`` keeps on_mount from pushing the resume modal. These tests
# exercise the streaming routes, not session resume -- and since B4 Step 3
# the resume probe reads the unified DB, which can legitimately contain
# sessions, so leaving it on would block pilot.pause() behind a modal.
RESEARCHAPP_KW = {"model": "m", "version": "0.4.2", "skip_resume": True}


# ─────────────────────────────────────────────────────────────────────────────
# text.started / text.ended lifecycle
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_started_creates_streaming_session():
    """text.started -> begin_streaming_session -> _streamer is not None."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)
        assert tv._streamer is None

        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()

        assert tv._streamer is not None
        assert tv._stream_baseline is not None


@pytest.mark.asyncio
async def test_text_delta_appends_to_active_streamer():
    """text_delta without an active streamer auto-starts one (legacy fallback)."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        # No text.started — simulate old backend that only emits text_delta
        app.route_agent_event("text_delta", {"text": "Hello", "text_id": "legacy"})
        await pilot.pause()

        # Auto-started streamer
        assert tv._streamer is not None
        assert tv._streamer.full_text == "Hello"


@pytest.mark.asyncio
async def test_text_delta_appends_to_3step_streamer():
    """text_delta after text.started appends to the existing streamer."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "Hello", "text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "world", "text_id": "t1"})
        await pilot.pause()

        assert tv._streamer is not None
        # Note: strip_thinking_tags() trims trailing whitespace, so don't
        # use leading-space chunks in tests.
        assert tv._streamer.full_text == "Helloworld"


@pytest.mark.asyncio
async def test_text_ended_finalizes_streamer_as_folder():
    """text.ended -> end_streaming_session -> _streamer cleared, folder added."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "Final answer", "text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text.ended", {"text_id": "t1", "text": "Final answer"})
        await pilot.pause()

        # Streamer was finalized
        assert tv._streamer is None
        assert tv._stream_baseline is None
        # Folder was appended
        assert len(tv._folders) == 1
        assert tv._folders[0].full_text == "Final answer"


@pytest.mark.asyncio
async def test_text_ended_without_active_streamer_is_noop():
    """Defensive: text.ended with no segment in progress does nothing."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        app.route_agent_event("text.ended", {"text_id": "t1", "text": "ignored"})
        await pilot.pause()

        assert tv._streamer is None
        assert len(tv._folders) == 0


@pytest.mark.asyncio
async def test_multi_segment_creates_multiple_folders():
    """Two text.started/text.ended segments -> two folders in transcript."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        # Segment 1
        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "First", "text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text.ended", {"text_id": "t1", "text": "First"})
        await pilot.pause()

        # Segment 2
        app.route_agent_event("text.started", {"text_id": "t2"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "Second", "text_id": "t2"})
        await pilot.pause()
        app.route_agent_event("text.ended", {"text_id": "t2", "text": "Second"})
        await pilot.pause()

        assert len(tv._folders) == 2
        assert tv._folders[0].full_text == "First"
        assert tv._folders[1].full_text == "Second"
        assert tv._streamer is None


@pytest.mark.asyncio
async def test_duplicate_text_started_finalizes_previous_segment():
    """Back-to-back text.started -> previous segment is auto-finalized."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        # First segment
        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "A", "text_id": "t1"})
        await pilot.pause()

        # Second text.started without text.ended -> auto-finalize
        app.route_agent_event("text.started", {"text_id": "t2"})
        await pilot.pause()

        # First segment is now a folder; second segment is active
        assert len(tv._folders) == 1
        assert tv._folders[0].full_text == "A"
        assert tv._streamer is not None
        assert tv._streamer.full_text == ""  # fresh streamer


# ─────────────────────────────────────────────────────────────────────────────
# assistant_message fallback
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assistant_message_legacy_path_writes_markdown():
    """No text.started -> assistant_message renders as Markdown (no fold)."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        # Simulate old backend: text_delta without text_id, then assistant_message
        app.route_agent_event("text_delta", {"text": "Hello", "text_id": "legacy"})
        await pilot.pause()

        app.route_agent_event("assistant_message", {"content": "Hello world"})
        await pilot.pause()
        await pilot.pause()  # let markdown render

        # Streamer cleared; content rendered as Markdown (no folder)
        assert tv._streamer is None
        # The Markdown body should appear in the rendered lines
        joined = " ".join(str(line) for line in tv.lines)
        assert "Hello world" in joined


@pytest.mark.asyncio
async def test_assistant_message_after_text_ended_does_not_double_write():
    """If text.ended already finalized, assistant_message is ignored."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        # 3-step path
        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "Final", "text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text.ended", {"text_id": "t1", "text": "Final"})
        await pilot.pause()

        # Snapshot line count right after text.ended
        snapshot_folders = len(tv._folders)

        # Now assistant_message fires (e.g. /goal /compact handler)
        app.route_agent_event("assistant_message", {"content": "DIFFERENT"})
        await pilot.pause()
        await pilot.pause()

        # No new content added — text.ended already wrote the folder
        assert len(tv._folders) == snapshot_folders
        # The "DIFFERENT" string should NOT be in the transcript
        joined = " ".join(str(line) for line in tv.lines)
        assert "DIFFERENT" not in joined


@pytest.mark.asyncio
async def test_assistant_message_after_text_delta_without_ended_clears_streamer():
    """assistant_message with active streamer but no text.ended -> finalize + write."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        # 3-step started + delta, but no ended
        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "Preview", "text_id": "t1"})
        await pilot.pause()

        # assistant_message fires (backend fell back before sending text.ended)
        app.route_agent_event("assistant_message", {"content": "Final body"})
        await pilot.pause()
        await pilot.pause()

        # Streamer cleared (preview replaced)
        assert tv._streamer is None
        # Content rendered as Markdown
        joined = " ".join(str(line) for line in tv.lines)
        assert "Final body" in joined
        # The "Preview" text may or may not appear (overwritten by Markdown),
        # but the final body must be present.


# ─────────────────────────────────────────────────────────────────────────────
# Tool events still work
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_emits_during_streaming():
    """tool_call arriving between text.started and text.ended is handled."""
    app = ResearchApp(**RESEARCHAPP_KW)
    async with app.run_test() as pilot:
        await pilot.pause()
        tv = app.query_one(TranscriptView)

        app.route_agent_event("text.started", {"text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("text_delta", {"text": "Calling tool", "text_id": "t1"})
        await pilot.pause()
        app.route_agent_event("tool_call", {
            "id": "tc1",
            "name": "lookup",
            "arguments": '{"q": "x"}',
        })
        await pilot.pause()
        app.route_agent_event("tool_result", {"id": "tc1", "status": "done", "elapsed_ms": 100})
        await pilot.pause()
        app.route_agent_event("text.ended", {"text_id": "t1", "text": "Calling tool"})
        await pilot.pause()

        # Streamer finalized
        assert tv._streamer is None
        assert len(tv._folders) == 1
        assert tv._folders[0].full_text == "Calling tool"
        # Tool call line was appended
        assert len(tv._tool_lines) == 0  # cleared after result
