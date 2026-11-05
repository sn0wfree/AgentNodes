# TUI Native Streaming via text.started/delta/ended

> Follow-up to PR1 (text-part-routing). See [docs/archive/undated/text-part-routing.md](text-part-routing.md).

## Context

PR1 introduced an opencode-style 3-step text protocol:

```
text.started  { text_id }          → push text part
text_delta    { text_id, text }    → findLast by id, append
text.ended    { text_id, text }    → findLast by id, override final
```

It kept `assistant_message` as a one-shot fallback for any consumer that
hadn't migrated. The TUI was that consumer.

## Before PR2

`route_agent_event` handled `text_delta` (incremental preview) and
`assistant_message` (final-formatted markdown). The mismatch between
streaming preview (plain text) and final render (Markdown with headers
/ bold / code blocks) caused a visible "jut" when `assistant_message`
fired: the streaming buffer was deleted and replaced with the
formatted Markdown.

This also meant the TUI never saw the actual segment boundary — text
streamed from iteration 2 was appended to the same buffer as iteration 1,
which is the same ordering bug the web frontend had, only less visible
because the TUI renders the whole turn as one block once streaming ends.

## What PR2 does

### Subscribes to the 3-step protocol

```
text.started  → begin_streaming()  (start a fresh streamer)
text_delta    → update_streaming_delta(text)  (append to active streamer)
text.ended    → end_streaming()  (finalize & keep open as folder)
```

The TUI now knows the segment boundary. After `text.ended`, the streamed
text remains as a folder (Ctrl+E to expand); if the next iteration
emits another `text.started`, a new streamer is started and the cycle
repeats.

### Keeps `assistant_message` as a fallback

During the transition window (old backend, partial rollout), the TUI
still renders `assistant_message` as the terminal event — replacing
any active streaming preview with the formatted Markdown. This is the
same behavior as before, kept for backward compatibility.

### Multi-segment routing

`StreamingText` already supports a single-segment buffer; for multi-segment
turns we route each segment to its own folder in the transcript. The
`_streamer` field is now per-segment; on `text.started` we begin a new
streaming session, on `text.ended` we fold it (keep visible) and reset
the streamer state.

### Dedup safety

The TUI is permitted to see both `text_delta`/`text.ended` (3-step
stream) AND `assistant_message` (one-shot) for the same content. To
prevent double-write, `assistant_message` only writes if the streamer
wasn't already finalized (i.e. the backend didn't send `text.ended`).
Defensive: if `text.ended` already arrived, `assistant_message` is a
no-op.

## Architecture

```
AgentLoop  ── text.started ──▶  ChatApp.route_agent_event
                                │
                                ├─▶ transcript.begin_streaming()
                                │
             ── text_delta ──▶  │
                                ├─▶ transcript.update_streaming_delta(text)
                                │      (strip thinking tags)
                                │
             ── text.ended ──▶  │
                                ├─▶ transcript.end_streaming()
                                │      (finalize folder, keep visible)
                                │
             ── assistant_message (fallback) ─▶
                                │
                                └─▶ transcript.write_assistant_message(content)
                                     (replace streaming preview with Markdown)
```

## Files changed

- `src/agentnodes/cli/tui/app.py` — `route_agent_event` adds 3 branches
- `src/agentnodes/cli/tui/widgets/transcript.py` — no change to public API
  (existing `begin_streaming` / `update_streaming` / `end_streaming` already sufficient)
- `tests/test_tui_text_routing.py` — new test file

## Risk assessment

| Risk | Mitigation |
|------|------------|
| Duplicate text shown when both 3-step and assistant_message fire | `assistant_message` only writes if no segment has ended yet |
| Old backend (no text.started) breaks TUI | `route_agent_event` falls back to the old text_delta → assistant_message path when no `text.started` arrives |
| Stripping thinking tags inconsistent between streaming and final | Same `text_filters.strip_thinking_tags` used in both paths |

## References

- opencode `text.ended` reference: `packages/core/src/session/message-updater.ts:243`
- opencode `text.started` reference: `packages/core/src/session/message-updater.ts:230`
- TUI existing `begin_streaming` / `end_streaming`:
  `src/agentnodes/cli/tui/widgets/transcript.py:371-422`
