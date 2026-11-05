# Text/Tool Routing via per-iteration text_id (opencode-style)

> Breaking change. See [PR1 commit](../) for the migration.

## Problem

The legacy `text_delta` SSE handler in `webui/frontend/src/hooks/useSSE.ts` used
`msg.parts.find((p) => p.type === 'text')` to locate the message's text part.
`find()` returns the **first** text part, so when an LLM iteration produced

```
text_delta(T1) → tool_call(1) → tool_call(2) → tool_result ×2
text_delta(T2) → tool_call(3) → tool_call(4) → tool_result ×2
text_delta(T3)
```

all subsequent deltas were appended to the first text part, yielding

```
parts = [text(T1+T2+T3 ...), tool_call(1), tool_call(2), tool_call(3), tool_call(4)]
```

The chat view then rendered all text stacked at the top and all tool calls
grouped at the bottom — exactly the symptom reported by the user.

After a page refresh, the order was correct (backend persisted the same
`text` → `tool_call` ordering into `parts_json`), confirming the bug was
purely in the live-stream accumulation path.

### Root cause

`text_delta` events had no identifier tying them to a specific text segment.
The frontend had to guess which text part to append to, and the guess
("always the first text part") was wrong whenever the LLM produced more
than one text segment separated by tool calls.

## opencode's solution

Opencode's `session.next.text.*` events use a 3-step lifecycle keyed by a
per-segment `textID`:

```
text.started({ textID })    → push new text part with id=textID
text.delta({ textID, delta }) → findLast by id, append
text.ended({ textID, text }) → findLast by id, override final
```

The LLM stream itself emits `text-start` events with **content block IDs**;
each LLM content block spawns a new text part. The frontend never has to
guess because every chunk carries the segment ID.

Reference:
- `core/src/session/message-updater.ts:230` — `text.started` pushes
- `core/src/session/message-updater.ts:237` — `text.delta` uses `latestText`
- `core/src/session/message-updater.ts:89` — `latestText` uses `findLast` by id

## Our protocol (PR1)

Because our LLM layer doesn't expose content blocks, we treat **each call to
`AgentLoop._stream_chat` (one LLM iteration)** as a single text segment and
assign each call a fresh `text_id = uuid4()`.

### Event sequence per LLM iteration

```
text.started  { text_id: U1 }
text_delta    { text_id: U1, text: "Hello" }
text_delta    { text_id: U1, text: " world" }
text.ended    { text_id: U1, text: "Hello world" }
```

If the LLM returns tool_calls, the iteration ends without a final text
segment (because `text.ended` carries the accumulated text, possibly empty).
Multi-iteration flows look like:

```
[iter 1]
text.started { text_id: U1 }
text_delta   { text_id: U1, text: "T1a" }
text_delta   { text_id: U1, text: "T1b" }
text.ended   { text_id: U1, text: "T1aT1b" }
tool_call    { id: "call_a", name: "foo", ... }
tool_call    { id: "call_b", name: "bar", ... }
tool_result  { id: "call_a", ... }
tool_result  { id: "call_b", ... }
[iter 2]
text.started { text_id: U2 }
text_delta   { text_id: U2, text: "T2" }
text.ended   { text_id: U2, text: "T2" }
```

Persisted `parts_json`:

```json
[
  { "type": "text", "id": "U1", "text": "T1aT1b" },
  { "type": "tool_call", "id": "call_a", "name": "foo", ... },
  { "type": "tool_call", "id": "call_b", "name": "bar", ... },
  { "type": "text", "id": "U2", "text": "T2" }
]
```

## Breaking changes

| What changed | Before | After |
|--------------|--------|-------|
| `text_delta` event | `{ text }` | `{ text, text_id }` — `text_id` **required** |
| `text.started` event | n/a | NEW: `{ text_id }` |
| `text.ended` event | n/a | NEW: `{ text_id, text }` |
| `TextPart` schema | `{ type, text }` | `{ type, id, text }` — `id` **required** |
| `parts_json` persistence | mixed (some parts with id, some without) | all text parts have id |
| Frontend behavior on `text_delta` without `text_id` | warn-and-drop (legacy) | **hard-break: drop and warn** |
| `assistant_message` event | unchanged | unchanged (kept for TUI compatibility) |

## Migration strategy

### DB migration (idempotent, runs on startup)

`_migrate_text_part_ids(conn)` runs at the end of `_ensure_schema()` in
`api/routers/web_session.py`. It scans every `messages.parts_json` row and
assigns a deterministic id to any text part missing one:

```python
f"legacy-{msg_id}-{idx}"
```

Idempotency: after the first run, every text part has an id, so subsequent
runs are no-ops. The migration is a single `SELECT ... UPDATE` pass; for
large DBs (~10k messages) it completes in a few seconds.

### Client migration

- **Frontend**: `TextPart.id` made required → underlying `parts_json` will
  already have ids (from stream events or DB migration). No data loss.
- **TUI**: still consumes `assistant_message` (kept for PR1 compatibility).
  Migrates to native streaming in PR2.

### Compatibility matrix

| State | Live stream | DB reload | TUI |
|-------|-------------|-----------|-----|
| Before PR1 | broken (text stacked at top) | OK (correct parts order) | OK |
| After PR1 | **OK** (text_id routing) | OK (idempotent migration) | OK (assistant_message) |
| After PR2 | OK | OK | **OK** (native streaming) |

## Alternative considered

**Tail-append fallback** (use `parts[parts.length - 1]` instead of `find`):
would fix the live-stream bug in 1 line but leaves the protocol ambiguous —
clients have no way to distinguish "the previous text segment ended" from
"a new text segment started". The 3-step protocol makes the boundary
explicit, which is what opencode's design optimizes for.

## Risk assessment

| Risk | Mitigation |
|------|------------|
| TUI silently breaks between PR1 deploy and PR2 merge | `assistant_message` event preserved verbatim |
| Migration corrupt `parts_json` | Migration only writes rows where change is needed; failure logged + skipped |
| Frontend drops legitimate `text_delta` (no text_id) | Logged as warning; only happens if backend regresses |
| `text_delta` ordering vs `text.ended` race | `text.ended` wins (override) — same as opencode |

## Files changed

### Backend
- `src/strategy_research/core/agent/loop.py` — `_stream_chat` / `_astream_chat` 3-step protocol
- `src/strategy_research/api/session/service.py` — `_accumulate_part` rewrite
- `src/strategy_research/api/routers/chat.py` — `_run_agent_loop_background` sync
- `src/strategy_research/api/routers/web_session.py` — `_migrate_text_part_ids` + caller

### Frontend
- `webui/frontend/src/stores/chat.ts` — `TextPart.id` required
- `webui/frontend/src/hooks/useSSE.ts` — `text.started` / `text.ended` handlers + `text_delta` id-based routing

### Tests
- `tests/test_text_part_routing.py` — 6 new tests
- `tests/test_db_migration.py` — 3 new tests
- `webui/frontend/src/test/useSSE.test.ts` — 6 new tests + `seedMessage` updated

## References

- opencode `text.started` reference: `packages/core/src/session/message-updater.ts:230`
- opencode `latestText` helper: `packages/core/src/session/message-updater.ts:89`
- opencode `text.started` event schema: `packages/schema/src/session-event.ts:197`
- opencode `publish-llm-event.ts:239` — event emission point
