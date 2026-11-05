# Context Compaction Design

> **Note (Phase A, 2026-07-31)**: This doc describes the legacy
> 3-layer system (L1 + L4 + L3). Phase A simplified to **L4-only**
> (opencode-aligned). L1 smart microcompact and L3 hard truncate
> have been removed. See `compaction-phase-a-simplification.md`
> for the current design.

## Overview

Context compaction (compact) is the system for compressing conversation history when token usage approaches the model's context limit. The system uses a 3-layer progressive compression strategy with configurable parameters, inspired by [opencode](https://github.com/anomalyco/opencode)'s compaction architecture.

## Architecture

### Compression Layers

```
Token Usage
│
├── 50% ──── L1: Smart Microcompact
│            Truncate oversized tool outputs (per-tool thresholds)
│
├── 80% ──── L4: LLM Summarize
│            Structured Markdown summary via LLM call
│            Token budget selection (preserve_recent_tokens + tail_turns)
│            Incremental summary update support
│
├── 90% ──── L3: Hard Truncate
│            Keep only system + last N messages (safety net)
│
└── 95% ──── Overflow Detection
             Force compression even below normal thresholds
```

### Layer Details

#### L1: Smart Microcompact (50%)

**Purpose**: Truncate oversized tool outputs that consume context without proportional value.

**Strategy**:
- Per-tool-type thresholds (not fixed 500 chars)
- Keep head 60% + tail 40% (tail often contains results)
- Skip error messages (preserve for debugging)
- Skip recent N tool outputs (protected by `collapse_keep_recent`)

**Default thresholds**:
| Tool | Limit |
|------|-------|
| `read_file` | 3000 chars |
| `search_code` | 2000 chars |
| `run_command` | 2000 chars |
| `backtest_run` | 4000 chars |
| `list_files` | 1000 chars |
| `_default` | 2000 chars |

**Why keep tail?** Many tool outputs have important results at the end (e.g., JSON `total_return` field, file path lists).

#### L4: LLM Summarize (80%)

**Purpose**: High-quality compression using LLM to generate a structured summary.

**Inspired by opencode's compaction architecture**:

1. **Token budget selection** (not fixed message count):
   - `tail_turns`: Keep last N turns verbatim (default: 2)
   - `preserve_recent_tokens`: Token budget for recent messages (default: dynamic 25% of context)
   - Head = old messages → LLM summarizes
   - Recent = budget-filled messages → keep verbatim

2. **Structured Markdown template**:
   ```markdown
   ## Objective
   - [what the user is trying to accomplish]

   ## Important Details
   - [decisions, constraints, key facts]

   ## Work State
   ### Completed
   ### Active
   ### Blocked

   ## Next Move
   1. [immediate next action]

   ## Relevant Files
   - [file paths and why they matter]
   ```

3. **Incremental summary update**:
   - When `previous_summary` exists, LLM updates it rather than regenerating
   - Preserves still-true details, removes stale ones, merges new facts

**Why structured template?**
- Maintains coherence: "Objective" + "Next Move" ensure LLM knows what to do
- Maintains traceability: "Relevant Files" preserves path information
- Maintains workflow: "Work State" distinguishes completed/in-progress/blocked

#### L3: Hard Truncate (90%)

**Purpose**: Safety net when L4 fails or isn't enough.

**Behavior**: Keep system messages + last `collapse_keep_recent` (default: 4) non-system messages. Discard everything else.

#### Overflow Detection (95%)

**Purpose**: Force compression even when normal thresholds haven't been reached.

**Trigger**: When `model_context_tokens` is known and total tokens >= 95% of usable context.

## Configuration

### CompactConfig

All parameters are configurable via `~/.quantnodes/llm.json`:

```json
{
  "compact": {
    "enabled": true,
    "microcompact_ratio": 0.5,
    "llm_summarize_ratio": 0.8,
    "hard_truncate_ratio": 0.9,
    "overflow_ratio": 0.95,
    "microcompact_tool_result_limit": 2000,
    "tool_truncate_limits": {
      "read_file": 3000,
      "backtest_run": 4000
    },
    "collapse_keep_recent": 4,
    "preserve_recent_tokens": 6000,
    "tail_turns": 2,
    "summary_output_tokens": 4096,
    "enable_incremental_summary": true
  }
}
```

### Parameter Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | `true` | Enable/disable compaction |
| `microcompact_ratio` | `0.5` | L1 threshold (50% of budget) |
| `llm_summarize_ratio` | `0.8` | L4 threshold (80% of budget) |
| `hard_truncate_ratio` | `0.9` | L3 threshold (90% of budget) |
| `overflow_ratio` | `0.95` | Overflow detection threshold |
| `microcompact_tool_result_limit` | `2000` | Default tool output truncation limit |
| `tool_truncate_limits` | per-tool dict | Per-tool-type truncation limits |
| `collapse_keep_recent` | `4` | Protect last N messages/tool outputs |
| `preserve_recent_tokens` | `null` | Token budget for recent messages (null = dynamic) |
| `tail_turns` | `2` | Keep last N turns verbatim in L4 |
| `summary_output_tokens` | `4096` | Max tokens for LLM summary output |
| `enable_incremental_summary` | `true` | Update previous summary vs regenerate |
| `summary_template` | `null` | Custom summary template (null = default) |

## `/compact` Command

Users can manually trigger compaction by typing `/compact` in the chat input.

### Flow

```
User types "/compact"
  → POST /api/chat/send_async {content: "/compact"}
    → chat.py: _handle_compact_command(body)
      → SessionService.compact_history(session_id, config)
        → store.get_messages()
        → compact_messages(messages, config)
          → L1: smart microcompact
          → L4: LLM structured summary
          → L3: hard truncate (safety net)
        → store.replace_messages()
      → Persist assistant message "✅ 上下文已压缩..."
      → EventBus: message_received → text_delta → agent_done
    → Return SendMessageResponse
  → Frontend SSE:
    → message_received → create placeholders
    → text_delta → stream result
    → agent_done → clear streaming
    → compact → update Agent.compaction_count + CompactBanner
```

### DB Operations

- `delete_messages(session_id, message_ids)`: Remove old messages
- `update_message_content(message_id, content, parts)`: Update compressed summary

## Frontend

### SSE Events

The `compact` event is emitted by the backend with:
```json
{
  "agent_id": "...",
  "layer": "llm_summarize(...)",
  "iteration": 1,
  "summary": "Context compression: llm_summarize(...)"
}
```

### Agent Store

Each `Agent` has:
- `compaction_count: number` — incremented on each compact event
- `last_compaction?: { layer: string; timestamp: number }` — most recent compaction info

### CompactBanner

A notification banner at the top of the message list:
- Shows "✅ 上下文已压缩: {layer}"
- Auto-dismisses after 5 seconds
- Uses `useChatStore.lastCompaction`

## Design Decisions

### Why delete L2 (Context Collapse)?

The original L2 (70% threshold) performed string-based compression with 100-char previews. This overlapped with L4 (LLM summarize) but at much lower quality. Removing L2 simplifies the architecture:

- **L1**: Fast, lossless, targeted at tool output noise
- **L4**: High-quality, lossy, global compression via LLM
- **L3**: Safety net

### Why head+tail truncation for L1?

Many tool outputs have important results at the end (JSON fields, file paths, metrics). Keeping head60% + tail 40% preserves these results while still reducing size.

### Why per-tool thresholds?

Different tools produce different output sizes and importance levels. A `backtest_run` result (4000 chars) is more valuable than a `list_files` output (1000 chars).

### Why incremental summary?

When a previous summary exists, updating it is more efficient than regenerating from scratch. The LLM preserves still-true details and merges new facts, producing a higher-quality summary with less token usage.

### Why token budget selection (not fixed count)?

Fixed message count (e.g., "keep last 4") doesn't account for message sizes. Token budget selection dynamically determines how many recent messages to keep based on actual token usage, which is more precise.
