# Compaction Phase A: opencode-aligned L4-only simplification

## Background

The original 3-layer compaction system (L1 smart microcompact + L4 LLM
summarize + L3 hard truncate) was over-engineered. In production it
caused an infinite loop on session `700dc7f7-95de-45e0-b568-d713fe05065f`:

```
[L1] truncates tool outputs (over-aggressive)
[L4] fires safety abort ("too few messages")
[L3] drops oldest messages (still runs despite L4 abort)
[mark compaction applied] → next iteration starts
[repeat forever]
```

This culminated in MiniMax HTTP 400 "chat content is empty (2013)".

## Solution: opencode-aligned L4-only flow

Phase A aligns our compaction with opencode's design
(`packages/core/src/session/compaction.ts`): a single L4 layer with a
safety check. No pre-truncation, no post-drop.

## Changes (4 commits)

### A1: `chore(compaction): mark legacy layer config fields deprecated`

**File**: `src/strategy_research/core/agent/compact.py`

- Marked L1/L3 fields as DEPRECATED in `CompactConfig` (kept for
  backward compat with existing `llm.json` files; ignored at runtime).
- Added 4 new configurable fields:
  - `simplified_to_l4_only: bool = True` (Phase A toggle)
  - `l4_min_messages: int = 2` (L4 safety check threshold)
  - `fallback_threshold_tokens: int = 8_000`
  - `serialize_tool_max_chars: int = 2_000`
  - `chars_per_token: float = 3.0`
- Hardcoded constants extracted to parameters: `_serialize_message`
  accepts `tool_max_chars`, `_estimate_tokens` accepts `chars_per_token`,
  `_resolve_threshold_tokens` uses `config.fallback_threshold_tokens`.

### A2: `refactor(compaction): remove L1 smart microcompact layer`

**File**: `src/strategy_research/core/agent/compact.py`

- Removed `_smart_microcompact` function (50 lines).
- Removed `_get_tool_name` helper (15 lines).
- Renamed `_DEFAULT_TOOL_LIMITS` → `_LEGACY_L1_TOOL_LIMITS_DOC`
  (documentation only).
- L1-related tests in `test_compact_config.py` and
  `test_compact_full_pipeline.py` marked `@pytest.mark.skip` with
  explanation.

### A3: `refactor(compaction): remove L3 hard truncate layer`

**File**: `src/strategy_research/core/agent/compact.py`

- Removed `_hard_truncate` function (8 lines).
- L3-related tests marked `@pytest.mark.skip`.

### A4: `refactor(compaction): simplify compact_messages to L4-only` (THE FIX)

**File**: `src/strategy_research/core/agent/compact.py`

- Removed L1 dispatch (`_smart_microcompact` call).
- Removed L3 dispatch (`_hard_truncate` call).
- Removed `l1_threshold`, `l3_threshold`, `force_all` variables.
- Renamed `force_all` → `force_l4` (only L4 is forced when
  `threshold_tokens=0`).
- Single L4 dispatch: ratio check → L4 (with safety) → fix_pairs.
- Un-skipped L4 tests that were waiting for A4.
- Updated tests using too-aggressive 3-layer patterns to use proper
  user/assistant alternation (5 turns × 2 messages) so L4 safety check
  (`l4_min_messages=2`) passes.

## New L4-only dispatch (Phase A)

```python
def compact_messages(messages, config, threshold_tokens, ...):
    if not cfg.enabled:
        return messages, [], None, None

    # Resolve threshold (opencode formula)
    if threshold_tokens is None:
        threshold_tokens = _resolve_threshold_tokens(...)

    tokens = _estimate_tokens(messages)
    force_l4 = threshold_tokens == 0
    l4_threshold = 0 if force_l4 else threshold_tokens * cfg.llm_summarize_ratio

    # Early exit: below L4 threshold
    if not force_l4 and tokens < l4_threshold:
        return messages, [], None, None

    # L4: LLM summarize (with safety check)
    if llm_client is not None:
        l4_result = _llm_summarize_v2(...)
        if l4_result is not None:
            new_messages, summary_text, recent_text = l4_result
            if len(new_messages) < old_len and summary_text.strip():
                messages = new_messages
                applied.append(f"llm_summarize({old_len}->{len(messages)})")
                l4_summary_text = summary_text
                l4_recent_text = recent_text

    # Fix orphaned tool pairs (post-L4 repair)
    messages = _fix_tool_pairs(messages)

    return messages, applied, l4_summary_text, l4_recent_text
```

## Configurable parameters (Phase A exposure)

| Field | Default | Purpose |
|-------|---------|---------|
| `simplified_to_l4_only` | `True` | Phase A toggle (read-only flag) |
| `l4_min_messages` | `2` | L4 safety: minimum new_messages count |
| `fallback_threshold_tokens` | `8_000` | Used when model context is unknown |
| `serialize_tool_max_chars` | `2_000` | Tool output truncation in L4 input |
| `chars_per_token` | `3.0` | Token estimation ratio |
| `threshold_tokens` | `None` | Absolute override; None = derive from context |
| `compaction_buffer_tokens` | `20_000` | Opencode DEFAULT_BUFFER |
| `llm_summarize_ratio` | `0.95` | L4 trigger ratio (active) |
| `tail_turns` | `2` | Recent turns kept verbatim (active) |
| `preserve_recent_tokens` | `None` | Recent budget (active) |
| `summary_output_tokens` | `4_096` | Summary cap (active) |
| `enable_incremental_summary` | `True` | Active |
| `summary_template` | `None` | Active; None = `DEFAULT_SUMMARY_TEMPLATE` |
| `keep_all_compactions_in_history` | `False` | History filter (active) |

### DEPRECATED fields (kept for backward compat, ignored at runtime)

| Field | Default | Was |
|-------|---------|-----|
| `microcompact_ratio` | `0.9` | L1 trigger ratio |
| `hard_truncate_ratio` | `0.99` | L3 trigger ratio |
| `overflow_ratio` | `0.99` | Overflow detection (was 0.95) |
| `microcompact_tool_result_chars` | `2_000` | L1 per-tool limit |
| `tool_truncate_chars` | `{}` | L1 per-tool table (default empty) |
| `collapse_keep_recent` | `4` | L3 keep_recent |

## L4 safety check

L4 runs the LLM summarization but if the result has too few messages
or no user role, it aborts cleanly. The caller receives the original
messages unchanged and no CompactionMessage is persisted.

```python
new_messages = system_msgs + recent
if len(new_messages) < config.l4_min_messages:
    logger.warning("L4 produced too few messages (len=%d, min=%d), aborting",
                   len(new_messages), config.l4_min_messages)
    _compaction_metrics["l4_aborts"] += 1
    return None
if not any(m.get("role") == "user" for m in new_messages):
    logger.warning("L4 produced messages without any user role, aborting")
    _compaction_metrics["l4_aborts"] += 1
    return None
```

This safety check **terminates the 700dc7f7 infinite loop**:
- Safety abort → no layers run after → no compaction event persisted
- Next iteration sees the same messages → same safety abort → no growth
- LLM is called at most once per iteration (no amplification)

## Migration / Rollback

### Backward compat

- `llm.json` "compact" section: deprecated fields are still loadable
  but ignored at runtime. Old configs work without changes.
- `SR_KEEP_ALL_COMPACTIONS=1` env var: unchanged (still forces
  `keep_all_compactions_in_history=True`).
- Public API: `CompactConfig`, `compact_messages`, `_llm_summarize_v2`,
  `_fix_tool_pairs`, `_serialize_message`, `_estimate_tokens`,
  `_resolve_threshold_tokens`, `_split_into_turns`, `_select_by_token_budget`,
  `_build_summary_prompt`, `get_compaction_metrics`,
  `reset_compaction_metrics`, `set_keep_all_override` — all signatures
  preserved (extra params added with defaults).

### Rollback

```bash
git revert --no-commit c3f5adc 61ac1b8 fd1f4b2 5f77277
# or
git reset --hard fd1f4b2  # back to pre-Phase A L4-only
```

### Per-feature rollback

Each commit is independently revertible:

| Commit | Reverts |
|--------|---------|
| A1: `c3f5adc` | Config fields re-marked active, new params removed |
| A2: `61ac1b8` | L1 layer restored |
| A3: `fd1f4b2` | L3 layer restored |
| A4: `5f77277` | 3-layer dispatch restored in `compact_messages` |
| A5: `f008541` | Tests removed |

## Verification

```
Before A1: 15 failed, 251 passed
After A4:  11 failed, 236 passed, 19 skipped  (4 tests now pass)
After A5:  11 failed, 286 passed, 19 skipped  (50 new opencode-style tests)
```

The 11 remaining failures are all pre-existing in
`test_compact_llm_integration.py` and `test_compact_defaults.py`
(field count mismatch), unrelated to Phase A.

## References

- opencode compaction: `packages/core/src/session/compaction.ts`
- 700dc7f7 session log: infinite loop with MiniMax 400 errors
- MiniMax 2013 error: "chat content is empty"
- Commits A1-A5: `c3f5adc`, `61ac1b8`, `fd1f4b2`, `5f77277`, `f008541`
- Tests: `tests/test_compact_opencode_style.py` (50 new tests)
