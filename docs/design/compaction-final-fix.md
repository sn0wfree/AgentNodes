# L4 Compaction: opencode-aligned + Bug Fix

> **Note (Phase A, 2026-07-31)**: This doc describes the L4 design
> introduced alongside the 3-layer system. Phase A simplified to
> L4-only (L1 and L3 removed). See
> `compaction-phase-a-simplification.md` for the current design.

## Background

Session `700dc7f7-95de-45e0-b568-d713fe05065f` triggered L4 compaction
hundreds of times in a single session. The model has 1M context window
but `threshold_tokens=8000` (hardcoded default) caused L4 to fire at
~6400 tokens. Every compaction ran through the L4 path, which had a
latent bug: `self.cc.preserve_recent_tokens // 100` crashed with
`TypeError: unsupported operand type(s) for //: 'NoneType' and 'int'`
because `preserve_recent_tokens` defaults to `None` (dynamic mode).

Result: silent failure in `_persist_compaction_event`, LLM lost
conversation history on the next turn, session appeared "disconnected".

## Opencode Reference

Opencode's compaction in `packages/core/src/session/compaction.ts`:

```ts
// line 12-15: constants
const DEFAULT_BUFFER = 20_000
const DEFAULT_KEEP_TOKENS = 8_000
const TOOL_OUTPUT_MAX_CHARS = 2_000
const SUMMARY_OUTPUT_TOKENS = 4_096

// line 183: summary max_tokens formula
const summaryOutput = Math.min(output || SUMMARY_OUTPUT_TOKENS, SUMMARY_OUTPUT_TOKENS)

// line 225-235: trigger logic
const compactIfNeeded = Effect.fn("SessionCompaction.compactIfNeeded")(function* (input: Input) {
  if (!config.auto) return false
  const context = input.model.route.defaults.limits?.context
  if (context === undefined || context <= 0) return false
  const output = input.request.generation?.maxTokens ?? input.model.route.defaults.limits?.output ?? 0
  if (
    estimate({ system: input.request.system, messages: input.request.messages, tools: input.request.tools }) <=
    context - Math.max(output, config.buffer)
  )
    return false
  return yield* compactAfterOverflow(input)
})
```

Key insight: opencode's trigger is **derived from model context**:
`trigger = context - max(output, buffer)`, not an absolute number.

## Changes

### 1. Fix current bug (`// 100` NoneType crash)

**Root cause**: `_persist_compaction_event` in `loop.py` recomputed
`recent_count = self.cc.preserve_recent_tokens // 100`, but
`preserve_recent_tokens` defaults to `None` (dynamic mode).

**Fix**: move recent serialization into the compact module itself.
The compact module already knows what "recent" means (it ran
`_select_by_token_budget`); the loop was duplicating work in the wrong
place.

`compact_messages` now returns a 4-tuple:
`(messages, applied, summary_text, recent_text)`

The loop receives `recent_text` pre-serialized and just passes it
through to `persist_message`. No more slicing, no more `// 100`,
no more silent failure.

### 2. opencode-aligned trigger formula

Old: `threshold_tokens=8000` (hardcoded absolute).
New: `threshold_tokens: int | None = None`, derived from model
context when None:

```python
def _resolve_threshold_tokens(config, model_context, model_max_output) -> int:
    if config.threshold_tokens is not None:
        return config.threshold_tokens
    if model_context and model_context > 0:
        buffer = config.compaction_buffer_tokens  # 20_000
        output = model_max_output or buffer
        return max(8000, model_context - max(output, buffer))
    return 8000
```

**Effect** (MiniMax-M3: 1M context, 128K output):
- Old: trigger = 8000, L4 fires at 6,400 tokens (extreme over-compaction)
- New: trigger = 872,000, L4 fires at 828,400 (129x later)

### 3. opencode-aligned summary_output_tokens

Old: `max_tokens = config.summary_output_tokens` (fixed 4096).
New: `max_tokens = min(model_max_output, config.summary_output_tokens)`
(capped at 4096 unless model allows more).

### 4. Field rename: `microcompact_tool_result_limit` → `_chars`

Opencode uses `TOOL_OUTPUT_MAX_CHARS = 2_000` (chars, not tokens).
The old name was misleading. New name makes the unit explicit.

**Breaking change**: no compat for old field name. If user has
`microcompact_tool_result_limit` in llm.json, it will fail to load.

### 5. New ratios (user-specified)

| Parameter | Old | New | Notes |
|-----------|-----|-----|-------|
| `microcompact_ratio` | 0.5 | 0.9 | L1: truncate tool outputs |
| `llm_summarize_ratio` | 0.8 | 0.95 | L4: LLM-driven summary |
| `hard_truncate_ratio` | 0.9 | 0.99 | L3: drop oldest messages |
| `overflow_ratio` | 0.95 | 0.99 | overflow detection |

With the new threshold derivation, these ratios now apply at sensible
context percentages (e.g., L4 at 95% of 1M = 950K, not 95% of 8K = 7.6K).

### 6. New `compaction_buffer_tokens`

opencode has `DEFAULT_BUFFER = 20_000`. We add the same:
- `compaction_buffer_tokens: int = 20000`

Used in trigger formula: `context - max(output, buffer)`.

### 7. All hardcoded values now in `CompactConfig`

`CompactConfig` now exposes every value that affects L4 behavior.
No more magic numbers in the code. LLMConfig parses them from
llm.json's "compact" section.

### 8. CLI: `compact show`

`quantnodes-research compact show` prints every effective value,
including derived ones (threshold, buffer). Lets users see exactly
what's in effect without reading code.

### 9. TUI fixes

`cli/tui/session.py:262` was not passing `compact_config` to
`AgentLoop`. Fixed.

### 10. Error propagation

`_persist_compaction_event` now propagates critical errors (no
silent `try/except: warning`). If L4 fails, the loop rolls back to
the original messages (LLM doesn't lose context).

## Files Changed

### New
- `tests/test_compact_persistence.py` (5 tests)
- `tests/test_compact_defaults.py` (8 tests)
- `tests/test_loop_threshold.py` (4 tests)
- `tests/test_compact_error_propagation.py` (3 tests)
- `src/agentnodes/cli/commands/compact_show.py`
- `docs/design/compaction-final-fix.md` (this file)

### Modified
- `src/agentnodes/core/agent/compact.py` (4-tuple return,
  opencode formulas, new defaults, `_chars` rename)
- `src/agentnodes/core/agent/loop.py` (`_persist_compaction_event`
  signature, `model_max_output_tokens` plumbing, error propagation)
- `src/agentnodes/core/llm/config.py` (load new fields)
- `src/agentnodes/cli/tui/session.py` (pass `compact_config`)
- `tests/test_compact_config.py` (update tuple unpacking)

## Backward Compatibility

- `threshold_tokens=8000` (explicit) still works
- `compact_config` not passed by caller → still uses `CompactConfig()`
  defaults
- Field rename `microcompact_tool_result_limit` → `_chars` is
  **breaking**: old llm.json entries will fail to load
- No compat shim added per user direction

## Verification

1. Apply all changes, run 437+ tests → expect 480+ all pass
2. Live DB: trigger L4 manually, verify CompactionMessage persists
3. CLI: `quantnodes-research compact show` shows all values
4. Regression: session 700dc7f7-95d, L4 no longer crashes
